from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

APPLE_EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.UTC)
OBJECT_REPLACEMENT_CHAR = "\ufffc"
REQUIRED_TABLES = {
    "message",
    "chat",
    "handle",
    "chat_message_join",
    "chat_handle_join",
    "attachment",
    "message_attachment_join",
}
REQUIRED_MESSAGE_COLUMNS = {
    "ROWID",
    "guid",
    "date",
    "is_from_me",
    "text",
    "attributedBody",
    "is_system_message",
    "associated_message_type",
    "is_sent",
    "error",
}
REQUIRED_ATTACHMENT_COLUMNS = {
    "ROWID",
    "filename",
    "mime_type",
    "uti",
    "total_bytes",
    "is_sticker",
    "hide_attachment",
    "is_commsafety_sensitive",
}
SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
IMAGE_SUFFIX_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


class ReadonlyConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        suppress = super().__exit__(exc_type, exc, traceback)
        self.close()
        return suppress


@dataclass(frozen=True)
class Message:
    rowid: int
    guid: str
    chat_id: int
    chat_guid: str
    date: int
    is_from_me: bool
    sender: str | None
    text: str
    has_attachments: bool

    @property
    def timestamp(self) -> str:
        return mac_time_to_datetime(self.date).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ImageAttachment:
    message_rowid: int
    path: Path
    mime_type: str
    byte_size: int


def connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    conn = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=5,
        factory=ReadonlyConnection,
    )
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def validate_schema(conn: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = REQUIRED_TABLES - tables
    if missing:
        raise RuntimeError(f"Unsupported Messages database schema; missing {len(missing)} required tables.")
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(message)").fetchall()}
    missing_columns = REQUIRED_MESSAGE_COLUMNS - columns
    if missing_columns:
        raise RuntimeError(
            f"Unsupported Messages database schema; missing {len(missing_columns)} required message columns."
        )
    attachment_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(attachment)").fetchall()
    }
    missing_attachment_columns = REQUIRED_ATTACHMENT_COLUMNS - attachment_columns
    if missing_attachment_columns:
        raise RuntimeError(
            "Unsupported Messages attachment schema; "
            f"missing {len(missing_attachment_columns)} required attachment columns."
        )


def mac_time_to_datetime(value: int | None) -> dt.datetime:
    if not value:
        return APPLE_EPOCH
    # Current Messages databases store nanoseconds; older versions used seconds.
    seconds = value / 1_000_000_000 if abs(value) > 10_000_000_000 else value
    return APPLE_EPOCH + dt.timedelta(seconds=seconds)


def datetime_to_mac_time(value: dt.datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return int((value.astimezone(dt.UTC) - APPLE_EPOCH).total_seconds() * 1_000_000_000)


def message_is_fresh(
    message: Message,
    max_age_seconds: int,
    now: dt.datetime | None = None,
) -> bool:
    moment = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    timestamp = mac_time_to_datetime(message.date)
    age = (moment - timestamp).total_seconds()
    # Allow modest clock skew, but never accept a message dated far in the future.
    return -300 <= age <= max_age_seconds


def _iter_strings(obj: Any, seen: set[int] | None = None) -> Iterable[str]:
    seen = seen or set()
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, str):
        yield obj
        return
    for attribute in ("value", "contents"):
        value = getattr(obj, attribute, None)
        if value is not None:
            yield from _iter_strings(value, seen)
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_strings(key, seen)
            yield from _iter_strings(value, seen)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            yield from _iter_strings(item, seen)


def decode_attributed_body(blob: bytes | None) -> str:
    if not blob:
        return ""
    try:
        import typedstream

        obj = typedstream.unarchive_from_data(blob)
    except Exception:
        return ""
    try:
        first = obj.contents[0]
        value = getattr(getattr(first, "value", first), "value", None)
        if isinstance(value, str):
            return value
    except Exception:
        pass
    candidates = [
        item
        for item in _iter_strings(obj)
        if item and not item.startswith("__") and item not in {"NSAttributedString", "NSObject", "NSString"}
    ]
    return max(candidates, key=len) if candidates else ""


def clean_text(text: str) -> str:
    return " ".join(text.replace(OBJECT_REPLACEMENT_CHAR, "[attachment]").split())


def message_text_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(clean_text(text).encode("utf-8")).hexdigest()


def message_guid_hash(guid: str) -> str:
    import hashlib

    return hashlib.sha256((guid or "").encode("utf-8")).hexdigest()


def _rows_to_messages(rows: Iterable[tuple[Any, ...]], include_empty: bool = False) -> list[Message]:
    messages: list[Message] = []
    for row in rows:
        rowid, guid, chat_id, chat_guid, date, is_from_me, sender, text, attributed, attachments = row
        body = clean_text(text or decode_attributed_body(attributed))
        if not body and attachments:
            body = "[attachment]"
        if not body and not include_empty:
            continue
        messages.append(
            Message(
                rowid=int(rowid),
                guid=str(guid) if guid else "",
                chat_id=int(chat_id),
                chat_guid=str(chat_guid),
                date=int(date or 0),
                is_from_me=bool(is_from_me),
                sender=str(sender) if sender else None,
                text=body,
                has_attachments=bool(attachments),
            )
        )
    return messages


BASE_SELECT = """
    SELECT m.ROWID, m.guid, cmj.chat_id, c.guid, m.date, m.is_from_me,
           h.id, m.text, m.attributedBody, m.cache_has_attachments
    FROM message m
    JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
    JOIN chat c ON c.ROWID = cmj.chat_id
    LEFT JOIN handle h ON h.ROWID = m.handle_id
"""


def latest_rowid(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()
    return int(row[0] if row else 0)


def messages_after(conn: sqlite3.Connection, after_rowid: int, limit: int = 500) -> list[Message]:
    rows = conn.execute(
        BASE_SELECT
        + """
        WHERE m.ROWID > ? AND m.is_system_message = 0
          AND COALESCE(m.associated_message_type, 0) = 0
        ORDER BY m.ROWID ASC
        LIMIT ?
        """,
        (after_rowid, limit),
    ).fetchall()
    return _rows_to_messages(rows, include_empty=True)


def message_by_id(conn: sqlite3.Connection, chat_id: int, rowid: int) -> Message | None:
    rows = conn.execute(
        BASE_SELECT
        + """
        WHERE cmj.chat_id = ? AND m.ROWID = ? AND m.is_system_message = 0
          AND COALESCE(m.associated_message_type, 0) = 0
        LIMIT 1
        """,
        (chat_id, rowid),
    ).fetchall()
    messages = _rows_to_messages(rows, include_empty=True)
    return messages[0] if messages else None


def previous_messages(
    conn: sqlite3.Connection, chat_id: int, before_rowid: int, limit: int
) -> list[Message]:
    if limit <= 0:
        return []
    rows = conn.execute(
        "SELECT * FROM ("
        + BASE_SELECT
        + """
          WHERE cmj.chat_id = ? AND m.ROWID < ? AND m.is_system_message = 0
            AND COALESCE(m.associated_message_type, 0) = 0
          ORDER BY m.ROWID DESC
          LIMIT ?
        ) ORDER BY ROWID ASC
        """,
        (chat_id, before_rowid, max(limit * 3, limit)),
    ).fetchall()
    messages = _rows_to_messages(rows)
    return messages[-limit:]


def image_attachments(
    conn: sqlite3.Connection,
    *,
    chat_id: int,
    message_rowids: Iterable[int],
    max_images: int,
    max_image_bytes: int,
    max_total_bytes: int,
    attachments_root: Path | None = None,
) -> list[ImageAttachment]:
    rowids = sorted({int(rowid) for rowid in message_rowids})
    if not rowids or max_images <= 0:
        return []
    root = (
        attachments_root or Path.home() / "Library" / "Messages" / "Attachments"
    ).expanduser()
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return []
    placeholders = ",".join("?" for _ in rowids)
    rows = conn.execute(
        f"""
        SELECT a.ROWID, maj.message_id, a.filename, LOWER(COALESCE(a.mime_type, ''))
        FROM attachment a
        JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
        JOIN chat_message_join cmj ON cmj.message_id = maj.message_id
        WHERE cmj.chat_id = ? AND maj.message_id IN ({placeholders})
          AND COALESCE(a.is_sticker, 0) = 0
          AND COALESCE(a.hide_attachment, 0) = 0
          AND COALESCE(a.is_commsafety_sensitive, 0) = 0
        ORDER BY maj.message_id DESC, a.ROWID DESC
        """,
        (chat_id, *rowids),
    ).fetchall()
    selected: list[tuple[int, ImageAttachment]] = []
    total_bytes = 0
    seen_paths: set[Path] = set()
    for attachment_id, message_rowid, filename, declared_mime in rows:
        if not filename:
            continue
        try:
            path = Path(str(filename)).expanduser().resolve(strict=True)
        except OSError:
            continue
        if not path.is_relative_to(resolved_root) or not path.is_file() or path in seen_paths:
            continue
        mime_type = str(declared_mime or "")
        if mime_type == "image/jpg":
            mime_type = "image/jpeg"
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            mime_type = IMAGE_SUFFIX_MIME_TYPES.get(path.suffix.lower(), "")
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            continue
        try:
            byte_size = path.stat().st_size
        except OSError:
            continue
        if byte_size <= 0 or byte_size > max_image_bytes:
            continue
        if total_bytes + byte_size > max_total_bytes:
            continue
        selected.append(
            (
                int(attachment_id),
                ImageAttachment(
                    message_rowid=int(message_rowid),
                    path=path,
                    mime_type=mime_type,
                    byte_size=int(byte_size),
                ),
            )
        )
        seen_paths.add(path)
        total_bytes += byte_size
        if len(selected) >= max_images:
            break
    return [
        attachment
        for _, attachment in sorted(
            selected, key=lambda item: (item[1].message_rowid, item[0])
        )
    ]


def participants(conn: sqlite3.Connection, chat_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT h.id
        FROM chat_handle_join chj
        JOIN handle h ON h.ROWID = chj.handle_id
        WHERE chj.chat_id = ? AND h.id IS NOT NULL
        ORDER BY h.id
        """,
        (chat_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def chat_guid(conn: sqlite3.Connection, chat_id: int) -> str:
    row = conn.execute("SELECT guid FROM chat WHERE ROWID = ?", (chat_id,)).fetchone()
    if not row or not row[0]:
        raise RuntimeError("Messages conversation no longer exists.")
    return str(row[0])


def matching_outgoing_rowid(
    conn: sqlite3.Connection,
    *,
    chat_id: int,
    response_hash: str,
    since: dt.datetime,
) -> int | None:
    matches = matching_outgoing_rowids(
        conn, chat_id=chat_id, response_hash=response_hash, since=since
    )
    return matches[0] if matches else None


def matching_outgoing_rowids(
    conn: sqlite3.Connection,
    *,
    chat_id: int,
    response_hash: str,
    since: dt.datetime,
) -> list[int]:
    cutoff = datetime_to_mac_time(since - dt.timedelta(seconds=5))
    rows = conn.execute(
        """
        SELECT m.ROWID, m.text, m.attributedBody
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id = ? AND m.is_from_me = 1 AND m.date >= ?
          AND COALESCE(m.associated_message_type, 0) = 0
          AND m.is_sent = 1 AND COALESCE(m.error, 0) = 0
        ORDER BY m.ROWID DESC
        LIMIT 100
        """,
        (chat_id, cutoff),
    ).fetchall()
    matches: list[int] = []
    for rowid, text, attributed in rows:
        body = clean_text(text or decode_attributed_body(attributed))
        if body and message_text_hash(body) == response_hash:
            matches.append(int(rowid))
    return matches


def signed_outgoing_rowids_after(
    conn: sqlite3.Connection,
    *,
    chat_id: int,
    after_rowid: int,
    signature: str,
) -> list[int]:
    rows = conn.execute(
        """
        SELECT m.ROWID, m.text, m.attributedBody
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id = ? AND m.is_from_me = 1 AND m.ROWID > ?
          AND COALESCE(m.associated_message_type, 0) = 0
          AND m.is_sent = 1 AND COALESCE(m.error, 0) = 0
        ORDER BY m.ROWID ASC
        """,
        (chat_id, after_rowid),
    ).fetchall()
    matches: list[int] = []
    for rowid, text, attributed in rows:
        body = clean_text(text or decode_attributed_body(attributed))
        if body.endswith(signature) and body.count(signature) == 1:
            matches.append(int(rowid))
    return matches


def outgoing_delivery_state(
    conn: sqlite3.Connection,
    *,
    chat_id: int,
    response_hash: str,
    since: dt.datetime,
) -> tuple[str, int | None]:
    cutoff = datetime_to_mac_time(since - dt.timedelta(seconds=5))
    rows = conn.execute(
        """
        SELECT m.ROWID, m.text, m.attributedBody, m.is_sent, COALESCE(m.error, 0)
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id = ? AND m.is_from_me = 1 AND m.date >= ?
          AND COALESCE(m.associated_message_type, 0) = 0
        ORDER BY m.ROWID DESC
        LIMIT 100
        """,
        (chat_id, cutoff),
    ).fetchall()
    matched: list[tuple[int, int, int]] = []
    for rowid, text, attributed, is_sent, error in rows:
        body = clean_text(text or decode_attributed_body(attributed))
        if body and message_text_hash(body) == response_hash:
            matched.append((int(rowid), int(is_sent or 0), int(error or 0)))
    for rowid, is_sent, error in matched:
        if is_sent == 1 and error == 0:
            return "sent", rowid
    for rowid, is_sent, error in matched:
        if is_sent == 0 and error == 0:
            return "pending", rowid
    if matched:
        return "failed", matched[0][0]
    return "absent", None
