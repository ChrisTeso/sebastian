import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from sebastian.contracts import Channel, Destination
from sebastian.policy import AdapterRegistry
from sebastian.slack import SlackAdapter, SlackAPI, SlackConfig, SocketReceiver


class Response(dict):
    headers = {'x-oauth-scopes': 'chat:write,im:history'}


class Client:
    def __init__(self):
        self.rows, self.sent = [], []
        self.info = {'id': 'D1', 'is_im': True, 'user': 'OWNER'}
        self.identity = Response(ok=True, team_id='T1', user_id='BOT', bot_id='B1')
    def auth_test(self): return self.identity
    def bots_info(self, **kwargs): return dict(ok=True, bot=dict(id='B1', user_id='BOT', app_id='A1'))
    def conversations_info(self, **kwargs): return dict(ok=True, channel=self.info)
    def conversations_history(self, **kwargs): return dict(ok=True, messages=self.rows)
    def conversations_replies(self, **kwargs): return dict(ok=True, messages=self.rows)
    def chat_postMessage(self, **kwargs):
        self.sent.append(kwargs)
        return dict(ok=True, channel=kwargs['channel'], ts='200.000001')


class SlackTests(unittest.TestCase):
    def setUp(self):
        self.config = SlackConfig('T1', 'BOT', 'B1', 'A1', 'OWNER', datetime.fromtimestamp(100, timezone.utc))
        self.client = Client()
        self.api = SlackAPI(self.client, self.config)
        self.api.inspect_identity()
        self.registry = AdapterRegistry()
        self.adapter = SlackAdapter(self.config, self.api, self.registry.register('slack', Channel.SLACK, 'T1'))
    def payload(self, **kwargs):
        row = dict(type='message', user='OWNER', channel='D1', text='hello', ts='101.000001')
        row.update(kwargs)
        return dict(type='event_callback', team_id='T1', api_app_id='A1', event=row, event_id='Ev1')
    def event(self, **kwargs):
        envelope = self.adapter.receive(self.payload(**kwargs))
        return self.registry.verified_event(self.registry.authenticate(envelope)) if envelope else None
    def group(self): self.client.info = {'id': 'D1', 'is_im': False, 'is_mpim': True}
    def test_identity_scopes(self):
        self.assertEqual(self.api.inspect_identity()['scopes'], ('chat:write', 'im:history'))
        self.client.identity['user_id'] = 'OWNER'
        with self.assertRaises(PermissionError): self.api.inspect_identity()
        self.assertFalse(self.api.verified)
    def test_wrong_installed_app_rejected(self):
        self.client.bots_info = lambda **kwargs: dict(ok=True, bot=dict(id='B1', user_id='BOT', app_id='WRONG'))
        with self.assertRaises(PermissionError): self.api.inspect_identity()
        self.assertFalse(self.api.verified)
    def test_owner_dm(self):
        e = self.event()
        self.assertEqual((e.sender_id, e.account_id, e.event_id), ('OWNER', 'T1', '101.000001'))
        self.assertFalse(e.is_group)
    def test_nonowner_dm_requires_mention(self):
        self.client.info['user'] = 'OTHER'
        self.assertIsNone(self.event(user='OTHER'))
        self.assertIsNotNone(self.event(user='OTHER', text='@SeBaStIaN hi'))
    def test_group_mentions(self):
        self.group()
        self.assertIsNone(self.event())
        self.assertIsNone(self.event(text='@sebastianExtra'))
        self.assertIsNotNone(self.event(text='Hi @SEBASTIAN'))
    def test_native_mention_thread(self):
        self.group()
        e = self.event(type='app_mention', text='<@BOT> help', thread_ts='99.000001')
        self.assertEqual(e.thread_id, '99.000001')
        self.assertTrue(e.is_group)
    def test_reply_in_sebastian_thread_without_mention_is_ignored(self):
        self.group()
        self.client.rows = [dict(user='BOT', bot_id='B1', subtype='bot_message', ts='90.000001', text='reply')]
        e = self.event(user='OTHER', text='explain that', thread_ts='90.000001')
        self.assertIsNone(e)
    def test_reply_after_sebastian_in_existing_thread(self):
        self.group()
        self.client.rows = [dict(user='BOT', bot_id='B1', ts='99.000001', thread_ts='90.000001', text='reply')]
        self.assertIsNone(self.event(user='OTHER', thread_ts='90.000001'))
    def test_ordinary_thread_posts_ignore_bot_participation_for_any_sender(self):
        self.group()
        self.client.rows = [dict(user='BOT', bot_id='B1', ts='99.000001', thread_ts='90.000001', text='reply')]
        for sender in ('OWNER', 'OTHER'):
            for text in ('rofl', '@LeRoy help', 'another thought'):
                self.assertIsNone(self.event(user=sender, text=text, thread_ts='90.000001'))
        self.assertIsNotNone(self.event(user='OTHER', text='<@BOT> help', thread_ts='90.000001'))
    def test_reply_requires_prior_exact_bot_and_thread(self):
        self.group()
        for changes in (dict(user='OTHER'), dict(bot_id='FOREIGN'), dict(thread_ts='80.000001'), dict(ts='102.000001')):
            prior = dict(user='BOT', bot_id='B1', ts='99.000001', thread_ts='90.000001', text="– Sebastian, Chris's AI Assistant")
            prior.update(changes)
            self.client.rows = [prior]
            self.assertIsNone(self.event(user='OTHER', thread_ts='90.000001'))
        self.client.rows = []
        self.assertIsNone(self.event(user='OTHER', thread_ts='90.000001'))
    def test_foreign_workspace_app(self):
        for field in ('team_id', 'api_app_id'):
            p = self.payload(); p[field] = 'WRONG'
            self.assertIsNone(self.adapter.receive(p))
    def test_filter_duplicate_self_subtype_backlog(self):
        for kw in (dict(user='BOT'), dict(bot_id='B1'), dict(subtype='message_changed'), dict(ts='99.000001')):
            self.assertIsNone(self.event(**kw))
        self.assertIsNotNone(self.event())
        self.assertIsNone(self.event(type='app_mention', text='<@BOT> duplicate'))
    def test_attachment_reference(self):
        e = self.event(subtype='file_share', files=[dict(id='F1', mimetype='image/png', size=100, url_private='https://private')])
        self.assertEqual(e.attachments[0].reference, 'F1')
        self.assertNotIn('private', repr(e))
    def test_exact_thread_predecessors(self):
        self.group()
        self.client.rows = [dict(user='OTHER', ts=f'{n}.000001', thread_ts='80.000001', text=str(n)) for n in range(81, 105)]
        self.client.rows += [dict(user='OTHER', ts='99.999999', text='wrong thread'), dict(user='OTHER', ts='80.000001', text='root')]
        e = self.event(text='@sebastian', thread_ts='80.000001')
        self.assertEqual(len(e.history), 10)
        self.assertEqual([h.text for h in e.history], [str(n) for n in range(91, 101)])
    def test_unthreaded_ignores_other_threads(self):
        self.client.rows = [dict(user='OTHER', ts='99.000001', thread_ts='90.000001', text='secret'), dict(user='OWNER', ts='98.000001', text='hello')]
        self.assertEqual([h.text for h in self.event().history], ['hello'])
    def test_send_bot_same_destination(self):
        self.api.send(Destination(Channel.SLACK, 'T1', 'D1', '90.000001'), 'final')
        self.assertEqual(self.client.sent, [dict(channel='D1', thread_ts='90.000001', text='final', unfurl_links=False, unfurl_media=False)])
        with self.assertRaises(PermissionError): self.api.send(Destination(Channel.SLACK, 'T2', 'D1'), 'bad')
    def test_socket_accept_before_ack(self):
        order=[]
        client=SimpleNamespace(socket_mode_request_listeners=[], send_socket_mode_response=lambda r:order.append(('ack',r)))
        receiver=SocketReceiver(client, lambda p:order.append(('queue',p)), response_factory=lambda **kw:kw)
        receiver._receive(client, SimpleNamespace(type='events_api', payload={'event':'fixture'}, envelope_id='env1'))
        self.assertEqual([x[0] for x in order], ['queue','ack'])
    def test_incomplete_history_fails_closed(self):
        self.client.conversations_history=lambda **kwargs: dict(ok=True, messages=[], has_more=True)
        with self.assertRaises(RuntimeError): self.event()
        self.assertEqual(self.adapter._seen, set())
    def test_mismatched_dm_user_rejected(self):
        self.client.info['user']='OTHER'
        self.assertIsNone(self.event())


if __name__ == '__main__': unittest.main()
