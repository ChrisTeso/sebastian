from __future__ import annotations

import argparse
import datetime as dt
import os
import secrets
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .codex_runner import CodexRunner, consequential_request
from .config import load_config
from .constants import DEFAULT_LOG_DIR, SIGNATURE, sebastian_home
from .generator import connectivity_test, generate_response
from .keychain import api_key_exists
from .logging_utils import configure_logging
from .messages import (
    chat_guid,
    connect_readonly,
    image_attachments,
    latest_rowid,
    outgoing_delivery_state,
    message_text_hash,
    message_by_id,
    message_guid_hash,
    message_is_fresh,
    messages_after,
    participants,
    previous_messages,
    signed_outgoing_rowids_after,
    validate_schema,
)
from .policy import agent_command, allowlisted, finalize_response, should_trigger, strip_trigger
from .sender import automation_probe, send_to_chat
from .state import StateStore, Trigger, parse_iso, utc_now


@dataclass(frozen=True)
class PendingApproval:
    request: str
    chat_guid: str
    expires_at: dt.datetime


class SebastianService:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_config()
        self.home = sebastian_home()
        self.disabled_path = self.home / "DISABLED"
        self.stop_requested = False
        self.logger = configure_logging(DEFAULT_LOG_DIR / "service.log")
        self.state = StateStore(self.home / "state.sqlite3")
        self.messages_path = Path(self.config["messages_db"]).expanduser()
        self.agent_lock = threading.Lock()
        self.agent_thread: threading.Thread | None = None
        self.agent_trigger_id: int | None = None
        self.pending_approvals: dict[str, PendingApproval] = {}
        self.agent_runner: CodexRunner | None = None
        if self.config.get("agent", {}).get("enabled", True):
            try:
                self.agent_runner = CodexRunner(self.config)
            except Exception as exc:
                self.logger.error(
                    "agent_unavailable error_type=%s", type(exc).__name__
                )

    def close(self) -> None:
        if self.agent_runner is not None:
            self.agent_runner.cancel()
        thread = self.agent_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self.state.close()
        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers.clear()

    def disabled(self) -> bool:
        return self.disabled_path.exists() or not self.config.get("enabled", True)

    def request_stop(self, *_args: object) -> None:
        self.stop_requested = True
        if self.agent_runner is not None:
            self.agent_runner.cancel()

    def initialize(self) -> None:
        with connect_readonly(self.messages_path) as conn:
            validate_schema(conn)
            latest = latest_rowid(conn)
        prior = self.state.get_meta("last_seen_rowid")
        self.state.initialize_cursor(latest)
        recovered = self.state.recover_interrupted_generation()
        interrupted_agents = self.state.recover_interrupted_agents()
        self.prune_if_due(force=True)
        if prior is None:
            self.logger.info("service_initialized cursor=%s", latest)
        if recovered:
            self.logger.warning("generation_recovered count=%s", recovered)
        if interrupted_agents:
            self.logger.warning("agent_jobs_interrupted count=%s", interrupted_agents)

    def discover(self) -> None:
        if self.disabled():
            return
        with connect_readonly(self.messages_path) as conn:
            batch = messages_after(conn, self.state.cursor())
            if not batch:
                return
            max_rowid = self.state.cursor()
            for message in batch:
                max_rowid = max(max_rowid, message.rowid)
                if not should_trigger(text=message.text, is_from_me=message.is_from_me):
                    continue
                command = agent_command(message.text)
                if command is not None:
                    if not message.is_from_me:
                        self.logger.info("agent_trigger_blocked_not_owner rowid=%s", message.rowid)
                        continue
                    if not self.config.get("agent", {}).get("enabled", True):
                        self.logger.info("agent_trigger_disabled rowid=%s", message.rowid)
                        continue
                    max_age = int(
                        self.config.get("agent", {}).get("max_message_age_seconds", 120)
                    )
                else:
                    max_age = int(self.config.get("max_trigger_age_seconds", 300))
                if not message_is_fresh(message, max_age):
                    self.logger.info("stale_trigger_ignored rowid=%s", message.rowid)
                    continue
                if not allowlisted(
                    self.config,
                    message.chat_guid,
                    message.sender,
                    is_from_me=message.is_from_me,
                ):
                    self.logger.info("trigger_blocked_by_allowlist rowid=%s", message.rowid)
                    continue
                result = self.state.discover_trigger(
                    chat_id=message.chat_id,
                    message_rowid=message.rowid,
                    message_guid_hash=(
                        message_guid_hash(message.guid) if message.guid else None
                    ),
                    per_conversation_limit=int(
                        self.config["max_triggers_per_conversation_per_minute"]
                    ),
                )
                self.logger.info("trigger_classified rowid=%s status=%s", message.rowid, result)
            self.state.advance_cursor(max_rowid)

    def prune_if_due(self, *, force: bool = False) -> None:
        now = utc_now()
        last_value = self.state.get_meta("last_prune_at")
        due = force or not last_value
        if last_value and not force:
            try:
                due = (now - parse_iso(last_value)).total_seconds() >= 86_400
            except ValueError:
                due = True
        if due:
            self.state.prune(int(self.config["state_retention_days"]), now)
            self.state.set_meta("last_prune_at", now.isoformat().replace("+00:00", "Z"))

    def _retry_delay(self, trigger: Trigger) -> int:
        settings = self.config.get("retry", {})
        initial = int(settings.get("initial_seconds", 15))
        maximum = int(settings.get("maximum_seconds", 900))
        return min(maximum, initial * (2 ** min(trigger.attempt_count, 6)))

    def reconcile_sends(self) -> None:
        if self.disabled():
            return
        grace = int(self.config.get("retry", {}).get("send_reconcile_seconds", 45))
        now = utc_now()
        with connect_readonly(self.messages_path) as conn:
            for trigger in self.state.sending_triggers():
                if not trigger.response_hash or not trigger.send_started_at:
                    self.state.schedule_retry(trigger.id, self._retry_delay(trigger), "invalid_send_state")
                    continue
                started = parse_iso(trigger.send_started_at)
                delivery_state, outgoing = outgoing_delivery_state(
                    conn,
                    chat_id=trigger.chat_id,
                    response_hash=trigger.response_hash,
                    since=started,
                )
                if delivery_state == "sent" and outgoing is not None:
                    self.state.mark_sent(trigger.id, outgoing)
                    self.logger.info("send_reconciled trigger=%s", trigger.id)
                elif delivery_state == "pending":
                    self.logger.info("send_still_pending trigger=%s", trigger.id)
                elif (now - started).total_seconds() >= grace:
                    self.state.schedule_retry(
                        trigger.id,
                        self._retry_delay(trigger),
                        "send_failed" if delivery_state == "failed" else "send_not_observed",
                    )
                    self.logger.warning("send_requeued trigger=%s", trigger.id)

    def _send_trigger_response(
        self,
        state: StateStore,
        trigger_id: int,
        target_chat_guid: str,
        body: str,
        *,
        retry_trigger: Trigger | None = None,
    ) -> None:
        response = finalize_response(body, int(self.config["max_response_chars"]))
        state.mark_sending(trigger_id, message_text_hash(response))
        if self.disabled() or self.stop_requested:
            if retry_trigger is not None:
                state.schedule_retry(
                    trigger_id, self._retry_delay(retry_trigger), "kill_switch"
                )
            else:
                state.mark_terminal(trigger_id, "agent_interrupted", "kill_switch")
            return
        try:
            send_to_chat(target_chat_guid, response)
        except Exception as exc:
            # The AppleEvent result is ambiguous. Keep 'sending' for DB reconciliation.
            self.logger.error(
                "send_ambiguous trigger=%s error_type=%s", trigger_id, type(exc).__name__
            )
            return
        self.logger.info("send_accepted trigger=%s", trigger_id)

    def _prune_pending_approvals(self) -> None:
        now = utc_now()
        expired = [
            token
            for token, pending in self.pending_approvals.items()
            if pending.expires_at <= now
        ]
        for token in expired:
            self.pending_approvals.pop(token, None)

    def _create_pending_approval(self, request: str, chat_guid_value: str) -> str:
        settings = self.config.get("agent", {})
        ttl = int(settings.get("approval_ttl_seconds", 600))
        with self.agent_lock:
            self._prune_pending_approvals()
            token = secrets.token_hex(3).upper()
            while token in self.pending_approvals:
                token = secrets.token_hex(3).upper()
            self.pending_approvals[token] = PendingApproval(
                request=request,
                chat_guid=chat_guid_value,
                expires_at=utc_now() + dt.timedelta(seconds=ttl),
            )
        return token

    def _agent_is_running(self) -> bool:
        thread = self.agent_thread
        return bool(thread and thread.is_alive())

    def _start_agent_job(
        self,
        trigger: Trigger,
        target_chat_guid: str,
        request: str,
        *,
        approved: bool,
    ) -> bool:
        if self.agent_runner is None:
            self._send_trigger_response(
                self.state,
                trigger.id,
                target_chat_guid,
                "Codex is unavailable on this Mac. Run sebastian doctor to inspect the setup.",
            )
            return False
        with self.agent_lock:
            if self._agent_is_running():
                self._send_trigger_response(
                    self.state,
                    trigger.id,
                    target_chat_guid,
                    "A Codex job is already running. Send @sebastian status or @sebastian cancel.",
                )
                return False
            self.state.mark_agent_running(trigger.id)
            self.agent_trigger_id = trigger.id
            thread = threading.Thread(
                target=self._agent_worker,
                args=(trigger.id, target_chat_guid, request, approved),
                name=f"sebastian-codex-{trigger.id}",
                daemon=True,
            )
            self.agent_thread = thread
            thread.start()
        self.logger.info("agent_job_started trigger=%s approved=%s", trigger.id, approved)
        return True

    def _agent_worker(
        self,
        trigger_id: int,
        target_chat_guid: str,
        request: str,
        approved: bool,
    ) -> None:
        assert self.agent_runner is not None
        result = self.agent_runner.run(request, approved=approved)
        if result.status == "completed":
            body = result.output
        elif result.status == "approval_required":
            token = self._create_pending_approval(request, target_chat_guid)
            body = (
                f"Approval required: {result.output}. "
                f"Reply @sebastian approve {token} before the code expires."
            )
        elif result.status == "cancelled":
            body = "The Codex job was cancelled."
        else:
            details = {
                "timeout": "The Codex job exceeded its time limit.",
                "codex_not_logged_in": "Codex is not logged in on this Mac.",
                "workspace_unavailable": "The configured Codex workspace is unavailable.",
                "codex_unavailable": "The Codex CLI is unavailable.",
            }
            body = details.get(
                result.error_code or "",
                "The Codex job failed without making a reliable completion report.",
            )
        state = StateStore(self.home / "state.sqlite3")
        try:
            if self.disabled() or self.stop_requested:
                state.mark_terminal(trigger_id, "agent_interrupted", "kill_switch")
            else:
                self._send_trigger_response(state, trigger_id, target_chat_guid, body)
        finally:
            state.close()
            with self.agent_lock:
                if self.agent_trigger_id == trigger_id:
                    self.agent_trigger_id = None
                    self.agent_thread = None
        self.logger.info(
            "agent_job_finished trigger=%s status=%s", trigger_id, result.status
        )

    def _process_agent_command(
        self,
        trigger: Trigger,
        target_chat_guid: str,
        command: tuple[str, str],
    ) -> None:
        action, value = command
        if action == "status":
            with self.agent_lock:
                self._prune_pending_approvals()
                running = self._agent_is_running()
                approvals = len(self.pending_approvals)
            if running:
                body = "A Codex job is running. Send @sebastian cancel to stop it."
            elif approvals:
                body = f"No Codex job is running. {approvals} approval request is pending."
            else:
                body = "No Codex job is running and no approval is pending."
            self._send_trigger_response(self.state, trigger.id, target_chat_guid, body)
            return
        if action == "cancel":
            cancelled = self.agent_runner.cancel() if self.agent_runner else False
            body = "Cancellation requested." if cancelled else "No Codex job is running."
            self._send_trigger_response(self.state, trigger.id, target_chat_guid, body)
            return
        if action == "approve":
            with self.agent_lock:
                self._prune_pending_approvals()
                pending = self.pending_approvals.get(value)
                if pending and pending.chat_guid == target_chat_guid and not self._agent_is_running():
                    self.pending_approvals.pop(value, None)
                else:
                    pending = None
            if pending is None:
                self._send_trigger_response(
                    self.state,
                    trigger.id,
                    target_chat_guid,
                    "That approval code is invalid, expired, belongs to another conversation, or a job is already running.",
                )
                return
            self._start_agent_job(
                trigger,
                target_chat_guid,
                pending.request,
                approved=True,
            )
            return
        if consequential_request(value):
            token = self._create_pending_approval(value, target_chat_guid)
            self._send_trigger_response(
                self.state,
                trigger.id,
                target_chat_guid,
                "This request can create an external or destructive side effect. "
                f"Reply @sebastian approve {token} before the code expires to proceed.",
            )
            return
        self._start_agent_job(
            trigger,
            target_chat_guid,
            value,
            approved=False,
        )

    def process_trigger(self, trigger: Trigger) -> None:
        if self.disabled():
            return
        with connect_readonly(self.messages_path) as conn:
            message = message_by_id(conn, trigger.chat_id, trigger.message_rowid)
            if message is None:
                self.state.mark_terminal(trigger.id, "unavailable", "source_missing")
                return
            if not should_trigger(text=message.text, is_from_me=message.is_from_me):
                self.state.mark_terminal(trigger.id, "ignored", "no_longer_trigger")
                return
            command = agent_command(message.text)
            target_chat_guid = chat_guid(conn, trigger.chat_id)
            if command is not None:
                if not message.is_from_me:
                    self.state.mark_terminal(trigger.id, "ignored", "agent_owner_required")
                    return
                self._process_agent_command(trigger, target_chat_guid, command)
                return
            chat_participants = participants(conn, trigger.chat_id)
            history = previous_messages(
                conn,
                trigger.chat_id,
                trigger.message_rowid,
                int(self.config["max_context_messages"]),
            )
            image_config = self.config.get("images", {})
            images = []
            if image_config.get("enabled", True):
                images = image_attachments(
                    conn,
                    chat_id=trigger.chat_id,
                    message_rowids=[item.rowid for item in [*history, message]],
                    max_images=int(image_config.get("max_images", 4)),
                    max_image_bytes=int(
                        image_config.get("max_image_bytes", 10_485_760)
                    ),
                    max_total_bytes=int(
                        image_config.get("max_total_bytes", 20_971_520)
                    ),
                )
        if not allowlisted(
            self.config,
            target_chat_guid,
            message.sender,
            is_from_me=message.is_from_me,
        ):
            self.state.mark_terminal(trigger.id, "ignored", "allowlist")
            return
        if self.disabled():
            return
        if not self.state.reserve_api_call(
            trigger.id, int(self.config["global_daily_api_call_limit"])
        ):
            self.logger.warning("daily_api_limit_reached trigger=%s", trigger.id)
            return
        request = strip_trigger(message.text) or "Please respond appropriately to this conversation."
        try:
            generated = generate_response(
                config=self.config,
                request=request,
                trigger=message,
                history=history,
                participants=chat_participants,
                images=images,
            )
            response = finalize_response(generated, int(self.config["max_response_chars"]))
        except Exception as exc:
            self.state.schedule_retry(trigger.id, self._retry_delay(trigger), "generation_failed")
            self.logger.error(
                "generation_failed trigger=%s error_type=%s", trigger.id, type(exc).__name__
            )
            return
        if self.disabled():
            self.state.schedule_retry(trigger.id, self._retry_delay(trigger), "kill_switch")
            return
        try:
            still_allowed = allowlisted(
                load_config(),
                target_chat_guid,
                message.sender,
                is_from_me=message.is_from_me,
            )
        except Exception:
            still_allowed = False
        if not still_allowed:
            self.state.mark_terminal(trigger.id, "ignored", "allowlist")
            return
        self._send_trigger_response(
            self.state,
            trigger.id,
            target_chat_guid,
            response,
            retry_trigger=trigger,
        )

    def run_once(self) -> None:
        if self.disabled():
            return
        self.prune_if_due()
        self.reconcile_sends()
        self.discover()
        for trigger in self.state.due_triggers():
            if self.disabled() or self.stop_requested:
                break
            self.process_trigger(trigger)

    def run(self, *, once: bool = False) -> int:
        self.initialize()
        self.logger.info("service_started")
        while not self.stop_requested:
            try:
                self.run_once()
            except Exception as exc:
                self.logger.error("poll_failed error_type=%s", type(exc).__name__)
            if once:
                break
            deadline = time.monotonic() + float(self.config["poll_seconds"])
            while not self.stop_requested and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.5, remaining))
        self.logger.info("service_stopped")
        return 0


def permission_probe() -> int:
    try:
        if database_probe() != 0:
            return 1
        accounts = automation_probe()
    except Exception as exc:
        print(f"Permission probe failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    if accounts < 1:
        print("Messages has no enabled account.", file=sys.stderr)
        return 1
    print("Sebastian can read Messages and sees an enabled Messages account.")
    return 0


def database_probe() -> int:
    try:
        config = load_config()
        with connect_readonly(Path(config["messages_db"]).expanduser()) as conn:
            validate_schema(conn)
            latest_rowid(conn)
    except Exception as exc:
        print(f"Messages database probe failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print("Sebastian has read-only access to the Messages database.")
    return 0


def keychain_probe() -> int:
    if not api_key_exists():
        print("Sebastian's Keychain API key is unavailable.", file=sys.stderr)
        return 1
    print("Sebastian can access its Keychain API key.")
    return 0


def openai_probe() -> int:
    try:
        if not connectivity_test(load_config()):
            raise RuntimeError("Unexpected connectivity response.")
    except Exception as exc:
        print(f"OpenAI connectivity probe failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print("Sebastian reached the OpenAI Responses API.")
    return 0


def codex_probe() -> int:
    try:
        config = load_config()
        probe_config = dict(config)
        probe_config["agent"] = {
            **config.get("agent", {}),
            "sandbox": "read-only",
            "timeout_seconds": 120,
            "reasoning_effort": "low",
        }
        result = CodexRunner(probe_config).run(
            "Do not use tools or modify anything. Reply with exactly CODEX_BRIDGE_OK."
        )
        if result.status != "completed" or result.output.strip() != "CODEX_BRIDGE_OK":
            raise RuntimeError(result.error_code or result.status)
    except Exception as exc:
        print(f"Codex bridge probe failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print("Sebastian reached Codex through the installed app identity.")
    return 0


def verify_trigger(trigger_id: int) -> int:
    state = StateStore(sebastian_home() / "state.sqlite3")
    try:
        trigger = state.trigger_by_id(trigger_id)
        if (
            trigger is None
            or trigger.status != "sent"
            or trigger.sent_message_rowid is None
        ):
            print("Trigger is not fully reconciled.", file=sys.stderr)
            return 1
        config = load_config()
        with connect_readonly(Path(config["messages_db"]).expanduser()) as conn:
            outgoing = message_by_id(conn, trigger.chat_id, trigger.sent_message_rowid)
            matches = signed_outgoing_rowids_after(
                conn,
                chat_id=trigger.chat_id,
                after_rowid=trigger.message_rowid,
                signature=SIGNATURE,
            )
        if (
            outgoing is None
            or not outgoing.is_from_me
            or not outgoing.text.endswith(SIGNATURE)
            or outgoing.text.count(SIGNATURE) != 1
            or matches != [trigger.sent_message_rowid]
        ):
            print("Trigger reply verification failed.", file=sys.stderr)
            return 1
    finally:
        state.close()
    print("Trigger has exactly one signed outgoing reply in the same conversation.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Sebastian background service.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "run",
            "doctor-database",
            "doctor-keychain",
            "doctor-openai",
            "doctor-codex",
            "doctor-permissions",
            "verify-trigger",
        ],
        default="run",
    )
    parser.add_argument("value", nargs="?")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    os.umask(0o077)
    args = parse_args()
    if args.command == "doctor-permissions":
        return permission_probe()
    if args.command == "doctor-database":
        return database_probe()
    if args.command == "doctor-keychain":
        return keychain_probe()
    if args.command == "doctor-openai":
        return openai_probe()
    if args.command == "doctor-codex":
        return codex_probe()
    if args.command == "verify-trigger":
        if not args.value or not args.value.isdigit():
            print("verify-trigger requires a numeric trigger ID.", file=sys.stderr)
            return 2
        return verify_trigger(int(args.value))
    service = SebastianService()
    signal.signal(signal.SIGTERM, service.request_stop)
    signal.signal(signal.SIGINT, service.request_stop)
    try:
        return service.run(once=args.once)
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
