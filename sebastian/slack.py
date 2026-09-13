"""Fresh Slack bot boundary. Only trusted Socket Mode callbacks may feed ingress.

Tokens, texts and attachment URLs are never logged or written here. Durable queue,
restart checkpoints and bounded reconnect supervision belong to the service.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re
from threading import RLock
from typing import Callable

from .contracts import AttachmentRef, Channel, Destination, Event, HistoryMessage, identifier, timestamp
from .policy import AdapterSigner, Envelope


def slack_time(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d{1,6}', value):
        raise ValueError('invalid Slack timestamp')
    return datetime.fromtimestamp(float(Decimal(value)), timezone.utc)


@dataclass(frozen=True)
class SlackConfig:
    team_id: str
    bot_user_id: str
    bot_id: str
    app_id: str
    owner_user_id: str
    started_at: datetime

    def __post_init__(self):
        for value in (self.team_id, self.bot_user_id, self.bot_id, self.app_id, self.owner_user_id):
            identifier(value)
        timestamp(self.started_at)


class SlackAPI:
    """Inject a slack_sdk WebClient authenticated with the existing bot token."""
    def __init__(self, client, config: SlackConfig):
        self.client, self.config = client, config
        self.verified = False

    def inspect_identity(self) -> dict:
        self.verified = False
        result = self.client.auth_test()
        if not result.get('ok') or (result.get('team_id'), result.get('user_id'), result.get('bot_id')) != (
            self.config.team_id, self.config.bot_user_id, self.config.bot_id):
            raise PermissionError('Slack bot identity mismatch')
        bot_response = self.client.bots_info(bot=self.config.bot_id)
        bot = bot_response.get('bot', {})
        if not bot_response.get('ok') or (bot.get('id'), bot.get('user_id'), bot.get('app_id')) != (
            self.config.bot_id, self.config.bot_user_id, self.config.app_id):
            raise PermissionError('Slack app identity mismatch')
        self.verified = True
        headers = getattr(result, 'headers', {})
        raw = next((v for k, v in headers.items() if k.lower() == 'x-oauth-scopes'), '')
        if isinstance(raw, list):
            raw = ','.join(raw)
        return {'team_id': result['team_id'], 'user_id': result['user_id'], 'bot_id': result['bot_id'], 'app_id': bot['app_id'],
                'scopes': tuple(sorted(s.strip() for s in raw.split(',') if s.strip())),
                'scopes_observed': bool(raw)}

    def conversation(self, channel: str) -> dict:
        if not self.verified:
            raise PermissionError('Slack identity inspection required')
        response = self.client.conversations_info(channel=channel)
        if not response.get('ok') or response.get('channel', {}).get('id') != channel:
            raise PermissionError('Slack conversation unavailable')
        return response['channel']

    def history(self, channel: str, thread: str | None, before: str) -> list[dict]:
        """Fetch bounded pages, then select exact nearest predecessors in adapter.

        Failure/incomplete pagination raises; never silently substitutes another
        channel/thread or claims an incomplete page is exact preceding history.
        """
        if not self.verified:
            raise PermissionError('Slack identity inspection required')
        cursor, messages = '', []
        for _ in range(20):
            args = dict(channel=channel, latest=before, inclusive=False, limit=100)
            if cursor:
                args['cursor'] = cursor
            if thread:
                response = self.client.conversations_replies(ts=thread, **args)
            else:
                response = self.client.conversations_history(**args)
            if not response.get('ok'):
                raise RuntimeError('Slack history unavailable')
            messages.extend(response.get('messages', []))
            # Channel history is newest-first. Stop once the complete nearest
            # ten eligible unthreaded predecessors have been obtained. Replies
            # are oldest-first and therefore require all pages before slicing.
            if not thread:
                eligible = {m['ts'] for m in messages if m.get('ts') and m.get('user')
                            and not m.get('thread_ts') and m.get('subtype') in (None, 'file_share')
                            and slack_time(m['ts']) < slack_time(before)}
                if len(eligible) >= 10:
                    return messages
            cursor = response.get('response_metadata', {}).get('next_cursor', '')
            if not cursor and not response.get('has_more', False):
                return messages
            if not cursor:
                raise RuntimeError('Slack history pagination incomplete')
        raise RuntimeError('Slack history page budget exhausted')

    def send(self, destination: Destination, text: str) -> str:
        """Service-only final delivery, with policy-authorized destination."""
        if not self.verified or destination.channel != Channel.SLACK or destination.account_id != self.config.team_id:
            raise PermissionError('Slack delivery identity mismatch')
        if not isinstance(text, str) or not text.strip() or len(text) > 40_000:
            raise ValueError('invalid Slack response')
        args = dict(channel=destination.conversation_id, text=text, unfurl_links=False, unfurl_media=False)
        if destination.thread_id:
            args['thread_ts'] = destination.thread_id
        # No user token, username, icon, as_user, or destination inferred from text.
        result = self.client.chat_postMessage(**args)
        if not result.get('ok') or result.get('channel') != destination.conversation_id:
            raise RuntimeError('Slack delivery unconfirmed')
        return result['ts']

    def send_image(self, destination: Destination, image) -> str:
        """Upload validated image bytes to the policy-authorized conversation.

        The file ID confirms upload completion; it is not a message timestamp.
        Slack may omit shares in the completion response, particularly in threads.
        """
        if not self.verified or destination.channel != Channel.SLACK or destination.account_id != self.config.team_id:
            raise PermissionError('Slack delivery identity mismatch')
        if (not isinstance(image.data, bytes) or not 0 < len(image.data) <= 20_000_000
                or image.media_type not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
                or not isinstance(image.filename, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', image.filename)):
            raise ValueError('invalid Slack image')
        args = dict(channel=destination.conversation_id, file=image.data,
                    filename=image.filename, title=image.filename)
        if destination.thread_id:
            args['thread_ts'] = destination.thread_id
        try:
            result = self.client.files_upload_v2(**args)
        except Exception:
            # SDK errors can include upload URLs and file metadata.
            raise RuntimeError('Slack image delivery failed or is uncertain; reconcile before retry') from None
        files = result.get('files', [])
        if (not result.get('ok') or not isinstance(files, list) or len(files) != 1
                or not isinstance(files[0], dict) or not isinstance(files[0].get('id'), str)
                or not re.fullmatch(r'F[A-Z0-9]+', files[0]['id'])):
            raise RuntimeError('Slack image delivery unconfirmed; reconcile before retry')
        return files[0]['id']


def attachments(row: dict) -> tuple[AttachmentRef, ...]:
    # Use provider file IDs; resolve/download only in the authorized tool layer.
    refs = []
    for item in row.get('files', [])[:32]:
        if item.get('id'):
            refs.append(AttachmentRef(item['id'], item.get('mimetype') or 'application/octet-stream', item.get('size', 0)))
    return tuple(refs)


class SlackAdapter:
    def __init__(self, config: SlackConfig, api: SlackAPI, signer: AdapterSigner):
        if api.config != config:
            raise ValueError('Slack API configuration mismatch')
        self.config, self.api, self.signer = config, api, signer
        self._seen: set[tuple[str, str]] = set()
        self._lock = RLock()

    def _message(self, row: dict, channel: str, thread: str | None) -> HistoryMessage:
        return HistoryMessage(Channel.SLACK, self.config.team_id, row['user'], channel, thread,
                              row['ts'], slack_time(row['ts']), row.get('text', ''), attachments(row))

    def receive(self, payload: dict) -> Envelope | None:
        """Trusted provider payload only; never expose this method as a model tool."""
        with self._lock:
            return self._receive(payload)

    def _receive(self, payload: dict) -> Envelope | None:
        if not self.api.verified:
            raise PermissionError('Slack identity inspection required')
        if payload.get('type') != 'event_callback' or payload.get('team_id') != self.config.team_id or payload.get('api_app_id') != self.config.app_id:
            return None
        row = payload.get('event', {})
        if row.get('type') not in ('message', 'app_mention') or row.get('subtype') not in (None, 'file_share'):
            return None
        if row.get('bot_id') or row.get('user') == self.config.bot_user_id or not row.get('user'):
            return None
        channel, ts = row.get('channel'), row.get('ts')
        identifier(channel)
        occurred = slack_time(ts)
        if occurred <= self.config.started_at:
            return None
        key = (channel, ts)  # message + app_mention notifications share this ID.
        if key in self._seen:
            return None
        info = self.api.conversation(channel)
        direct = info.get('is_im') is True and info.get('is_mpim') is not True
        if direct and info.get('user') != row['user']:
            return None
        text = row.get('text', '')
        if not isinstance(text, str):
            raise ValueError('invalid Slack text')
        mentioned = bool(re.search(r'(?<![\w@])@sebastian\b', text, re.IGNORECASE)) or f'<@{self.config.bot_user_id}>' in text
        thread = row.get('thread_ts')
        if thread:
            slack_time(thread)
        if not (direct and row['user'] == self.config.owner_user_id) and not mentioned and not thread:
            return None
        base = self._message(row, channel, thread)
        history = {}
        replies_to_bot = False
        for prior in self.api.history(channel, thread, ts):
            # Thread roots omit thread_ts. A root is context for its own thread.
            prior_thread = prior.get('thread_ts')
            if thread and prior.get('ts') == thread:
                prior_thread = thread
            if prior_thread != thread or prior.get('subtype') not in (None, 'file_share', 'bot_message') or not prior.get('user'):
                continue
            item = self._message(prior, channel, thread)
            if item.occurred_at < occurred:
                history[item.event_id] = item
                if thread and prior.get('user') == self.config.bot_user_id and prior.get('bot_id') == self.config.bot_id:
                    replies_to_bot = True
        if not (direct and row['user'] == self.config.owner_user_id) and not mentioned and not replies_to_bot:
            return None
        context = tuple(sorted(history.values(), key=lambda h: (h.occurred_at, h.event_id))[-10:])
        event = Event(base.channel, base.account_id, base.sender_id, base.conversation_id, base.thread_id,
                      base.event_id, base.occurred_at, base.text, base.attachments,
                      is_group=not direct, history=context)
        signed = self.signer.sign(event)
        self._seen.add(key)
        return signed


class SocketReceiver:
    """Acknowledge promptly and enqueue via callback; no model work in listener.

    callback must accept provider payload into S5's queue. Feed adapter.receive
    only from that trusted queue. SDK automatic reconnection is disabled so S5
    can supervise bounded reconnect and shutdown explicitly.
    """
    def __init__(self, client, callback: Callable[[dict], None], response_factory=None):
        if response_factory is None:
            from slack_sdk.socket_mode.response import SocketModeResponse
            response_factory = SocketModeResponse
        self.client, self.callback, self.response_factory = client, callback, response_factory
        client.socket_mode_request_listeners.append(self._receive)

    def _receive(self, client, request):
        if request.type == 'events_api':
            # Accept into queue before ack so failed acceptance can be retried.
            self.callback(request.payload)
        client.send_socket_mode_response(self.response_factory(envelope_id=request.envelope_id))

    def connect(self):
        self.client.connect()

    def close(self):
        self.client.close()


def socket_client(app_token: str, bot_token: str):
    """Instantiate supported SDK; callers supply securely loaded existing auth."""
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    if not app_token.startswith('xapp-') or not bot_token.startswith('xoxb-'):
        raise ValueError('existing app and bot tokens required')
    web = WebClient(token=bot_token, timeout=10, retry_handlers=[])
    return SocketModeClient(app_token=app_token, web_client=web, auto_reconnect_enabled=False)
