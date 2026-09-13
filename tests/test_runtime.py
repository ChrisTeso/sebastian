import queue,threading,unittest
from collections import deque
from pathlib import Path
from sebastian.runtime import AppServer
from sebastian.runtime_policy import make_runtime_policy

class RuntimeTransportTests(unittest.TestCase):
    def client(self,events):
        client=object.__new__(AppServer)
        client._queue=queue.Queue();client._pending=deque();client._id=0;client._active=None;client._lock=threading.RLock();client._sessions={}
        client.sent=[];client._send=client.sent.append
        for event in events:client._queue.put(event)
        return client
    def test_completion_before_turn_start_response_is_not_lost(self):
        profile=make_runtime_policy('conversation',Path('/tmp'),{})
        client=self.client([
          {'id':1,'result':{'thread':{'id':'thread'}}},
          {'id':2,'result':{'data':[]}},
          {'method':'item/completed','params':{'threadId':'thread','turnId':'turn','item':{'type':'agentMessage','phase':'final_answer','text':'answer'}}},
          {'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}},
          {'id':3,'result':{'turn':{'id':'turn'}}}])
        self.assertEqual(client.respond('scope',profile,'hello',timeout=.1).text,'answer')
    def test_denied_tool_call_is_rejected_outside_model(self):
        profile=make_runtime_policy('conversation',Path('/tmp'),{})
        client=self.client([]);client._active=('thread','turn',profile,None)
        client._server_request({'id':99,'method':'item/tool/call','params':{'threadId':'thread','turnId':'turn','tool':'shell','arguments':{'command':'cat private'}}})
        self.assertFalse(client.sent[0]['result']['success'])
    def test_unexpected_mcp_exposure_blocks_before_turn(self):
        profile=make_runtime_policy('conversation',Path('/tmp'),{})
        client=self.client([{'id':1,'result':{'thread':{'id':'thread'}}},{'id':2,'result':{'data':[{'tools':{'private':{}}}]}}])
        with self.assertRaises(RuntimeError):client.respond('scope',profile,'hello',timeout=.1)
        self.assertNotIn('turn/start',[v.get('method') for v in client.sent])
