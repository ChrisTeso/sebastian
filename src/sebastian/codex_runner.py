from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


APPROVAL_MARKER = "SEBASTIAN_APPROVAL_REQUIRED:"


@dataclass(frozen=True)
class CodexRunResult:
    status: str
    output: str
    error_code: str | None = None


def resolve_codex_executable(configured: str = "") -> Path:
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.absolute()
        raise FileNotFoundError("Configured Codex executable is unavailable.")

    on_path = shutil.which("codex")
    if on_path:
        return Path(on_path).absolute()

    home = Path.home()
    candidates = [home / ".local" / "bin" / "codex"]
    nvm_versions = home / ".nvm" / "versions" / "node"
    if nvm_versions.is_dir():
        candidates.extend(
            sorted(nvm_versions.glob("*/bin/codex"), reverse=True)
        )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.absolute()
    raise FileNotFoundError("Codex CLI is not installed or executable.")


def consequential_request(text: str) -> bool:
    value = " ".join((text or "").lower().split())
    patterns = (
        r"\b(?:send|email|text|message|post|publish)\b.*\b(?:to|on|public|live)\b",
        r"\b(?:buy|purchase|order|pay|transfer|trade|sell)\b",
        r"\b(?:delete|erase|wipe|destroy)\b",
        r"\b(?:password|api key|secret key|sign in|log in|revoke)\b",
        r"\b(?:deploy|push|merge|submit|apply for|book|reserve)\b",
        r"\b(?:cancel|close)\b.*\b(?:account|subscription|service|booking)\b",
    )
    return any(re.search(pattern, value) for pattern in patterns)


def _agent_instructions(*, approved: bool) -> str:
    approval = (
        "The user explicitly approved the consequential actions described in this task. "
        "Do not expand that approval beyond the task."
        if approved
        else (
            "Do not send communications, publish, purchase, transfer money, change accounts or "
            "credentials, deploy, push, merge, submit forms, or perform destructive or otherwise "
            "consequential external actions. If one becomes necessary, stop without doing it and "
            f"end with exactly `{APPROVAL_MARKER} <concise action summary>`."
        )
    )
    return (
        "You are running as Sebastian's local Codex worker from an owner-authenticated Apple "
        "Messages command. Work autonomously within the configured workspace. Use installed "
        "skills and plugins when useful. Never read or modify the Messages database. Preserve "
        "unrelated worktree changes. Keep the final response concise and plain text because it "
        f"will be returned by SMS. {approval}"
    )


class CodexRunner:
    def __init__(self, config: dict) -> None:
        settings = config.get("agent", {})
        self.executable = resolve_codex_executable(str(settings.get("codex_executable", "")))
        self.workspace = Path(str(settings.get("workspace", "~/Code"))).expanduser().resolve()
        self.sandbox = str(settings.get("sandbox", "workspace-write"))
        self.timeout_seconds = int(settings.get("timeout_seconds", 1800))
        self.model = str(settings.get("model") or config.get("model") or "")
        self.reasoning_effort = str(
            settings.get("reasoning_effort")
            or config.get("reasoning", {}).get("effort", "medium")
        )
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> bool:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return False
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        return True

    def run(self, request: str, *, approved: bool = False) -> CodexRunResult:
        if not self.workspace.is_dir():
            return CodexRunResult("failed", "", "workspace_unavailable")

        prompt = request.strip()
        output_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="codex-result-",
                suffix=".txt",
                delete=False,
            ) as handle:
                output_path = Path(handle.name)
            output_path.chmod(0o600)
            args = [
                str(self.executable),
                "--sandbox",
                self.sandbox,
                "--ask-for-approval",
                "never",
                "--search",
                "--cd",
                str(self.workspace),
            ]
            if self.model:
                args.extend(["--model", self.model])
            if self.reasoning_effort:
                args.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
            args.extend(
                [
                    "-c",
                    "developer_instructions="
                    + json.dumps(_agent_instructions(approved=approved)),
                ]
            )
            args.extend(
                [
                    "exec",
                    "--ephemeral",
                    "--color",
                    "never",
                    "--skip-git-repo-check",
                    "--output-last-message",
                    str(output_path),
                    "-",
                ]
            )
            environment = os.environ.copy()
            # The native Sebastian launcher injects its dedicated Responses API key.
            # Codex uses its own login and must never inherit that secret.
            environment.pop("SEBASTIAN_OPENAI_API_KEY", None)
            environment["PATH"] = os.pathsep.join(
                [str(self.executable.parent), environment.get("PATH", "")]
            ).rstrip(os.pathsep)
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                env=environment,
            )
            with self._lock:
                self._process = process
            try:
                _stdout, stderr = process.communicate(prompt, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                self.cancel()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                return CodexRunResult("failed", "", "timeout")
            finally:
                with self._lock:
                    self._process = None

            output = ""
            if output_path.exists():
                output = output_path.read_text(encoding="utf-8", errors="replace").strip()
            if process.returncode == -signal.SIGTERM:
                return CodexRunResult("cancelled", "", "cancelled")
            if process.returncode != 0:
                error_code = "codex_failed"
                if "not logged in" in (stderr or "").lower():
                    error_code = "codex_not_logged_in"
                return CodexRunResult("failed", output, error_code)
            if not output:
                return CodexRunResult("failed", "", "empty_result")
            if APPROVAL_MARKER in output and not approved:
                summary = output.split(APPROVAL_MARKER, 1)[1].strip()
                return CodexRunResult("approval_required", summary or "consequential action")
            return CodexRunResult("completed", output)
        except FileNotFoundError:
            return CodexRunResult("failed", "", "codex_unavailable")
        except Exception:
            return CodexRunResult("failed", "", "runner_error")
        finally:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
