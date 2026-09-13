"""Small persistent Codex App Server client; no channel delivery or transcript logs."""
from __future__ import annotations
import json,os,queue,signal,subprocess,threading,time
from collections import deque
from pathlib import Path
from .runtime_policy import RuntimePolicy,PermissionDenied
from .toolbelt_reader import ToolbeltReader
from .outbound import OutboundImage,RuntimeReply,MAX_REPLY_IMAGES

BINARY=Path('/Applications/ChatGPT.app/Contents/Resources/codex')
class RuntimeFailure(RuntimeError):pass

class AppServer:
    def __init__(self,binary:Path=BINARY):
        self._env={k:v for k,v in os.environ.items() if k in {'HOME','PATH','TMPDIR','USER','LOGNAME','LANG','SHELL'}}
        self.process=subprocess.Popen([str(binary),'app-server','--listen','stdio://'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=self._env,start_new_session=True)
        self._queue=queue.Queue(maxsize=10000);self._id=0;self._lock=threading.RLock();self._sessions={};self._pending=deque();self._active=None;self._closed=False
        threading.Thread(target=self._read,daemon=True).start()
        try:
            self._request('initialize',{'clientInfo':{'name':'sebastian','version':'0.1.0'},'capabilities':{'experimentalApi':True}})
            self._send({'method':'initialized','params':{}})
        except BaseException:self.close();raise
    def _read(self):
        try:
            for line in self.process.stdout:
                try:value=json.loads(line)
                except ValueError:continue
                try:self._queue.put(value,timeout=1)
                except queue.Full:
                    self.process.terminate();return
        finally:
            try:self._queue.put_nowait({'method':'sebastian/runtime_closed'})
            except queue.Full:pass
    def _send(self,message):
        if self._closed or self.process.poll() is not None:raise RuntimeFailure('Runtime is unavailable')
        self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()
    def _next(self,deadline):
        try:message=self._queue.get(timeout=max(.001,deadline-time.monotonic()))
        except queue.Empty:raise RuntimeFailure('Runtime request timed out') from None
        if message.get('method')=='sebastian/runtime_closed':raise RuntimeFailure('Runtime connection closed')
        return message
    def _server_request(self,message):
        params=message.get('params',{});active=self._active
        if message['method']=='item/tool/call' and active is not None:
            thread,turn,profile,reader=active
            try:
                if params.get('threadId')!=thread or params.get('turnId')!=turn:raise PermissionDenied('Wrong runtime conversation')
                profile.authorize_tool(params.get('tool',''))
                if params.get('tool')!='toolbelt_read' or reader is None:raise PermissionDenied('No approved document tool')
                text=reader.read(params.get('arguments',{}));ok=True
            except (PermissionDenied,ValueError,TypeError):text='This tool request is outside the approved access scope.';ok=False
            self._send({'id':message['id'],'result':{'contentItems':[{'type':'inputText','text':text}],'success':ok}})
        else:
            # No action-time approval, login, or extra tool authority is fabricated.
            self._send({'id':message['id'],'error':{'code':-32000,'message':'This request requires owner attention in the desktop app'}})
    def _request(self,method,params,timeout=60):
        self._id+=1;rid=self._id;self._send({'id':rid,'method':method,'params':params});deadline=time.monotonic()+timeout
        while True:
            message=self._next(deadline)
            if 'method' in message and 'id' in message and self._active is not None:self._server_request(message)
            elif message.get('id')==rid:
                if 'error' in message:raise RuntimeFailure('Runtime rejected '+method)
                return message.get('result',{})
            else:self._pending.append(message)
    def respond(self,key:str,profile:RuntimePolicy,prompt:str,reader:ToolbeltReader|None=None,timeout:float=180)->RuntimeReply:
        with self._lock:
            scope=key+':'+profile.fingerprint
            if scope not in self._sessions:
                params=profile.thread_params()
                if reader is not None:
                    profile.authorize_tool('toolbelt_read');params['dynamicTools']=[reader.descriptor()]
                self._sessions[scope]=self._request('thread/start',params)['thread']['id']
            thread=self._sessions[scope]
            profile.verify_mcp_inventory(self._request('mcpServerStatus/list',{'threadId':thread,'detail':'toolsAndAuthOnly'}))
            started=self._request('turn/start',{'threadId':thread,'input':[{'type':'text','text':prompt}]})
            turn=started['turn']['id'];self._active=(thread,turn,profile,reader);deadline=time.monotonic()+timeout;final=[];images={}
            try:
                while True:
                    message=self._pending.popleft() if self._pending else self._next(deadline)
                    if 'method' in message and 'id' in message:self._server_request(message);continue
                    p=message.get('params',{})
                    if p.get('threadId')!=thread or (p.get('turnId') and p['turnId']!=turn):continue
                    if message.get('method')=='item/completed' and p.get('turnId')==turn:
                        item=p.get('item',{})
                        if item.get('type')=='imageGeneration' and item.get('status')=='completed' and not item.get('failure'):
                            if item.get('id') not in images:
                                if len(images)>=MAX_REPLY_IMAGES:raise RuntimeFailure('Too many generated reply images')
                                images[item['id']]=OutboundImage.from_result(item.get('result'))
                        if item.get('type')=='agentMessage' and item.get('phase')=='final_answer':final.append(item.get('text',''))
                    if message.get('method')=='turn/completed' and p.get('turn',{}).get('id')==turn:
                        if p['turn'].get('status')!='completed':raise RuntimeFailure('The request did not complete')
                        answer='\n'.join(final).strip()
                        if not answer and not images:raise RuntimeFailure('The runtime returned no final answer')
                        return RuntimeReply(answer,tuple(images.values()))
            except BaseException:
                try:self._request('turn/interrupt',{'threadId':thread,'turnId':turn},timeout=5)
                except Exception:pass
                raise
            finally:self._active=None
    def close(self):
        if self._closed:return
        self._closed=True
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid,signal.SIGTERM)
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
