#!/usr/bin/env python3
"""S2 isolated App Server capability probe. No chat adapters or live delivery."""
import argparse, json, os, pathlib, queue, signal, subprocess, threading, time
ROOT=pathlib.Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'.private/probes'
BINARY='/Applications/ChatGPT.app/Contents/Resources/codex'

class RPC:
    def __init__(self):
        env={k:v for k,v in os.environ.items() if k in {'HOME','PATH','TMPDIR','USER','LOGNAME','LANG','SHELL'}}
        self.err=open(PRIVATE/'app-server-stderr.log','a')
        self.p=subprocess.Popen([BINARY,'app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.err,text=True,env=env,start_new_session=True)
        self.q=queue.Queue();self.next_id=1;self.events=[];self.pending=[]
        threading.Thread(target=self.reader,daemon=True).start()
        self.call('initialize',{'clientInfo':{'name':'sebastian_compatibility_probe','version':'0.1.0'},'capabilities':{'experimentalApi':True}})
        self.send({'method':'initialized','params':{}})
    def reader(self):
        for line in self.p.stdout:
            try:self.q.put(json.loads(line))
            except json.JSONDecodeError:pass
    def send(self,value):self.p.stdin.write(json.dumps(value)+'\n');self.p.stdin.flush()
    def receive(self,timeout=90):
        msg=self.q.get(timeout=timeout)
        if 'method' in msg:
            self.events.append(msg)
            if 'id' in msg:
                # No unattended permission, auth, or external-action approvals in probes.
                self.send({'id':msg['id'],'error':{'code':-32000,'message':'Probe does not grant interactive requests'}})
        return msg
    def call(self,method,params=None,timeout=90):
        rid=self.next_id;self.next_id+=1;self.send({'id':rid,'method':method,'params':params or {}})
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            m=self.receive(max(.1,end-time.monotonic()))
            if m.get('id')==rid and 'method' not in m:
                if 'error' in m:raise RuntimeError(json.dumps(m['error']))
                return m.get('result')
        raise TimeoutError(method)
    def turn(self,thread,text,timeout=180):
        offset=len(self.events);start=time.monotonic()
        r=self.call('turn/start',{'threadId':thread,'input':[{'type':'text','text':text}]})
        tid=r['turn']['id']
        while time.monotonic()-start<timeout:
            candidates=[e for e in self.events[offset:] if e.get('method')=='turn/completed' and e.get('params',{}).get('turn',{}).get('id')==tid]
            if candidates:
                items=[e['params']['item'] for e in self.events[offset:] if e.get('method')=='item/completed']
                return {'turn_id':tid,'status':candidates[-1]['params']['turn']['status'],'seconds':round(time.monotonic()-start,3),'final':[i.get('text') for i in items if i.get('type')=='agentMessage' and i.get('phase')=='final_answer'],'items':items}
            self.receive(max(.1,timeout-(time.monotonic()-start)))
        self.call('turn/interrupt',{'threadId':thread,'turnId':tid});raise TimeoutError('turn')
    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            try:self.p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.p.pid,signal.SIGTERM)
                try:self.p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(self.p.pid,signal.SIGKILL);self.p.wait()
        self.err.close()

def main():
    os.umask(0o077);PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    ws=PRIVATE/'workspace';ws.mkdir(exist_ok=True)
    (ws/'fixture.txt').write_text('SEBASTIAN-S2-FILE-7349\n')
    (ws/'AGENTS.md').write_text('This is a harmless compatibility probe workspace. Follow only the explicit probe. Never read private messages, secrets, browser storage, other tasks or personal memory. Do not send messages, modify user data, or spawn subagents. Do not investigate unrelated repositories.\n')
    rpc=RPC();result={}
    try:
        account=rpc.call('account/read',{'refreshToken':False});a=account.get('account') or {};result['auth']={k:a.get(k) for k in ['type','planType']};print('Authentication:',result['auth'],flush=True)
        started=rpc.call('thread/start',{'cwd':str(ws),'approvalPolicy':'never','sandbox':'danger-full-access','ephemeral':False,'developerInstructions':'This isolated runtime is being tested. Only perform the explicit harmless probe. Do not read memory, secret files, private messages, or unrelated content. Do not invoke another reasoning runtime or delegate. Report blocked capabilities truthfully. No external writes or sends.'})
        thread=started['thread']['id'];result['thread_id']=thread;(PRIVATE/'app-server-thread.json').write_text(json.dumps({'thread_id':thread}))
        inventory=rpc.call('mcpServerStatus/list',{'threadId':thread,'detail':'toolsAndAuthOnly'})
        result['inventory']=[{'name':d.get('name'),'authStatus':d.get('authStatus'),'tools':list(d.get('tools',{}))} for d in inventory.get('data',[])]
        print('MCP inventory:',json.dumps(result['inventory']),flush=True)
        turn=rpc.turn(thread,'Read only fixture.txt in the current directory with your local file tool and return its exact contents. Remember this conversation marker for the next turn: BLUE-OTTER-7349. Do not read any other files.')
        result['file_read']=turn;print('File probe:',turn['status'],turn['final'],flush=True)
        turn=rpc.turn(thread,'What conversation marker did I give you in my previous message? Return only that marker without tools.')
        result['continuation']=turn;print('Continuation:',turn['status'],turn['final'],flush=True)
    finally:
        (PRIVATE/'app-server-initial.json').write_text(json.dumps(result,indent=2));rpc.close()
if __name__=='__main__':main()
