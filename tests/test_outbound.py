import base64,tempfile,unittest
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from sebastian.outbound import OutboundImage,RuntimeReply
from sebastian.images import ImageUnavailable
from sebastian.runtime import RuntimeFailure
from sebastian.runtime_policy import make_runtime_policy
from sebastian.contracts import Channel,Destination,Event
import test_runtime
import test_engine

PNG=b'\x89PNG\r\n\x1a\nsynthetic-private-image'

class ImageRuntimeTests(unittest.TestCase):
    def run_reply(self,items,status='completed'):
        start=[{'id':1,'result':{'thread':{'id':'thread'}}},{'id':2,'result':{'data':[]}}, {'id':3,'result':{'turn':{'id':'turn'}}}]
        events=start+[{'method':'item/completed','params':{'threadId':thread,'turnId':turn,'item':item}} for thread,turn,item in items]
        events.append({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':status}}})
        client=test_runtime.RuntimeTransportTests().client(events)
        with patch.object(client,'_request',wraps=client._request):
            # Avoid waiting for an interrupt acknowledgment in failure fixtures.
            request=client._request
            client._request=lambda method,*a,**k: {} if method=='turn/interrupt' else request(method,*a,**k)
            return client.respond('scope',make_runtime_policy('conversation',Path('/tmp'),{}),'image',timeout=.1)
    def item(self,**kwargs):return dict(type='imageGeneration',id='image',status='completed',result=base64.b64encode(PNG).decode(),**kwargs)
    def test_image_only_and_duplicate_notifications(self):
        i=self.item();reply=self.run_reply([('thread','turn',i),('thread','turn',i)])
        self.assertEqual(reply.text,'');self.assertEqual(len(reply.images),1);self.assertEqual(reply.images[0].data,PNG)
        self.assertNotIn('private',repr(reply))
    def test_input_screenshots_and_other_turns_not_exported(self):
        reply=self.run_reply([('other','turn',self.item()),('thread','old',self.item()),('thread',None,self.item()),('thread','turn',{'type':'imageView','path':'/private/image.png'}),('thread','turn',{'type':'agentMessage','phase':'final_answer','text':'done'})])
        self.assertEqual(reply.images,());self.assertEqual(reply.text,'done')
    def test_failed_turn_does_not_export_completed_image(self):
        with self.assertRaises(RuntimeFailure):self.run_reply([('thread','turn',self.item())],status='failed')
    def test_invalid_and_oversized_images(self):
        for value in ['not-base64','data:image/png;base64,AAAA','A'*11_000_000]:
            with self.assertRaises(ImageUnavailable):OutboundImage.from_result(value)

class ImageEngineTests(unittest.TestCase):
    setUp=test_engine.EngineTests.setUp
    accept=test_engine.EngineTests.accept
    def test_image_only_uses_original_thread_and_metadata_ledger(self):
        image=OutboundImage(PNG,'image/png');self.runtime.answer=RuntimeReply(images=(image,))
        sent=[];self.sources.send_image=lambda d,i:sent.append((d,i)) or 'FILE123'
        job=self.accept();self.engine.run_once();self.engine.run_once()
        self.assertEqual(sent,[(Destination(Channel.SLACK,'TEAM','GROUP','THREAD'),image)])
        self.assertEqual(self.sources.sent[0][1],"– Sebastian, Chris's AI Assistant")
        self.assertEqual(self.ledger.deliveries(job.job_id)[-1].receipt,'FILE123')
        for p in Path(self.settings.state_dir).glob('state.sqlite*'):self.assertNotIn(PNG,p.read_bytes())
    def test_image_failure_abandons_remaining_and_never_retries(self):
        self.runtime.answer=RuntimeReply('caption',(OutboundImage(PNG,'image/png'),OutboundImage(PNG,'image/png')))
        calls=[]
        def fail(d,i):calls.append(i);raise RuntimeError('private provider error')
        self.sources.send_image=fail;job=self.accept();self.engine.run_once();self.engine.run_once()
        self.assertEqual([d.status for d in self.ledger.deliveries(job.job_id)],['confirmed','uncertain','abandoned'])
        self.assertEqual(len(calls),1)
    def test_image_echo_marker_does_not_suppress_unrelated_empty_text(self):
        image=OutboundImage(PNG,'image/png');job=self.accept();self.ledger.claim();self.ledger.start(job.job_id)
        d=Destination(Channel.MESSAGES,'ACCOUNT','CHAT')
        planned=self.ledger.plan(job.job_id,d,image.marker,0);self.ledger.attempting(planned.part_id)
        event=Event(Channel.MESSAGES,'ACCOUNT','OWNER','CHAT',None,'OUTGOING',datetime.now(timezone.utc),'',is_from_me=True,owner_metadata_verified=True)
        self.assertFalse(self.ledger.is_self_reply(event));self.assertTrue(self.ledger.is_self_reply(event,image.marker))
        self.assertFalse(self.ledger.is_self_reply(replace(event,conversation_id='OTHER'),image.marker))
        self.ledger.confirmed(planned.part_id,event.event_id)
        self.assertTrue(self.ledger.is_self_reply(event))
