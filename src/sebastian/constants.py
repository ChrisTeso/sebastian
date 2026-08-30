from __future__ import annotations

from pathlib import Path

LABEL = "com.christeso.sebastian"
KEYCHAIN_SERVICE = "com.christeso.sebastian.openai"
SIGNATURE = "— Sebastian, Chris’s AI assistant"
TAG = "@sebastian"

DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "Sebastian"
DEFAULT_APP = Path.home() / "Applications" / "Sebastian.app"
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "Sebastian"
DEFAULT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DEFAULT_MESSAGES_DB = Path.home() / "Library" / "Messages" / "chat.db"


def sebastian_home() -> Path:
    import os

    return Path(os.environ.get("SEBASTIAN_HOME", str(DEFAULT_HOME))).expanduser()
