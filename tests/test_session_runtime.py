import json,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from sebastian.runtime import RuntimeFailure
from sebastian.runtime_policy import make_runtime_policy
from sebastian.session_runtime import SessionRuntime

class FakeConnection:
    instances=[];running=0;maximum=0
    def __init__(self,owner):
        self.owner=owner;self.process=self;self.dead=False;self.images=[];self.__class__.instances.append(self)
    def poll(self):return 1 if self.dead else None
    def close(self):self.dead=True
    def respond(self,key,profile,prompt,reader,timeout):
        type(self).running+=1;type(self).maximum=max(type(self).maximum,type(self).running)
        try:
            self.images.append(list(self.owner._images))
            self.owner._ids[self.owner._scope]='synthetic-thread';self.owner._save_ids()
            if prompt=='cancel':
                while not self.owner._cancel.wait(.01):pass
                raise RuntimeFailure('canceled')
            if prompt=='failure':raise RuntimeFailure('failed')
            time.sleep(.01);return 'final'
        finally:type(self).running-=1

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'private/sessions.json'
        self.p=make_runtime_policy('conversation',Path('/tmp'),{})
        self.patch=patch('sebastian.session_runtime._Connection',FakeConnection);self.patch.start()
        FakeConnection.instances=[];FakeConnection.maximum=0
        self.r=SessionRuntime(self.path)
    def tearDown(self):self.r.close();self.patch.stop();self.temp.cleanup()
    def test_one_shot_images_failure_and_metadata(self):
        self.r.stage_images(['data:image/png;base64,AAAA'])
        with self.assertRaises(RuntimeFailure):self.r.respond('private-channel-key',self.p,'failure')
        self.assertEqual(self.r.respond('private-channel-key',self.p,'secret text'),'final')
        self.assertEqual(FakeConnection.instances[-1].images,[[]])
        text=self.path.read_text();self.assertNotIn('private-channel-key',text);self.assertNotIn('secret text',text)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.path.parent.stat().st_mode&0o777,0o700)
    def test_concurrent_serialization_and_thread_local_images(self):
        barrier=threading.Barrier(2)
        def run(image):
            self.r.stage_images([image]);barrier.wait();self.r.respond(image,self.p,'hello')
        images=['data:image/png;base64,AAAA','data:image/png;base64,BBBB']
        threads=[threading.Thread(target=run,args=(i,)) for i in images]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(FakeConnection.maximum,1)
        self.assertCountEqual(FakeConnection.instances[0].images,[[i] for i in images])
    def test_cancel_and_explicit_next_request(self):
        errors=[]
        def run():
            try:self.r.respond('same',self.p,'cancel')
            except RuntimeFailure as e:errors.append(str(e))
        t=threading.Thread(target=run);t.start()
        while not FakeConnection.instances:time.sleep(.001)
        self.assertTrue(self.r.cancel());t.join(2);self.assertFalse(t.is_alive());self.assertEqual(errors,['canceled'])
        self.assertEqual(self.r.respond('same',self.p,'new'),'final');self.assertFalse(self.r.cancel())
    def test_restart_metadata_and_profile_separation(self):
        self.r.respond('key',self.p,'one');self.r.close();self.r=SessionRuntime(self.path)
        self.assertEqual(len(self.r._ids),1)
        self.r.respond('key',make_runtime_policy('owner',Path('/tmp'),{}),'two')
        self.assertEqual(len(self.r._ids),2)

class NativeAdapterTests(unittest.TestCase):
    def test_image_schema_and_resume_fields(self):
        from sebastian.session_runtime import _Connection
        from types import SimpleNamespace
        calls=[]
        owner=SimpleNamespace(_ids={'scope':'saved'},_scope='scope',_save_ids=lambda:None,_images=['data:image/png;base64,AAAA'],model=None)
        connection=object.__new__(_Connection);connection.owner=owner
        def request(client,method,params,timeout):
            calls.append((method,params));return {'thread':{'id':'saved'}}
        with patch('sebastian.runtime.AppServer._request',request):
            connection._request('thread/start',{'cwd':'/tmp','environments':[],'sandbox':'read-only','ephemeral':False})
            connection._request('turn/start',{'threadId':'saved','input':[{'type':'text','text':'describe'}]})
        self.assertEqual(calls[0],('thread/resume',{'cwd':'/tmp','sandbox':'read-only','threadId':'saved'}))
        self.assertEqual(calls[1][1]['input'][-1],{'type':'image','url':'data:image/png;base64,AAAA'})
    def test_cancellation_next_uses_no_extra_rpc_reader(self):
        from sebastian.session_runtime import _Connection
        from types import SimpleNamespace
        import queue
        connection=object.__new__(_Connection)
        connection.owner=SimpleNamespace(_deadline=time.monotonic()+5,_interrupting=False,_cancel=threading.Event())
        connection._queue=queue.Queue();connection.owner._cancel.set()
        with self.assertRaisesRegex(RuntimeFailure,'canceled'):connection._next(time.monotonic()+5)
