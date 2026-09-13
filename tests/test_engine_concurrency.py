import threading,unittest
from dataclasses import replace
from sebastian.ledger import JobMetadata
from sebastian.outbound import RuntimeReply
import test_engine

class ConcurrentEngineTests(unittest.TestCase):
    setUp=test_engine.EngineTests.setUp
    def test_reserved_image_worker_does_not_block_normal_and_fifo_holds(self):
        image=replace(self.event,conversation_id='IMAGE_CHAT',thread_id=None,event_id='image1',text='Generate an image of a robot.')
        normal=replace(self.event,conversation_id='NORMAL_CHAT',thread_id=None,event_id='normal',text='What is 7 + 8?')
        second=replace(image,event_id='image2')
        same=replace(image,event_id='same',text='Follow-up question')
        events={e.event_id:e for e in (image,normal,second,same)}
        self.sources.fetch=lambda job:(self.sources.signer.sign(events[job.metadata.event_id]),[])
        for e,lane in ((image,'image'),(second,'image'),(same,'normal'),(normal,'normal')):self.ledger.accept(JobMetadata.from_event(e),lane=lane)
        started=threading.Event();release=threading.Event();order=[]
        def respond(key,profile,prompt,reader):
            if 'IMAGE_CHAT' in key:pass
            if 'Generate an image' in prompt:started.set();release.wait(2)
            return RuntimeReply('done')
        self.runtime.respond=respond
        thread=threading.Thread(target=lambda: self.engine.run_once('image'));thread.start()
        self.assertTrue(started.wait(1))
        try:
            self.assertTrue(self.engine.run_once('normal'));self.assertFalse(self.engine.run_once('normal'))
            self.assertEqual(self.sources.sent[0][0].conversation_id,'NORMAL_CHAT')
            self.assertTrue(thread.is_alive())
        finally:release.set();thread.join(2)
        self.assertEqual([x[0].conversation_id for x in self.sources.sent],['NORMAL_CHAT','IMAGE_CHAT'])
