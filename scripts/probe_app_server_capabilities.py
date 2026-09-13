#!/usr/bin/env python3
"""Continue the S2 synthetic thread; exercise native tools and cancellation."""
import json,os,time
from probe_app_server import RPC,PRIVATE

def main():
 os.umask(0o077)
 thread=json.loads((PRIVATE/'app-server-thread.json').read_text())['thread_id'];rpc=RPC();results={}
 try:
  resumed=rpc.call('thread/resume',{'threadId':thread,'excludeTurns':True});results['resumed_same_thread']=resumed['thread']['id']==thread
  probes=[
   ('reconnect','Return only the conversation marker from our previous conversation, without reading any files or using tools.'),
   ('service_plugin','Use the connected GitHub plugin get_profile tool once to verify authentication. Do not use shell, curl, CLI, or extract credentials. Do not read repositories, messages, or private content. In your final answer return only AUTHENTICATED_PLUGIN_READ_PASS if the call succeeded, otherwise BLOCKED with the concrete error. Do not repeat any profile fields in your final answer.'),
   ('chrome','Using only the unified computer use cua_repl plugin and its documented API, inspect the EXISTING logged-in Chrome browser session by listing its available tabs. Do not open, navigate, close, or change any tabs. Do not read page content, history, cookies, credentials, or private messages. Return only CHROME_PASS and the tab count if successful, otherwise BLOCKED with the concrete error. Do not return tab titles or URLs. Do not use shell, AppleScript, raw browser profile reads, or another runtime.'),
   ('mac_app','Using only the unified computer use cua_repl plugin and its documented API, open Calculator and compute 7 times 8 using its UI. Verify the displayed result is 56. Do not read other apps or private content. Return only MAC_APP_PASS:56 if observed, otherwise BLOCKED and the concrete error. Do not use shell or AppleScript or another runtime.'),
  ]
  for name,prompt in probes:
   try:
    result=rpc.turn(thread,prompt,timeout=180);results[name]=result
    print(name,result['status'],result['final'],flush=True)
   except Exception as e:results[name]={'error':type(e).__name__,'detail':str(e)};print(name,type(e).__name__,str(e)[:300],flush=True)
   (PRIVATE/'app-server-capabilities.json').write_text(json.dumps(results,indent=2))
  start=rpc.call('turn/start',{'threadId':thread,'input':[{'type':'text','text':'Run a harmless local command that waits 30 seconds, then return WAIT_FINISHED. Do not change any files or contact any service.'}]});tid=start['turn']['id'];start_time=time.monotonic();observed=False
  while time.monotonic()-start_time<30:
   e=rpc.receive(30)
   if e.get('method')=='item/started' and e.get('params',{}).get('item',{}).get('type')=='commandExecution':observed=True;break
   if e.get('method')=='turn/completed':break
  rpc.call('turn/interrupt',{'threadId':thread,'turnId':tid})
  terminal=None;end=time.monotonic()+20
  while time.monotonic()<end:
   found=[e for e in rpc.events if e.get('method')=='turn/completed' and e.get('params',{}).get('turn',{}).get('id')==tid]
   if found:terminal=found[-1]['params']['turn']['status'];break
   rpc.receive(max(.1,end-time.monotonic()))
  results['cancel']={'command_started':observed,'terminal_status':terminal};print('cancel',results['cancel'],flush=True)
  results['after_cancel']=rpc.turn(thread,'Return only CANCEL_RECOVERED without tools.',timeout=45)
 finally:
  (PRIVATE/'app-server-capabilities.json').write_text(json.dumps(results,indent=2));rpc.close()
if __name__=='__main__':main()
