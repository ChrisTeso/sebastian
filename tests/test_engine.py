from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile,unittest
from unittest.mock import patch
from sebastian.contracts import Channel,Destination,Event
from sebastian.policy import AdapterRegistry,PolicyConfig
from sebastian.ledger import Ledger,JobMetadata
from sebastian.engine import Engine
from sebastian.images import ImageUnavailable

class FakeRuntime:
    def __init__(self):self.calls=[];self.images=[];self.answer='final answer';self.fail=False
    def stage_images(self,images):self.images=list(images)
    def respond(self,*args):
        self.calls.append((args,list(self.images)));self.images=[]
        if self.fail:raise RuntimeError('private failure fixture')
        return self.answer
    def cancel(self):return True
    def close(self):pass
class FakeSources:
    def __init__(self,event):
        self.registry=AdapterRegistry();self.signer=self.registry.register('fixture',event.channel,event.account_id)
        self.event=event;self.sent=[];self.fetch_error=None;self.send_error=False;self.images=[]
    def fetch(self,job):
        if self.fetch_error:raise self.fetch_error
        return self.signer.sign(self.event),self.images
    def send(self,destination,text):
        self.sent.append((destination,text))
        if self.send_error:raise RuntimeError('transport fixture')
        return 'provider-receipt'
    def close(self):pass

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.event=Event(Channel.SLACK,'TEAM','OWNER','GROUP','THREAD','EVENT',datetime.now(timezone.utc),'private request fixture',is_group=True)
        self.sources=FakeSources(self.event);self.runtime=FakeRuntime()
        policy=PolicyConfig(owner_slack=frozenset({('TEAM','OWNER')}),owner_group_replies=True,owner_destinations=(Destination(Channel.SLACK,'TEAM','DM'),))
        self.settings=SimpleNamespace(policy=policy,state_dir=root,owner_workspace=root,restricted_workspace=root)
        self.ledger=Ledger(root/'state.sqlite')
        with patch('sebastian.engine.load_user_config',return_value={}):self.engine=Engine(self.settings,self.runtime,ledger=self.ledger,sources=self.sources)
        self.addCleanup(self.engine.close)
    def accept(self):return self.ledger.accept(JobMetadata.from_event(self.event))
    def test_final_only_samegroup_images_and_duplicate(self):
        self.sources.images=['synthetic-image'];job=self.accept();self.accept()
        self.assertTrue(self.engine.run_once());self.assertFalse(self.engine.run_once())
        self.assertEqual(len(self.runtime.calls),1);self.assertEqual(self.runtime.calls[0][1],['synthetic-image'])
        self.assertEqual(self.sources.sent,[(Destination(Channel.SLACK,'TEAM','GROUP','THREAD'),"final answer\n\n– Sebastian, Chris's AI Assistant")])
        self.assertEqual(self.ledger.deliveries(job.job_id)[0].status,'confirmed')
        self.assertNotIn(b'private request fixture',Path(self.settings.state_dir/'state.sqlite').read_bytes())
        self.assertNotIn(b'final answer',Path(self.settings.state_dir/'state.sqlite').read_bytes())
    def test_runtime_failure_is_actionable_never_replayed(self):
        self.runtime.fail=True;self.accept();self.engine.run_once();self.engine.run_once()
        self.assertEqual(len(self.runtime.calls),1)
        self.assertIn('did not replay',self.sources.sent[0][1])
        self.assertNotIn('private failure fixture',self.sources.sent[0][1])
    def test_provider_uncertainty_is_recorded_not_retried(self):
        self.sources.send_error=True;job=self.accept();self.engine.run_once();self.engine.run_once()
        self.assertEqual(len(self.sources.sent),1)
        self.assertEqual(self.ledger.deliveries(job.job_id)[0].status,'uncertain')
    def test_crash_running_yields_failure_without_model(self):
        job=self.accept();self.ledger.claim();self.ledger.start(job.job_id);self.ledger.recover()
        self.engine.run_once();self.assertEqual(self.runtime.calls,[])
        self.assertIn('interrupted',self.sources.sent[0][1])
    def test_missing_image_failure_does_not_ask_model_to_guess(self):
        self.sources.fetch_error=ImageUnavailable('private attachment path');self.accept();self.engine.run_once()
        self.assertEqual(self.runtime.calls,[]);self.assertIn('attached image',self.sources.sent[0][1])
    def test_retrieval_failure_only_retries_before_runtime(self):
        self.sources.fetch_error=RuntimeError('private provider detail');job=self.accept();self.engine.run_once()
        self.assertEqual(self.ledger.get_job(job.job_id).status,'queued');self.assertEqual(self.runtime.calls,[]);self.assertEqual(self.sources.sent,[])

    def test_confirmed_response_crash_does_not_send_failure_again(self):
        job=self.accept();self.ledger.claim();self.ledger.start(job.job_id)
        d=self.ledger.plan(job.job_id,Destination(Channel.SLACK,'TEAM','GROUP','THREAD'),'delivered',0)
        self.ledger.attempting(d.part_id);self.ledger.confirmed(d.part_id,'receipt');self.ledger.recover()
        self.engine.run_once()
        self.assertEqual(self.sources.sent,[]);self.assertEqual(self.runtime.calls,[])
        self.assertEqual(self.ledger.get_job(job.job_id).status,'complete')
    def test_partial_send_abandons_remaining_without_retry(self):
        self.runtime.answer='long result '*900;self.sources.send_error=True;job=self.accept();self.engine.run_once()
        statuses=[d.status for d in self.ledger.deliveries(job.job_id)]
        self.assertEqual(statuses[0],'uncertain');self.assertTrue(all(s=='abandoned' for s in statuses[1:]))
        self.assertEqual(len(self.sources.sent),1)
