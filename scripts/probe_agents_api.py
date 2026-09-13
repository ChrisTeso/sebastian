#!/usr/bin/env python3
"""S2 disposable Agents API session with a self-hosted Mac executor."""
import json,os,pathlib,queue,signal,subprocess,threading,time,urllib.request,urllib.error
ROOT=pathlib.Path(__file__).resolve().parents[1];P=ROOT/'.private/probes';BINARY='/Applications/ChatGPT.app/Contents/Resources/codex'
BASE='https://api.openai.com/v1/agents/sessions'
class API:
 def __init__(self):
  self.key=subprocess.check_output(['/usr/bin/security','find-generic-password','-s','com.christeso.sebastian.openai','-w'],text=True,timeout=20).strip()
 def request(self,method,path='',body=None,stream=False):
  req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Authorization':'Bearer '+self.key,'OpenAI-Beta':'agents=v1','Content-Type':'application/json','Accept':'text/event-stream' if stream else 'application/json'})
  try:
   r=urllib.request.urlopen(req,timeout=180 if stream else 30)
   if stream:return r
   with r:
    raw=r.read();return json.loads(raw) if raw.strip() else {"http_status":r.status}
  except urllib.error.HTTPError as e:
   d=e.read().decode();raise RuntimeError('HTTP '+str(e.code)+' '+d[:1500]) from None

def main():
 os.umask(0o077);P.mkdir(parents=True,exist_ok=True,mode=0o700);ws=P/'agents-workspace';ws.mkdir(exist_ok=True)
 (ws/'fixture.txt').write_text('SEBASTIAN-S2-FILE-7349\n')
 api=API();summary={};proc=None;sid=None;stream=None
 log=open(P/'agents-events.jsonl','w');err=open(P/'executor-stderr.log','w');out=open(P/'executor-stdout.log','w');events=[];q=queue.Queue()
 try:
  payload={'agent':{'model':'gpt-6-astra','instructions':'This is an isolated harmless runtime compatibility test. Only perform the explicit test. Never read private messages, memory, credentials, browser storage, other tasks or unrelated files. Never send messages or modify external services. Never delegate or invoke another reasoning runtime. Use the available native tools, reporting exact blockers. Do not use shell or AppleScript as a substitute for computer/browser plugins.'},'environment':{'type':'self_hosted','workspace_directory':str(ws),'capability_directories':['/Users/teso/.codex/plugins/cache/openai-bundled/unified-computer-use/26.908.40834','/Users/teso/.codex/plugins/cache/openai-curated-remote/github/0.1.12-5f7cd798dc99']}}
  (P/'agents-request.json').write_text(json.dumps(payload,indent=2))
  created=api.request('POST',body=payload);(P/'agents-session.json').write_text(json.dumps(created,indent=2));sid=created['id'];env=created['environment'];eid=env.get('id') or env.get('environment_id');remote=env['remote_url'];assert eid
  summary.update(session_id=sid,environment_id=eid);print('Agents API session created; environment awaits connection.',flush=True)
  stream=api.request('GET','/'+sid+'/events?stream=true',stream=True)
  def reader():
   try:
    for raw in stream:
     line=raw.decode().strip()
     if line.startswith('data:'):
      data=line[5:].strip()
      if data=='[DONE]':continue
      try:e=json.loads(data)
      except ValueError:continue
      events.append(e);q.put(e);log.write(json.dumps(e)+'\n');log.flush()
   except Exception as e:q.put({'type':'stream.error','error':type(e).__name__})
  threading.Thread(target=reader,daemon=True).start()
  childenv={k:v for k,v in os.environ.items() if k in {'HOME','PATH','TMPDIR','USER','LOGNAME','LANG','SHELL'}}
  # The restricted executor credential is the only API key passed to compute.
  childenv['CODEX_API_KEY']=(P/'executor-key').read_text().strip()
  exec_home=P/'executor-home';exec_home.mkdir(exist_ok=True);childenv['CODEX_HOME']=str(exec_home)
  proc=subprocess.Popen([BINARY,'exec-server','--remote',remote,'--environment-id',eid,'--exit-on-stdin-close'],stdin=subprocess.PIPE,stdout=out,stderr=err,env=childenv,start_new_session=True)
  summary['executor_pid']=proc.pid
  end=time.monotonic()+100;connected=False
  while time.monotonic()<end:
   if proc.poll() is not None:raise RuntimeError('Executor exited with code '+str(proc.returncode))
   try:e=q.get(timeout=2)
   except queue.Empty:continue
   if e.get('type')=='agent.session.environment.connected':connected=True;break
   if e.get('type') in ['agent.session.environment.failed','error']:raise RuntimeError('Environment connection failed; inspect private events')
  summary['connected']=connected
  if not connected:raise TimeoutError('Executor did not report connected')
  print('Mac executor connected.',flush=True)
  def turn(name,prompt):
   offset=len(events);start=time.monotonic();api.request('POST','/'+sid+'/events',{'events':[{'type':'agent.session.input.message','input':[{'role':'user','content':[{'type':'input_text','text':prompt}]}]}]})
   terminal=None
   while time.monotonic()-start<200:
    found=[e for e in events[offset:] if e.get('type') in ['agent.session.turn.completed','agent.session.turn.failed','agent.session.turn.cancelled'] and not (e.get('turn') or {}).get('subagent_id')]
    if found:terminal=found[-1];break
    try:q.get(timeout=2)
    except queue.Empty:pass
   if terminal is None:
    api.request('POST','/'+sid+'/events',{'events':[{'type':'agent.session.input.cancel'}]});raise TimeoutError(name)
   items=api.request('GET','/'+sid+'/items?order=asc&limit=100');(P/('agents-'+name+'-items.json')).write_text(json.dumps(items,indent=2))
   texts=[e.get('text') for e in events[offset:] if e.get('type')=='agent.session.turn.output_text.done']
   result={'terminal':terminal,'seconds':round(time.monotonic()-start,3),'texts':texts};summary[name]=result
   (P/'agents-summary.json').write_text(json.dumps(summary,indent=2));print(name,terminal['type'],texts,flush=True)
  for name,prompt in [
   ('file','Read only fixture.txt in the current directory with your local file tool and return its exact contents. Remember the conversation marker BLUE-OTTER-7349 for the next turn.'),
   ('continuation','Return only the conversation marker from my previous message. Do not use tools.'),
   ('service_plugin','Use the connected GitHub service plugin get_profile tool once to prove authenticated access. Do not use shell, curl, CLI, other runtime or extract credentials. Do not return profile fields. Return AUTHENTICATED_PLUGIN_READ_PASS if it succeeds, or BLOCKED with the concrete reason.'),
   ('chrome','Using only the unified computer use cua_repl plugin, inspect the existing signed-in Chrome browser by listing available tabs. Do not open, navigate or modify tabs, read page content, history, cookies, credentials or messages. Return CHROME_PASS and tab count, otherwise BLOCKED with the exact error. Never use shell or other substitutes.'),
   ('mac_app','Using only the unified computer use cua_repl plugin, open Calculator and compute 7 times 8 through its UI. Verify the display is 56. Return MAC_APP_PASS:56 if observed, otherwise BLOCKED with the concrete error. No shell, AppleScript, or another reasoning runtime.')]:turn(name,prompt)
 except Exception as e:
  summary['error']={'type':type(e).__name__,'detail':str(e)};print('Probe stopped:',type(e).__name__,str(e)[:1500],flush=True)
 finally:
  if sid:
   try:api.request('POST','/'+sid+'/events',{'events':[{'type':'agent.session.input.cancel'}]});summary['cleanup_cancel']=True
   except Exception:summary['cleanup_cancel']=False
   try:summary['deleted']=api.request('DELETE','/'+sid)
   except Exception as e:summary['delete_error']=type(e).__name__
  if proc is not None and proc.poll() is None:
   proc.stdin.close()
   try:proc.wait(timeout=10)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=10)
  if proc is not None:summary['executor_exit']=proc.returncode
  (P/'agents-summary.json').write_text(json.dumps(summary,indent=2))
  out.close();err.close()
  print('Cleanup complete; evidence saved privately.',flush=True)
if __name__=='__main__':main()
