"""Serialized, cancelable native sessions. Private input remains in memory only here."""
from __future__ import annotations
import hashlib,json,math,os,queue,re,tempfile,threading,time
from dataclasses import replace
from pathlib import Path
from .runtime import AppServer,BINARY,RuntimeFailure

class _Connection(AppServer):
    def __init__(self,owner):
        self.owner=owner
        super().__init__(owner.binary)
    def _next(self,deadline):
        limit=min(deadline,self.owner._deadline) if not self.owner._interrupting else deadline
        while True:
            if not self.owner._interrupting and (self.owner._cancel.is_set() or (getattr(self.owner,'_external_cancel',None) is not None and self.owner._external_cancel.is_set())):
                raise RuntimeFailure('Request canceled; interrupted actions may already have occurred')
            if time.monotonic()>=limit:raise RuntimeFailure('Runtime request timed out; actions may already have occurred')
            try:message=self._queue.get(timeout=min(.1,max(.001,limit-time.monotonic())))
            except queue.Empty:continue
            if message.get('method')=='sebastian/runtime_closed':raise RuntimeFailure('Runtime connection closed; request was not replayed')
            return message
    def _request(self,method,params,timeout=60):
        if method=='turn/interrupt':
            self.owner._interrupting=True
            try:return super()._request(method,params,timeout=min(timeout,2))
            finally:self.owner._interrupting=False
        if method=='thread/start':
            if self.owner.model:params={**params,'model':self.owner.model,'allowModelFallback':False}
            saved=self.owner._ids.get(self.owner._scope)
            if saved:
                # Resume under the exact fingerprint and original enforcement configuration.
                params={k:v for k,v in params.items() if k in {'cwd','config','developerInstructions','approvalPolicy','sandbox'}}
                params['threadId']=saved
                result=super()._request('thread/resume',params,timeout)
            else:result=super()._request(method,params,timeout)
            self.owner._ids[self.owner._scope]=result['thread']['id']
            self.owner._save_ids()
            return result
        if method=='turn/start':
            params=dict(params)
            params['input']=list(params['input'])+[{'type':'image','url':u} for u in self.owner._images]
            if self.owner.model:params['model']=self.owner.model
            effort=getattr(self.owner,'_turn_effort',None)
            if effort:params['effort']=effort
            schema=getattr(self.owner,'_output_schema',None)
            if schema is not None:params['outputSchema']=schema
        return super()._request(method,params,timeout)

class SessionRuntime:
    """One request reader; cancellation only sets an event from the control thread."""
    _desktop_lock=threading.Lock()
    def __init__(self,metadata_path:Path,binary:Path=BINARY,model:str|None=None,execution_lock=None):
        self.metadata_path=Path(metadata_path);self.binary=binary;self.model=model
        if execution_lock is not None:self._desktop_lock=execution_lock
        self._turn_effort=None;self._output_schema=None;self._external_cancel=None
        self._state_lock=threading.Lock();self._cancel=threading.Event();self._local=threading.local()
        self._connection=None;self._active=False;self._closed=False;self._interrupting=False
        self._deadline=float('inf');self._scope='';self._images=[];self.last_timings={}
        self.metadata_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.metadata_path.parent.is_symlink() or self.metadata_path.is_symlink():raise ValueError('Session metadata must not be a symlink')
        os.chmod(self.metadata_path.parent,0o700)
        self._ids={}
        if self.metadata_path.exists():
            os.chmod(self.metadata_path,0o600)
            self._ids=json.loads(self.metadata_path.read_text())
            if not isinstance(self._ids,dict) or any(not re.fullmatch('[a-f0-9]{64}',k) or not isinstance(v,str) or len(v)>200 for k,v in self._ids.items()):raise ValueError('Invalid session metadata')
    def _save_ids(self):
        fd,name=tempfile.mkstemp(prefix='.sessions-',dir=self.metadata_path.parent)
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump(self._ids,stream);stream.flush();os.fsync(stream.fileno())
            os.replace(name,self.metadata_path)
        finally:
            if os.path.exists(name):os.unlink(name)
    def stage_images(self,images:list[str]):
        self._local.images=[]
        if not isinstance(images,list) or len(images)>8 or any(not isinstance(u,str) or not re.fullmatch(r'data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+',u) or len(u)>20_000_000 for u in images):
            raise ValueError('Images must be bounded image data URLs')
        self._local.images=list(images)
    def respond(self,key,profile,prompt,reader=None,timeout=180,*,effort=None,output_schema=None,cancel_event=None,delivery_instructions=True):
        if effort not in (None,'low','medium'):raise ValueError('Invalid reasoning effort')
        if delivery_instructions:profile=replace(profile,developer_instructions=profile.developer_instructions+' The host delivers your final answer as Sebastian into the authorized original conversation/thread. Generated images are delivered automatically as attachments in that same conversation; use the image generation tool when asked for an image and emit its generated image result. Do not replace an image with a local path or a textual description. Do not use native messaging tools merely to deliver that final answer. A separate explicit owner request to send another message remains subject to existing permissions.')
        images=getattr(self._local,'images',[]);self._local.images=[]
        start=time.monotonic()
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('timeout must be finite and positive')
        while True:
            if cancel_event is not None and cancel_event.is_set():raise RuntimeFailure('Request canceled')
            remaining=timeout-(time.monotonic()-start)
            if remaining<=0:raise RuntimeFailure('Request timed out waiting for the desktop')
            if self._desktop_lock.acquire(timeout=min(.1,remaining)):break
        acquired=time.monotonic();startup=0
        try:
            with self._state_lock:
                if self._closed:raise RuntimeFailure('Runtime is closed')
                if cancel_event is not None and cancel_event.is_set():raise RuntimeFailure('Request canceled')
                self._cancel.clear();self._active=True
            self._deadline=start+timeout;self._images=images;self._turn_effort=effort;self._output_schema=output_schema;self._external_cancel=cancel_event
            self._scope=hashlib.sha256((key+':'+profile.fingerprint+':'+(self.model or '')).encode()).hexdigest()
            if self._connection is None or self._connection.process.poll() is not None:
                if self._connection:self._connection.close()
                stamp=time.monotonic();self._connection=_Connection(self);startup=time.monotonic()-stamp
            return self._connection.respond(key,profile,prompt,reader,timeout=max(.001,self._deadline-time.monotonic()))
        except BaseException:
            # Discard poisoned transport even when turn/start acknowledgment was lost.
            # A later explicit request can resume the saved native thread; this turn is never retried.
            if self._connection:
                self._connection.close();self._connection=None
            raise
        finally:
            now=time.monotonic();self._images=[];self._external_cancel=None;self._output_schema=None
            self.last_timings={'queue_wait_s':acquired-start,'runtime_startup_s':startup,'runtime_s':max(0,now-acquired-startup),'total_s':now-start}
            with self._state_lock:self._active=False
            self._desktop_lock.release()
    def cancel(self):
        with self._state_lock:
            if not self._active:return False
            self._cancel.set();return True
    def close(self):
        with self._state_lock:self._closed=True;self._cancel.set()
        with self._desktop_lock:
            if self._connection:self._connection.close();self._connection=None
        self._local.images=[]
