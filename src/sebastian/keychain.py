from __future__ import annotations

import os


class KeychainError(RuntimeError):
    pass


def get_api_key() -> str:
    key = os.environ.get("SEBASTIAN_OPENAI_API_KEY", "").strip()
    if not key:
        raise KeychainError("Sebastian's OpenAI API key is unavailable in the login Keychain.")
    return key


def api_key_exists() -> bool:
    try:
        return bool(get_api_key())
    except KeychainError:
        return False
