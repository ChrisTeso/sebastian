"""Identity and audience decisions, enforced before any reasoning runtime call.

Registry setup and signers belong exclusively to trusted channel adapters. Never
expose signing credentials, registration, or policy configuration as model tools.
"""
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import hashlib
import hmac
import json
import secrets

from .contracts import Access, Channel, Destination, Event, HistoryMessage, UntrustedContext, identifier


def _canonical(event: Event) -> bytes:
    return json.dumps(asdict(event), sort_keys=True, separators=(',', ':'), default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)).encode()


@dataclass(frozen=True)
class Envelope:
    adapter_id: str
    event: Event = field(repr=False)
    signature: str = field(repr=False)


@dataclass(frozen=True)
class AuthenticatedEvent:
    """A receipt, not a caller assertion; Policy rechecks its registry seal."""
    envelope: Envelope = field(repr=False)
    seal: str = field(repr=False)


class AdapterSigner:
    def __init__(self, adapter_id: str, key: bytes):
        self._adapter_id = adapter_id
        self._key = key

    def sign(self, event: Event) -> Envelope:
        if type(event) is not Event:
            raise ValueError('invalid event')
        return Envelope(self._adapter_id, event, hmac.new(self._key, _canonical(event), hashlib.sha256).hexdigest())


def _matches(expected: str, supplied: object) -> bool:
    return isinstance(supplied, str) and supplied.isascii() and len(supplied) == len(expected) and hmac.compare_digest(expected, supplied)


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, tuple[Channel, str, bytes]] = {}
        self._receipt_key = secrets.token_bytes(32)

    def register(self, adapter_id: str, channel: Channel, account_id: str, secret: bytes | None = None) -> AdapterSigner:
        identifier(adapter_id)
        identifier(account_id)
        if not isinstance(channel, Channel) or adapter_id in self._adapters:
            raise ValueError('invalid adapter registration')
        key = secrets.token_bytes(32) if secret is None else secret
        if type(key) is not bytes or len(key) < 32:
            raise ValueError('adapter key must contain at least 32 bytes')
        self._adapters[adapter_id] = (channel, account_id, key)
        return AdapterSigner(adapter_id, key)

    def _verify(self, envelope: Envelope) -> Event:
        if type(envelope) is not Envelope or type(envelope.event) is not Event or not isinstance(envelope.adapter_id, str):
            raise PermissionError('unauthenticated event')
        registration = self._adapters.get(envelope.adapter_id)
        if registration is None:
            raise PermissionError('unknown adapter')
        channel, account, key = registration
        event = envelope.event
        if event.channel != channel or event.account_id != account:
            raise PermissionError('adapter scope mismatch')
        expected = hmac.new(key, _canonical(event), hashlib.sha256).hexdigest()
        if not _matches(expected, envelope.signature):
            raise PermissionError('invalid adapter signature')
        return event

    def _seal(self, envelope: Envelope) -> str:
        payload = json.dumps([envelope.adapter_id, envelope.signature], separators=(',', ':')).encode()
        return hmac.new(self._receipt_key, payload, hashlib.sha256).hexdigest()

    def authenticate(self, envelope: Envelope) -> AuthenticatedEvent:
        self._verify(envelope)
        return AuthenticatedEvent(envelope, self._seal(envelope))

    def verified_event(self, authenticated: AuthenticatedEvent) -> Event:
        if type(authenticated) is not AuthenticatedEvent or type(authenticated.envelope) is not Envelope:
            raise PermissionError('authenticated receipt required')
        event = self._verify(authenticated.envelope)
        if not _matches(self._seal(authenticated.envelope), authenticated.seal):
            raise PermissionError('invalid authentication receipt')
        return event


@dataclass(frozen=True)
class PolicyConfig:
    # Slack account_id is the provider workspace/team ID, never a display name.
    owner_slack: frozenset[tuple[str, str]] = frozenset()
    owner_messages: frozenset[tuple[str, str]] = frozenset()
    toolbelt_slack: frozenset[tuple[str, str]] = frozenset()
    # Trusted, preverified private owner conversations, one per account/channel.
    owner_destinations: tuple[Destination, ...] = ()
    # Explicit owner preference permits answers in the originating group.
    owner_group_replies: bool = False
    permission_revision: str = '1'
    messages_history_limit: int = 10
    context_char_limit: int = 16_000

    def __post_init__(self):
        identifier(self.permission_revision)
        if type(self.owner_group_replies) is not bool:
            raise ValueError('invalid group reply authorization')
        for identities in (self.owner_slack, self.owner_messages, self.toolbelt_slack):
            if type(identities) is not frozenset:
                raise ValueError('identities must be immutable')
            for identity in identities:
                if type(identity) is not tuple or len(identity) != 2:
                    raise ValueError('identity requires account and sender')
                for value in identity:
                    identifier(value)
        if self.owner_slack & self.toolbelt_slack:
            raise ValueError('conflicting identity profiles')
        if type(self.owner_destinations) is not tuple or any(type(d) is not Destination for d in self.owner_destinations):
            raise ValueError('invalid owner destinations')
        pairs = [(d.channel, d.account_id) for d in self.owner_destinations]
        if len(set(pairs)) != len(pairs):
            raise ValueError('duplicate owner destination')
        required = {(Channel.SLACK, a) for a, _ in self.owner_slack} | {(Channel.MESSAGES, a) for a, _ in self.owner_messages}
        if not required.issubset(set(pairs)):
            raise ValueError('owner identity requires a private destination')
        if type(self.messages_history_limit) is not int or not 0 <= self.messages_history_limit <= 10:
            raise ValueError('invalid history limit')
        if type(self.context_char_limit) is not int or not 0 <= self.context_char_limit <= 32_000:
            raise ValueError('invalid context budget')


@dataclass(frozen=True)
class Decision:
    access: Access
    session_key: str
    destination: Destination
    context: UntrustedContext = field(repr=False)
    event: Event = field(repr=False)
    disclosure_authorized: bool


class Policy:
    def __init__(self, config: PolicyConfig, registry: AdapterRegistry):
        if type(config) is not PolicyConfig or type(registry) is not AdapterRegistry:
            raise ValueError('trusted policy config and registry required')
        self.config = config
        self.registry = registry
        # Automatic fingerprint prevents accidental session reuse even when an
        # operator changes identities without also incrementing the revision.
        self._config_fingerprint = hashlib.sha256(json.dumps({
            'owner_slack': sorted(config.owner_slack), 'owner_messages': sorted(config.owner_messages),
            'toolbelt_slack': sorted(config.toolbelt_slack),
            'destinations': [asdict(d) for d in config.owner_destinations],
            'revision': config.permission_revision, 'owner_group_replies': config.owner_group_replies,
        }, sort_keys=True).encode()).hexdigest()

    def decide(self, authenticated: AuthenticatedEvent) -> Decision:
        event = self.registry.verified_event(authenticated)
        identity = (event.account_id, event.sender_id)
        owner = (event.channel == Channel.SLACK and identity in self.config.owner_slack) or (
            event.channel == Channel.MESSAGES and identity in self.config.owner_messages
            and event.is_from_me and event.owner_metadata_verified)
        access = Access.OWNER if owner else Access.CONVERSATION
        if not owner and event.channel == Channel.SLACK and identity in self.config.toolbelt_slack:
            access = Access.TOOLBELT_READ_ONLY
        # A deliberately narrow, anchored command in the authenticated trigger.
        # Quoted history, attachment instructions, and nonowner text never grant it.
        disclosure = bool(owner and event.is_group and (self.config.owner_group_replies or event.text.casefold().startswith('@sebastian share-here:')))
        destination = Destination(event.channel, event.account_id, event.conversation_id, event.thread_id)
        if owner and event.is_group and not disclosure:
            destination = next(d for d in self.config.owner_destinations if d.channel == event.channel and d.account_id == event.account_id)
        audience = {'destination': asdict(destination), 'is_group': event.is_group and (not owner or disclosure), 'disclosure': disclosure}
        session = [event.channel, event.account_id, event.conversation_id, event.thread_id,
                   event.sender_id, access, self.config.permission_revision, self._config_fingerprint, audience]
        session_key = hashlib.sha256(json.dumps(session, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        context = self._context(event)
        # Do not forward the original unfiltered history to the runtime.
        clean_event = replace(event, history=())
        return Decision(access, session_key, destination, context, clean_event, disclosure)

    def _context(self, event: Event) -> UntrustedContext:
        eligible = [h for h in event.history if (h.channel, h.account_id, h.conversation_id, h.thread_id) ==
                    (event.channel, event.account_id, event.conversation_id, event.thread_id)
                    and h.occurred_at <= event.occurred_at and h.event_id != event.event_id]
        eligible.sort(key=lambda h: (h.occurred_at, h.event_id), reverse=True)
        limit = 10 if event.channel == Channel.SLACK else self.config.messages_history_limit
        chosen = []
        seen = set()
        remaining = self.config.context_char_limit
        for item in eligible:
            if len(chosen) >= limit or remaining == 0:
                break
            if item.event_id in seen:
                continue
            seen.add(item.event_id)
            text = item.text[:remaining]
            remaining -= len(text)
            chosen.append(replace(item, text=text))
        return UntrustedContext(tuple(reversed(chosen)))
