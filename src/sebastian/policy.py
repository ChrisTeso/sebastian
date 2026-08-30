from __future__ import annotations

import re
from .constants import SIGNATURE

TAG_RE = re.compile(r"(?<![\w@])@sebastian\b", re.IGNORECASE)
SIGNATURE_RE = re.compile(r"(?:\s*— Sebastian, Chris[’']s AI assistant\s*)+$", re.IGNORECASE)
SIGNATURE_ANY_RE = re.compile(r"— Sebastian, Chris[’']s AI assistant", re.IGNORECASE)


def plain_text(text: str) -> str:
    """Remove common Markdown syntax while preserving readable content."""
    value = re.sub(r"^\s*```[^\n]*$", "", text, flags=re.MULTILINE)
    value = re.sub(r"`([^`\n]+)`", r"\1", value)
    value = re.sub(r"!\[([^\]]*)\]\((https?://[^)]+)\)", r"\1 (\2)", value)
    value = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", value)
    value = re.sub(r"(?<!\\)(\*\*|__)(.+?)\1", r"\2", value)
    value = re.sub(r"(?<![\\\w])(\*|_)([^\n]+?)\1(?!\w)", r"\2", value)
    value = re.sub(r"^\s{0,3}#{1,6}\s+", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s{0,3}>\s?", "", value, flags=re.MULTILINE)
    return value


def contains_trigger(text: str) -> bool:
    return bool(TAG_RE.search(text or ""))


def strip_trigger(text: str) -> str:
    cleaned = TAG_RE.sub("", text or "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"[,;:]([!?])", r"\1", cleaned)
    return cleaned.strip(" \t\r\n,;:-")


def is_loop_response(text: str) -> bool:
    return bool(SIGNATURE_RE.search(text or ""))


def should_trigger(*, text: str, is_from_me: bool) -> bool:
    # Chris can invoke Sebastian from an outgoing message. Sebastian's own
    # outgoing replies are distinguished by the required signature, which also
    # prevents a model-produced mention from creating a reply loop.
    return bool(text) and contains_trigger(text) and not is_loop_response(text)


def finalize_response(text: str, max_chars: int) -> str:
    body = SIGNATURE_ANY_RE.sub("", (text or "").strip()).strip()
    body = plain_text(body).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if not body:
        body = "I’m sorry, but I couldn’t produce a useful answer."
    separator = "\n\n"
    body_limit = max_chars - len(separator) - len(SIGNATURE)
    if len(body) > body_limit:
        body = body[: max(1, body_limit - 1)].rstrip() + "…"
    return f"{body}{separator}{SIGNATURE}"


def allowlisted(
    config: dict,
    chat_guid: str,
    sender: str | None,
    *,
    is_from_me: bool = False,
) -> bool:
    settings = config.get("allowlist", {})
    if not settings.get("enabled") or is_from_me:
        return True
    chats = set(settings.get("conversation_guids", []))
    senders = set(settings.get("participants", []))
    return chat_guid in chats or bool(sender and sender in senders)
