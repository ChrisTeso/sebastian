import json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from sebastian.routed_runtime import RoutedRuntime
from sebastian.runtime_policy import make_runtime_policy
from sebastian.routing import request_lane,needs_image_history
from sebastian.outbound import RuntimeReply
from sebastian.runtime import RuntimeFailure

class FakeSession:
    def __init__(self,path,execution_lock=None,model=None):
        self.path=path;self.model=model;self._local=threading.local();self._connection=None;self.calls=[];self.last_timings={};self.execution_lock=execution_lock;self.cancelled=False;self.closed=False
        self.result=RuntimeReply('{"route":"answer","answer":"done"}') if model=='gpt-5.6-luna' else RuntimeReply('done')
    def stage_images(self,images):self._local.images=list(images)
    def respond(self,key,profile,prompt,reader,timeout,**options):
        self.calls.append({'key':key,'profile':profile,'prompt':prompt,'reader':reader,'timeout':timeout,'images':getattr(self._local,'images',[]),**options})
        if getattr(self,'hook',None):self.hook()
        if isinstance(self.result,Exception):raise self.result
        return self.result
    def cancel(self):self.cancelled=True;return True
    def close(self):self.closed=True

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        with patch('sebastian.routed_runtime.SessionRuntime',FakeSession),patch('sebastian.routed_runtime.load_user_config',return_value={'mcp_servers':{'private':{}},'plugins':{'private':{}},'apps':{'private':{}}}):self.r=RoutedRuntime(root/'sessions.json',root)
        self.addCleanup(self.r.close);self.owner=make_runtime_policy('owner',root,{})
    def call(self,text,profile=None,reader=None):return self.r.respond('key',profile or self.owner,json.dumps({'request':text}),reader,timeout=10)
    def route(self,to):self.r.luna.result=RuntimeReply(json.dumps({'route':to,'answer':''}))
    def test_luna_answers_without_second_model_or_tools(self):
        result=self.call('What is 7 + 8?')
        self.assertEqual(result.model,'Luna');self.assertFalse(self.r.normal.calls or self.r.sol.calls)
        call=self.r.luna.calls[0];p=call['profile'];params=p.thread_params()
        self.assertEqual(params['environments'],[]);self.assertEqual(params['selectedCapabilityRoots'],[])
        self.assertFalse(p.config['mcp_servers.private.enabled']);self.assertFalse(p.config['features.image_generation'])
        self.assertFalse(p.config['features.shell_tool']);self.assertIsNotNone(call['output_schema']);self.assertFalse(call['delivery_instructions'])
    def test_routes_preserve_original_permissions_and_reader(self):
        for choice,runtime in [('sol',self.r.sol),('astra',self.r.normal)]:
            self.route(choice);p=make_runtime_policy('toolbelt_read_only',Path(self.tmp.name),{});reader=object()
            result=self.call('Read the approved project document.',p,reader)
            self.assertIs(runtime.calls[-1]['profile'],p);self.assertIs(runtime.calls[-1]['reader'],reader)
            self.assertIsNone(self.r.luna.calls[-1]['reader']);self.assertEqual(result.routed_by,'Luna')
            self.assertEqual(result.model,choice.capitalize())
    def test_bad_router_falls_back_but_execution_failure_never_replays(self):
        self.r.luna.result=RuntimeReply('bad JSON');self.call('Hello')
        self.assertEqual(len(self.r.normal.calls),1);self.assertFalse(self.r.sol.calls)
        self.route('sol');self.r.sol.result=RuntimeFailure('tool may have run')
        with self.assertRaises(RuntimeFailure):self.call('Do a task')
        self.assertEqual(len(self.r.normal.calls),1)
    def test_cancel_during_route_never_escalates(self):
        self.route('sol');self.r.luna.hook=self.r.cancel
        with self.assertRaises(RuntimeFailure):self.call('Check calendar')
        self.assertFalse(self.r.sol.calls or self.r.normal.calls)
    def test_image_profile_removes_desktop_and_private_tools(self):
        result=self.call('Generate an image of a purple robot.')
        self.assertFalse(self.r.normal.calls or self.r.luna.calls)
        p=self.r.image.calls[0]['profile'];params=p.thread_params()
        self.assertEqual(params['environments'],[]);self.assertEqual(params['selectedCapabilityRoots'],[])
        self.assertEqual(params['sandbox'],'read-only');self.assertFalse(p.config['features.apps'])
        self.assertFalse(p.config['mcp_servers.private.enabled']);self.assertFalse(p.config['plugins.private.enabled'])
        self.assertFalse(p.config['apps.private.enabled']);self.assertEqual(result.model,'Sol')
    def test_image_lane_never_grants_nonowner_authority(self):
        self.route('sol');self.call('Generate an image of a robot.',make_runtime_policy('conversation',Path(self.tmp.name),{}))
        self.assertFalse(self.r.image.calls);self.assertEqual(self.r.sol.calls[0]['profile'].access,'conversation')
    def test_image_requiring_personal_tools_keeps_original_profile(self):
        self.route('sol')
        for text in ['Generate an image from my desktop file.','Create a picture based on that Slack message.','Edit the previous image.','Create a picture of a dog and remind me tomorrow to print it']:
            self.call(text)
        self.assertEqual(len(self.r.sol.calls),4);self.assertFalse(self.r.image.calls)
        self.assertTrue(all(c['profile'] is self.owner for c in self.r.sol.calls))
    def test_image_and_normal_calls_overlap_without_image_leak(self):
        started=threading.Event();release=threading.Event()
        self.r.image.hook=lambda:(started.set(),release.wait(2))
        def image():self.r.stage_images(['synthetic']);self.call('Generate an image of a robot.')
        t=threading.Thread(target=image);t.start();self.assertTrue(started.wait(1))
        try:self.call('What is 7 + 8?');self.assertTrue(t.is_alive())
        finally:release.set();t.join(2)
        self.assertEqual(self.r.luna.calls[0]['images'],[]);self.assertEqual(self.r.image.calls[0]['images'],['synthetic'])
    def test_ingress_conservative_lane_hints(self):
        self.assertEqual(request_lane('<@BOT123> Generate an image of a robot.',owner=True),'image')
        self.assertEqual(request_lane('Generate an image of a robot.',owner=True,attachments=True),'normal')
        self.assertEqual(request_lane('Generate an image of a robot.',owner=False),'normal')
    def test_trivial_text_does_not_reload_old_image_pixels(self):
        for text in ['What is 19 + 23?', 'Hello!', 'Reply with only OK']:self.assertFalse(needs_image_history(text))
        for text in ['What color is it?', 'What is 19 + 23 in that image?', 'Describe the picture', 'Reply with only the number of dogs', 'What is 2nd man wearing?']:self.assertTrue(needs_image_history(text))
