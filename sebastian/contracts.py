"""Immutable channel boundary values. Bodies are untrusted, ephemeral context."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Channel(StrEnum):
    SLACK = 'slack'
    MESSAGES = 'messages'


class Access(StrEnum):
    OWNER = 'owner'
    CONVERSATION = 'conversation'
    TOOLBELT_READ_ONLY = 'toolbelt_read_only'


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid identity')


def timestamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timestamp must be timezone aware')


@dataclass(frozen=True)
class AttachmentRef:
    reference: str = field(repr=False)
    media_type: str
    size_bytes: int

    def __post_init__(self):
        identifier(self.reference)
        identifier(self.media_type)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError('invalid attachment size')


@dataclass(frozen=True)
class Destination:
    channel: Channel
    account_id: str
    conversation_id: str
    thread_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.channel, Channel):
            raise ValueError('invalid channel')
        identifier(self.account_id)
        identifier(self.conversation_id)
        if self.thread_id is not None:
            identifier(self.thread_id)


@dataclass(frozen=True)
class HistoryMessage:
    channel: Channel
    account_id: str
    sender_id: str
    conversation_id: str
    thread_id: str | None
    event_id: str
    occurred_at: datetime
    text: str = field(repr=False)
    attachments: tuple[AttachmentRef, ...] = field(default=(), repr=False)

    def __post_init__(self):
        Destination(self.channel, self.account_id, self.conversation_id, self.thread_id)
        identifier(self.sender_id)
        identifier(self.event_id)
        timestamp(self.occurred_at)
        if not isinstance(self.text, str) or len(self.text) > 100_000:
            raise ValueError('invalid text')
        if type(self.attachments) is not tuple or len(self.attachments) > 32 or any(type(a) is not AttachmentRef for a in self.attachments):
            raise ValueError('invalid attachments')


@dataclass(frozen=True)
class Event(HistoryMessage):
    is_group: bool = False
    is_from_me: bool = False
    owner_metadata_verified: bool = False
    history: tuple[HistoryMessage, ...] = field(default=(), repr=False)

    def __post_init__(self):
        super().__post_init__()
        if any(type(v) is not bool for v in (self.is_group, self.is_from_me, self.owner_metadata_verified)):
            raise ValueError('invalid metadata')
        if type(self.history) is not tuple or len(self.history) > 100 or any(type(h) is not HistoryMessage for h in self.history):
            raise ValueError('invalid history')


@dataclass(frozen=True)
class UntrustedContext:
    messages: tuple[HistoryMessage, ...] = field(repr=False)
    trust: str = field(default='untrusted', init=False)
