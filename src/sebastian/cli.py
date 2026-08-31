from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .config import config_path, load_config, write_config
from .constants import DEFAULT_APP, DEFAULT_LOG_DIR, DEFAULT_PLIST, LABEL, SIGNATURE, sebastian_home
from .policy import contains_trigger, finalize_response, is_loop_response, strip_trigger
from .state import StateStore


def launch_domain() -> str:
    return f"gui/{os.getuid()}"


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args], capture_output=True, text=True, timeout=30, check=False
    )


def is_loaded() -> bool:
    return launchctl("print", f"{launch_domain()}/{LABEL}").returncode == 0


def wait_for_loaded(expected: bool, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_loaded() is expected:
            return True
        time.sleep(0.1)
    return is_loaded() is expected


def installed_probe(command: str, *values: str, timeout: int = 90) -> bool:
    launcher = DEFAULT_APP / "Contents" / "MacOS" / "Sebastian"
    if not launcher.exists():
        return False
    home = sebastian_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = uuid.uuid4().hex
    label = f"{LABEL}.diagnostic.{token}"
    plist_path = home / f"diagnostic-{token}.plist"
    output_path = home / f"diagnostic-{token}.out"
    error_path = home / f"diagnostic-{token}.err"
    result_path = home / f"diagnostic-{token}.result"
    payload = {
        "Label": label,
        "ProgramArguments": [str(launcher), command, *values],
        "RunAtLoad": True,
        # Diagnostics run in the logged-in GUI session so macOS can evaluate the
        # installed app's Keychain and Automation identity consistently.
        "ProcessType": "Interactive",
        "StandardOutPath": str(output_path),
        "StandardErrorPath": str(error_path),
        "EnvironmentVariables": {"SEBASTIAN_DIAGNOSTIC_RESULT": str(result_path)},
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle)
    os.chmod(plist_path, 0o600)
    loaded = False
    try:
        result = launchctl("bootstrap", launch_domain(), str(plist_path))
        if result.returncode != 0:
            return False
        loaded = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result_path.exists():
                value = result_path.read_text(encoding="utf-8").strip()
                return value == "0"
            time.sleep(0.25)
        return False
    finally:
        if loaded:
            launchctl("bootout", f"{launch_domain()}/{label}")
        for path in (plist_path, output_path, error_path, result_path):
            path.unlink(missing_ok=True)


def command_status(_args: argparse.Namespace) -> int:
    home = sebastian_home()
    config = load_config()
    disabled = (home / "DISABLED").exists() or not config["enabled"]
    loaded = is_loaded()
    print(f"service: {'loaded' if loaded else 'not loaded'}")
    print(f"monitoring: {'disabled' if disabled else 'enabled'}")
    print(f"model: {config['model']}")
    print(f"reasoning: {config['reasoning']['effort']}")
    image_config = config["images"]
    print(
        "images: "
        + (
            f"enabled (max {image_config['max_images']}, {image_config['detail']} detail)"
            if image_config["enabled"]
            else "disabled"
        )
    )
    print(f"config: {config_path()}")
    state_path = home / "state.sqlite3"
    if state_path.exists():
        state = StateStore(state_path)
        try:
            print(f"last seen Messages row: {state.cursor()}")
            print(f"triggers: {json.dumps(state.status_counts(), sort_keys=True)}")
            print(f"API calls today: {state.api_calls_today()}/{config['global_daily_api_call_limit']}")
        finally:
            state.close()
    else:
        print("state: not initialized")
    return 0 if loaded and not disabled else 1


def command_start(_args: argparse.Namespace) -> int:
    home = sebastian_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    disabled = home / "DISABLED"
    disabled.touch(mode=0o600, exist_ok=True)
    os.chmod(disabled, 0o600)
    if not DEFAULT_PLIST.exists():
        print(f"LaunchAgent is not installed: {DEFAULT_PLIST}", file=sys.stderr)
        return 1
    loaded_here = False
    if not is_loaded():
        result = launchctl("bootstrap", launch_domain(), str(DEFAULT_PLIST))
        if result.returncode != 0:
            print("Unable to load Sebastian LaunchAgent.", file=sys.stderr)
            return 1
        loaded_here = True
        if not wait_for_loaded(True):
            print("Sebastian LaunchAgent did not become ready.", file=sys.stderr)
            return 1
    disabled.unlink(missing_ok=True)
    result = launchctl("kickstart", "-k", f"{launch_domain()}/{LABEL}")
    if result.returncode != 0:
        disabled.touch(mode=0o600, exist_ok=True)
        os.chmod(disabled, 0o600)
        if loaded_here:
            launchctl("bootout", f"{launch_domain()}/{LABEL}")
        print("Unable to start Sebastian.", file=sys.stderr)
        return 1
    print("Sebastian started; monitoring is enabled.")
    return 0


def command_stop(_args: argparse.Namespace) -> int:
    home = sebastian_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    disabled = home / "DISABLED"
    disabled.touch(mode=0o600, exist_ok=True)
    os.chmod(disabled, 0o600)
    if is_loaded():
        result = launchctl("bootout", f"{launch_domain()}/{LABEL}")
        if result.returncode != 0 and is_loaded():
            print("Unable to unload Sebastian LaunchAgent.", file=sys.stderr)
            return 1
        if not wait_for_loaded(False):
            print("Sebastian LaunchAgent did not stop in time.", file=sys.stderr)
            return 1
    print("Sebastian stopped; the kill switch is active.")
    return 0


def command_restart(args: argparse.Namespace) -> int:
    if command_stop(args) != 0:
        return 1
    return command_start(args)


def command_logs(args: argparse.Namespace) -> int:
    path = DEFAULT_LOG_DIR / "service.log"
    if not path.exists():
        print("No Sebastian service log exists yet.")
        return 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-args.lines :]:
        print(line)
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    try:
        config = load_config()
        checks.append(("config", True, "valid"))
    except Exception as exc:
        checks.append(("config", False, type(exc).__name__))
        config = None
    checks.append(("launcher app", DEFAULT_APP.exists(), str(DEFAULT_APP)))
    checks.append(("LaunchAgent", DEFAULT_PLIST.exists(), str(DEFAULT_PLIST)))
    checks.append(("Keychain API key", installed_probe("doctor-keychain"), "installed app identity"))
    probe = "doctor-permissions" if args.permissions else "doctor-database"
    detail = "FDA + Automation" if args.permissions else "read-only via installed app identity"
    checks.append(("Messages permissions", installed_probe(probe), detail))
    if config and args.openai:
        checks.append(
            ("OpenAI connectivity", installed_probe("doctor-openai", timeout=120), "Responses API reachable")
        )
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return 0 if checks and all(item[1] for item in checks) else 1


def command_test(args: argparse.Namespace) -> int:
    if args.dry_run:
        variants = ["@Sebastian hello", "Hey, @sebastian!", "(@SEBASTIAN) status?"]
        if not all(contains_trigger(item) for item in variants):
            print("Dry-run trigger matcher failed.", file=sys.stderr)
            return 1
        if is_loop_response(f"done\n\n{SIGNATURE}") is False:
            print("Dry-run loop guard failed.", file=sys.stderr)
            return 1
        request = strip_trigger(variants[1])
        response = finalize_response(f"Dry run understood: {request}", 300)
        if response.count(SIGNATURE) != 1 or not response.endswith(SIGNATURE):
            print("Dry-run signature guard failed.", file=sys.stderr)
            return 1
        print(response)
        print("\nDry run passed. No API call or Messages send occurred.")
        return 0
    if args.live:
        state_path = sebastian_home() / "state.sqlite3"
        if not state_path.exists():
            print("State is not initialized. Start Sebastian first.", file=sys.stderr)
            return 1
        state = StateStore(state_path)
        try:
            baseline_id = state.max_trigger_id()
        finally:
            state.close()
        print("Send a message containing @sebastian in any direct or group conversation.")
        print("Waiting up to 5 minutes for one new trigger; no message content will be shown.")
        deadline = time.monotonic() + 300
        observed_id: int | None = None
        while time.monotonic() < deadline:
            time.sleep(2)
            state = StateStore(state_path)
            try:
                new_triggers = state.triggers_after_id(baseline_id)
            finally:
                state.close()
            if len(new_triggers) > 1:
                print("More than one new trigger appeared; rerun the test during a quiet window.", file=sys.stderr)
                return 1
            if new_triggers:
                observed_id = new_triggers[0].id
                break
        if observed_id is None:
            print("Timed out without observing a new trigger.", file=sys.stderr)
            return 1
        while time.monotonic() < deadline:
            time.sleep(2)
            state = StateStore(state_path)
            try:
                trigger = state.trigger_by_id(observed_id)
            finally:
                state.close()
            if trigger and trigger.status == "sent":
                break
            if trigger and trigger.status in {"ignored", "unavailable", "rate_limited", "daily_limited"}:
                print(f"Live trigger ended with status {trigger.status}.", file=sys.stderr)
                return 1
        else:
            print("Timed out before the new trigger completed.", file=sys.stderr)
            return 1
        if not installed_probe("verify-trigger", str(observed_id)):
            print("Live reply failed same-conversation/signature/uniqueness verification.", file=sys.stderr)
            return 1
        print("Initial live reply verified. Restarting to prove the trigger is not processed again...")
        if command_restart(args) != 0:
            return 1
        time.sleep(float(load_config()["poll_seconds"]) + 3)
        if not installed_probe("verify-trigger", str(observed_id)):
            print("Restart deduplication verification failed.", file=sys.stderr)
            return 1
        print("Live trigger, exact reply, same conversation, signature, and restart deduplication passed.")
        return 0
    print("Choose --dry-run or --live.", file=sys.stderr)
    return 2


def command_allowlist(args: argparse.Namespace) -> int:
    config = load_config()
    allowlist = config["allowlist"]
    if args.enable:
        allowlist["enabled"] = True
    if args.disable:
        allowlist["enabled"] = False
    if args.add_chat and args.add_chat not in allowlist["conversation_guids"]:
        allowlist["conversation_guids"].append(args.add_chat)
    if args.remove_chat:
        allowlist["conversation_guids"] = [
            item for item in allowlist["conversation_guids"] if item != args.remove_chat
        ]
    if args.add_participant and args.add_participant not in allowlist["participants"]:
        allowlist["participants"].append(args.add_participant)
    if args.remove_participant:
        allowlist["participants"] = [
            item for item in allowlist["participants"] if item != args.remove_participant
        ]
    if any((args.enable, args.disable, args.add_chat, args.remove_chat, args.add_participant, args.remove_participant)):
        write_config(config)
    print(f"enabled: {allowlist['enabled']}")
    print(f"conversation entries: {len(allowlist['conversation_guids'])}")
    print(f"participant entries: {len(allowlist['participants'])}")
    return 0


def command_config(args: argparse.Namespace) -> int:
    path = config_path()
    if args.edit:
        editor = os.environ.get("EDITOR", "/usr/bin/nano")
        return subprocess.run([editor, str(path)], check=False).returncode
    config = load_config()
    safe = dict(config)
    safe["allowlist"] = {
        "enabled": config["allowlist"]["enabled"],
        "conversation_entries": len(config["allowlist"]["conversation_guids"]),
        "participant_entries": len(config["allowlist"]["participants"]),
    }
    print(json.dumps(safe, indent=2))
    print(f"path: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sebastian", description="Operate the local Sebastian service.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in {
        "status": command_status,
        "start": command_start,
        "stop": command_stop,
        "restart": command_restart,
    }.items():
        child = sub.add_parser(name)
        child.set_defaults(handler=handler)
    logs = sub.add_parser("logs")
    logs.add_argument("--lines", type=int, default=80)
    logs.set_defaults(handler=command_logs)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--openai", action="store_true", help="Make a minimal paid API connectivity call.")
    doctor.add_argument("--permissions", action="store_true", help="Probe from the installed app identity.")
    doctor.set_defaults(handler=command_doctor)
    test = sub.add_parser("test")
    group = test.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--live", action="store_true")
    test.set_defaults(handler=command_test)
    allow = sub.add_parser("allowlist")
    allow.add_argument("--enable", action="store_true")
    allow.add_argument("--disable", action="store_true")
    allow.add_argument("--add-chat")
    allow.add_argument("--remove-chat")
    allow.add_argument("--add-participant")
    allow.add_argument("--remove-participant")
    allow.set_defaults(handler=command_allowlist)
    config = sub.add_parser("config")
    config.add_argument("--edit", action="store_true")
    config.set_defaults(handler=command_config)
    return parser


def main() -> int:
    os.umask(0o077)
    args = build_parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
