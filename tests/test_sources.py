from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import tempfile,unittest
from sebastian.contracts import Channel
from sebastian.engine import Sources
from sebastian.ledger import Ledger,JobMetadata
from sebastian.slack import SlackConfig,SlackAPI
from sebastian.policy import AdapterRegistry
from sebastian.images import ConversationImages

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.ledger=Ledger(self.root/'ledger.sqlite');self.addCleanup(self.ledger.close)
        self.source=object.__new__(Sources);self.source.ledger=self.ledger;self.source.on_cancel=Mock()
        self.source.config=SlackConfig('TEAM','BOTUSER','BOT','APP','OWNER',datetime.now(timezone.utc)-timedelta(seconds=10))
    def test_socket_accepts_only_metadata_and_exact_origin(self):
        ts=str(datetime.now(timezone.utc).timestamp())[:17]
        payload={'type':'event_callback','team_id':'TEAM','api_app_id':'APP','event':{'type':'message','channel_type':'im','channel':'DM','user':'OWNER','ts':ts,'text':'SYNTHETIC_PRIVATE_BODY'}}
        self.source.accept_slack(payload);self.source.accept_slack(payload)
        self.assertEqual(self.ledger.stats()['jobs'],{'queued':1})
        self.assertNotIn(b'SYNTHETIC_PRIVATE_BODY',(self.root/'ledger.sqlite').read_bytes())
        payload['team_id']='OTHER';self.source.accept_slack(payload)
        self.assertEqual(self.ledger.stats()['jobs'],{'queued':1})
    def test_thread_rehydration_uses_thread_api(self):
        # Thread candidates must reach the worker for trusted parent validation.
        now=datetime.now(timezone.utc);ts='1800000000.000001';root='1800000000.000000'
        job=self.ledger.accept(JobMetadata(Channel.SLACK,'TEAM','GROUP',root,ts,now,now))
        client=Mock();client.conversations_replies.return_value={'ok':True,'messages':[{'type':'message','ts':ts,'thread_ts':root,'user':'OWNER','text':'request'}]}
        self.source.api=SimpleNamespace(client=client);self.source._slack_event=Mock(return_value=None)
        self.assertEqual(self.source.fetch(job),(None,[]))
        client.conversations_replies.assert_called_once();client.conversations_history.assert_not_called()
    def test_nonowner_thread_post_without_mention_is_not_queued(self):
        ts=f'{datetime.now(timezone.utc).timestamp():.6f}'
        payload={'type':'event_callback','team_id':'TEAM','api_app_id':'APP','event':{
            'type':'message','channel_type':'channel','channel':'GROUP','user':'OTHER',
            'ts':ts,'thread_ts':'100.000001','text':'explain that'}}
        self.source.accept_slack(payload)
        self.assertEqual(self.ledger.stats()['jobs'],{})
        self.source.on_cancel.assert_not_called()
    def test_unverified_thread_cancel_does_not_interrupt(self):
        ts=f'{datetime.now(timezone.utc).timestamp():.6f}'
        self.source.accept_slack({'type':'event_callback','team_id':'TEAM','api_app_id':'APP','event':{
            'type':'message','channel_type':'channel','channel':'GROUP','user':'OWNER',
            'ts':ts,'thread_ts':'100.000001','text':'stop'}})
        self.source.on_cancel.assert_not_called()
    def test_changed_account_does_not_rehydrate_old_namespace(self):
        now=datetime.now(timezone.utc)
        job=self.ledger.accept(JobMetadata(Channel.SLACK,'OLDTEAM','GROUP',None,'EVENT',now,now))
        self.source.api=Mock()
        self.assertEqual(self.source.fetch(job),(None,[]));self.source.api.assert_not_called()

    def test_transient_image_failure_does_not_poison_rehydration_retry(self):
        now=datetime.now(timezone.utc);ts=f'{now.timestamp():.6f}'
        job=self.ledger.accept(JobMetadata(Channel.SLACK,'TEAM','DM',None,ts,now,now))
        client=Mock()
        row={'type':'message','ts':ts,'user':'OWNER','text':'image question','files':[{'id':'F','mimetype':'image/png','size':20}]}
        client.conversations_history.return_value={'ok':True,'messages':[row]}
        client.conversations_info.return_value={'ok':True,'channel':{'id':'DM','is_im':True,'user':'OWNER'}}
        self.source.api=SlackAPI(client,self.source.config);self.source.api.verified=True
        registry=AdapterRegistry();self.source.slack_signer=registry.register('slack',Channel.SLACK,'TEAM');self.source.bot='synthetic-token'
        with patch.object(ConversationImages,'_slack',side_effect=[RuntimeError('transient provider failure'),b'\x89PNG\r\n\x1a\nfixture']):
            with self.assertRaises(RuntimeError):self.source.fetch(job)
            envelope,images=self.source.fetch(job)
        self.assertIsNotNone(envelope);self.assertEqual(len(images),1)
        self.assertEqual(registry.authenticate(envelope).envelope.event.event_id,ts)
