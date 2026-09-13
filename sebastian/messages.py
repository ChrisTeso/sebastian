"""Fresh read-only Messages ingress. No raw bodies are persisted or logged."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import os
import stat
import sqlite3
import subprocess
from typing import Callable

from .contracts import AttachmentRef, Channel, Destination, Event, HistoryMessage, identifier
from .policy import AdapterSigner, Envelope

MENTION = re.compile(r'(?<![\w@])@sebastian\b', re.IGNORECASE)
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def apple_timestamp(value: int | float) -> datetime:
    # Older databases use seconds; current databases use nanoseconds.
    numeric = float(value)
    if abs(numeric) > 100_000_000_000:
        numeric /= 1_000_000_000
    return APPLE_EPOCH + timedelta(seconds=numeric)


def decode_body(text: str | None, attributed: bytes | None) -> str:
    if text:
        return text[:100_000]
    if not attributed:
        return ''
    if len(attributed) > 2_000_000:
        raise ValueError('Messages body exceeds decoding limit')
    import typedstream
    from typedstream.archiving import GenericArchivedObject
    from typedstream.types.foundation import NSString
    try:
        root = typedstream.unarchive_from_data(attributed)
        if isinstance(root, NSString):
            return root.value[:100_000]
        if isinstance(root, GenericArchivedObject):
            cls = root.clazz
            names = set()
            while cls is not None:
                names.add(cls.name)
                cls = cls.superclass
            if names & {b'NSAttributedString', b'NSMutableAttributedString'}:
                # Only the attributed string's first field is content; never
                # extract arbitrary strings from attribute dictionaries.
                first = root.contents[0].values[0]
                if isinstance(first, NSString):
                    return first.value[:100_000]
    except Exception:
        raise ValueError('Unsupported or malformed Messages attributed body') from None
    raise ValueError('Unsupported Messages attributed body')


@dataclass(frozen=True)
class OwnerMetadata:
    """Trusted setup metadata, never populated from a message/caller claim.

    Each pair is the exact message.account/account_guid observed and verified
    during local account setup; chat login is verified independently.
    """
    account_id: str
    sender_id: str
    account_pairs: frozenset[tuple[str, str]] = field(repr=False)
    chat_logins: frozenset[str] = field(repr=False)
    owner_private_chat_ids: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __post_init__(self):
        identifier(self.account_id)
        identifier(self.sender_id)
        if type(self.account_pairs) is not frozenset or type(self.chat_logins) is not frozenset or not self.account_pairs or not self.chat_logins:
            raise ValueError('verified account metadata required')
        for pair in self.account_pairs:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError('invalid account metadata')
            for value in pair:
                identifier(value)
        for login in self.chat_logins:
            identifier(login)
        if type(self.owner_private_chat_ids) is not frozenset:
            raise ValueError('immutable owner chat allowlist required')
        for chat in self.owner_private_chat_ids:
            identifier(chat)


class MessagesAdapter:
    def __init__(self, db_path: Path, owner: OwnerMetadata, signer: AdapterSigner,
                 *, highwater: int | None = None, history_limit: int = 10,
                 is_self_reply: Callable[[Event], bool] = lambda event: False,
                 is_sebastian_message: Callable[[Destination, str], bool] = lambda destination, guid: False):
        if type(owner) is not OwnerMetadata or not 0 <= history_limit <= 10:
            raise ValueError('invalid adapter configuration')
        if highwater is not None and (type(highwater) is not int or highwater < 0):
            raise ValueError('invalid highwater')
        self.owner, self.signer = owner, signer
        self.db = sqlite3.connect(Path(db_path).expanduser().resolve().as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.history_limit, self.is_self_reply = history_limit, is_self_reply
        self.is_sebastian_message = is_sebastian_message
        self.columns = {t: {r[1] for r in self.db.execute(f'PRAGMA table_info({t})')} for t in
                        ('message', 'chat', 'handle', 'attachment', 'chat_handle_join', 'message_attachment_join')}
        for table, required in {'message': {'guid', 'text', 'date', 'is_from_me', 'handle_id'},
                                'chat': {'guid'}, 'handle': {'id'}}.items():
            if not required <= self.columns[table]:
                self.db.close()
                raise ValueError('Unsupported Messages database schema')
        # A first launch deliberately starts at the current maximum, no backlog.
        self.highwater = self.db.execute('SELECT COALESCE(MAX(ROWID),0) FROM message').fetchone()[0] if highwater is None else highwater
        self._seen: set[tuple[str, str]] = set()
        # Count invalid decode/validation encounters, not unique records. No
        # content, paths, recipient IDs or exception details are retained.
        self.invalid_record_count = 0

    def close(self):
        self.db.close()

    def _rows(self, where: str, args: tuple, limit: int, descending: bool = False):
        fields = []
        for name in ('style', 'account_login'):
            fields.append(f'c.{name} AS chat_{name}' if name in self.columns['chat'] else f'NULL AS chat_{name}')
        order = 'DESC' if descending else 'ASC'
        return self.db.execute(f'''SELECT m.ROWID AS message_rowid, m.*, c.ROWID AS chat_rowid,
             c.guid AS chat_guid, h.id AS handle_identifier, {', '.join(fields)}
             FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID
             JOIN chat c ON c.ROWID=j.chat_id LEFT JOIN handle h ON h.ROWID=m.handle_id
             WHERE {where} ORDER BY m.ROWID {order}, c.ROWID {order} LIMIT ?''', (*args, limit)).fetchall()

    def _event(self, row) -> Event:
        data = dict(row)
        from_me = data['is_from_me'] == 1
        verified = from_me and (data.get('account'), data.get('account_guid')) in self.owner.account_pairs and data['chat_account_login'] in self.owner.chat_logins
        sender = self.owner.sender_id if verified else ('unverified-local' if from_me else data['handle_identifier'] or 'unknown-sender')
        group = data['chat_style'] != 45  # Unknown schemas conservatively require mention.
        if self.columns['chat_handle_join']:
            count = self.db.execute('SELECT COUNT(*) FROM chat_handle_join WHERE chat_id=?', (data['chat_rowid'],)).fetchone()[0]
            group = group or count > 1
        attachments = []
        if self.columns['message_attachment_join'] and {'guid', 'mime_type', 'total_bytes'} <= self.columns['attachment']:
            for a in self.db.execute('''SELECT a.guid,a.mime_type,a.total_bytes FROM attachment a
                    JOIN message_attachment_join j ON j.attachment_id=a.ROWID WHERE j.message_id=? LIMIT 32''', (data['message_rowid'],)):
                if a['guid']:
                    attachments.append(AttachmentRef('messages-attachment:' + a['guid'], a['mime_type'] or 'application/octet-stream', max(0, a['total_bytes'] or 0)))
        return Event(Channel.MESSAGES, self.owner.account_id, sender, data['chat_guid'], None,
                     data['guid'] or f"row:{data['message_rowid']}", apple_timestamp(data['date']),
                     decode_body(data['text'], data.get('attributedBody')), tuple(attachments),
                     group, from_me, verified)

    def _record(self, row) -> Event | None:
        data = dict(row)
        # Filter before decoding in both ingress and retrieved history.
        if any(data.get(k, 0) for k in ('associated_message_type', 'is_system_message', 'is_service_message', 'item_type')):
            return None
        try:
            return self._event(row)
        except (ValueError, TypeError, OverflowError):
            self.invalid_record_count += 1
            return None
        # SQLite, missing dependencies and other infrastructure failures escape.

    def history(self, conversation_id: str, before_rowid: int, limit: int | None = None) -> tuple[HistoryMessage, ...]:
        identifier(conversation_id)
        count = self.history_limit if limit is None else min(max(limit, 0), self.history_limit)
        if count == 0:
            return ()
        events = []
        # Bounded scan tolerates invalid/reaction records while collecting up
        # to ten usable predecessors from the exact same conversation.
        for row in self._rows('c.guid=? AND m.ROWID<?', (conversation_id, before_rowid), 100, True):
            event = self._record(row)
            if event is not None:
                events.append(event)
                if len(events) == count:
                    break
        return tuple(HistoryMessage(e.channel, e.account_id, e.sender_id, e.conversation_id, e.thread_id,
                                   e.event_id, e.occurred_at, e.text, e.attachments) for e in reversed(events))

    def is_reply_to_sebastian(self, row) -> bool:
        """Native reply metadata plus an exact confirmed host delivery receipt.

        A signature or ordinary outgoing owner message cannot establish a bot
        parent. Missing native reply metadata fails closed on older schemas.
        """
        data = dict(row)
        parent_guid = data.get('thread_originator_guid')
        if not isinstance(parent_guid, str) or not parent_guid or len(parent_guid) > 512:
            return False
        parents = self._rows('c.guid=? AND m.guid=? AND m.ROWID<?',
                             (data['chat_guid'], parent_guid, data['message_rowid']), 2)
        if len(parents) != 1:
            return False
        parent = dict(parents[0])
        if (parent['is_from_me'] != 1
                or (parent.get('account'), parent.get('account_guid')) not in self.owner.account_pairs
                or parent['chat_account_login'] not in self.owner.chat_logins):
            return False
        destination = Destination(Channel.MESSAGES, self.owner.account_id, data['chat_guid'])
        return self.is_sebastian_message(destination, parent_guid)

    def poll(self, limit: int = 100) -> tuple[Envelope, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('invalid poll limit')
        # Select distinct message ROWIDs first so chats sharing a message cannot
        # be skipped at a batch boundary.
        ids = [r[0] for r in self.db.execute('SELECT ROWID FROM message WHERE ROWID>? ORDER BY ROWID LIMIT ?', (self.highwater, limit))]
        if not ids:
            return ()
        rows = self._rows('m.ROWID>? AND m.ROWID<=?', (self.highwater, ids[-1]), 100_000)
        output = []
        from dataclasses import replace
        seen = set(self._seen)
        for row in rows:
            event = self._record(row)
            if event is None:
                continue
            key = (event.conversation_id, event.event_id)
            if key in seen:
                continue
            seen.add(key)
            # Messages stores both outgoing and incoming copies of texts sent
            # to oneself. Only the verified outgoing copy is a request. This
            # drops a known self mirror; it never grants authority to incoming.
            own_aliases = {v[2:] if v.startswith(('E:', 'P:')) else v for v in self.owner.chat_logins}
            if (not event.is_from_me and not event.is_group
                    and event.conversation_id in self.owner.owner_private_chat_ids
                    and event.sender_id in own_aliases):
                continue
            if event.is_from_me and self.is_self_reply(event):
                continue
            if (not (event.owner_metadata_verified and not event.is_group and event.conversation_id in self.owner.owner_private_chat_ids)
                    and not MENTION.search(event.text) and not self.is_reply_to_sebastian(row)):
                continue
            output.append(self.signer.sign(replace(event, history=self.history(event.conversation_id, row['message_rowid']))))
        # Caller persists highwater with accepted queue records in S5. Poll is
        # intentionally not a durable queue or acknowledgement protocol.
        self.highwater = ids[-1]
        self._seen = seen if len(seen) <= 10_000 else { (e.event.conversation_id, e.event.event_id) for e in output }
        return tuple(output)


SEND_SCRIPT = '''on run argv
    set targetChatID to item 1 of argv
    set replyText to item 2 of argv
    tell application "Messages"
        set targetChat to chat id targetChatID
        send replyText to targetChat
    end tell
end run'''

SEND_IMAGE_SCRIPT = '''on run argv
    set targetChatID to item 1 of argv
    set imageFile to POSIX file (item 2 of argv)
    tell application "Messages"
        set targetChat to chat id targetChatID
        send imageFile to targetChat
    end tell
end run'''


class MessagesSender:
    """Sends from this Mac's signed-in Messages account into an exact chat.

    Construction is inert. command() supports safe fixture-only validation.
    The delivery ledger must register pending fingerprints before send().
    """
    def __init__(self, account_id: str, run: Callable = subprocess.run):
        identifier(account_id)
        self.account_id, self.run = account_id, run

    def command(self, destination: Destination, text: str) -> list[str]:
        if destination.channel != Channel.MESSAGES or destination.account_id != self.account_id or destination.thread_id is not None:
            raise ValueError('Messages destination mismatch')
        if not isinstance(text, str) or not text or len(text) > 20_000 or '\x00' in text:
            raise ValueError('invalid Messages reply')
        return ['/usr/bin/osascript', '-e', SEND_SCRIPT, '--', destination.conversation_id, text]

    def send(self, destination: Destination, text: str) -> None:
        command = self.command(destination, text)
        try:
            result = self.run(command, capture_output=True, timeout=30, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise RuntimeError('Messages delivery failed or is uncertain; reconcile before retry') from None
        if result.returncode != 0:
            # Do not leak osascript errors: they can contain recipient/body text.
            raise RuntimeError('Messages delivery failed or is uncertain; reconcile before retry')

    def image_command(self, destination: Destination, path: Path) -> list[str]:
        if destination.channel != Channel.MESSAGES or destination.account_id != self.account_id or destination.thread_id is not None:
            raise ValueError('Messages destination mismatch')
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError('absolute prepared image path required')
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077 or not 0 < metadata.st_size <= 20_000_000):
            raise ValueError('private prepared image file required')
        return ['/usr/bin/osascript', '-e', SEND_IMAGE_SCRIPT, '--', destination.conversation_id, str(path)]

    def send_image(self, destination: Destination, path: Path) -> None:
        """Send a caller-prepared private file; the caller owns its lifetime.

        Keep the file in an owner-only temporary directory outside the repository
        until a matching Messages attachment receipt is observed. An osascript
        return alone does not establish that Messages copied or delivered it.
        """
        command = self.image_command(destination, path)
        try:
            result = self.run(command, capture_output=True, timeout=30, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise RuntimeError('Messages image delivery failed or is uncertain; reconcile before retry') from None
        if result.returncode != 0:
            raise RuntimeError('Messages image delivery failed or is uncertain; reconcile before retry')
