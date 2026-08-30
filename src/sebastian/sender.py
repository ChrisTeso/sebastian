from __future__ import annotations

import base64
import subprocess


class SendError(RuntimeError):
    pass


def _send_script(encoded_chat: str, encoded_message: str) -> str:
    # The payload is base64, so untrusted message content cannot become AppleScript.
    return f'''on run
set encodedChat to "{encoded_chat}"
set encodedMessage to "{encoded_message}"
set chatId to do shell script "/bin/echo " & quoted form of encodedChat & " | /usr/bin/base64 -D"
set messageText to do shell script "/bin/echo " & quoted form of encodedMessage & " | /usr/bin/base64 -D"
tell application "Messages"
    set targetChat to chat id chatId
    send messageText to targetChat
end tell
end run
'''


def send_to_chat(chat_guid: str, text: str, timeout: int = 30) -> None:
    encoded_chat = base64.b64encode(chat_guid.encode("utf-8")).decode("ascii")
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    result = subprocess.run(
        ["/usr/bin/osascript", "-"],
        input=_send_script(encoded_chat, encoded),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        # Never propagate stderr: AppleScript errors can contain conversation metadata.
        raise SendError(f"Messages automation failed with exit status {result.returncode}.")


def automation_probe() -> int:
    script = '''tell application "Messages"
set enabledCount to 0
repeat with accountItem in accounts
    if enabled of accountItem is true then set enabledCount to enabledCount + 1
end repeat
return enabledCount
end tell
'''
    result = subprocess.run(
        ["/usr/bin/osascript", "-"],
        input=script,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise SendError(f"Messages Automation permission check failed with exit status {result.returncode}.")
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise SendError("Messages returned an unexpected account status.") from exc
