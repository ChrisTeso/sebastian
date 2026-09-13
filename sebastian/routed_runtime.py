"""Luna answers simple requests; bounded escalation preserves host authority."""
import json,threading,time
from dataclasses import replace
from pathlib import Path
from .session_runtime import SessionRuntime
from .runtime import RuntimeFailure
from .runtime_policy import make_runtime_policy,load_user_config
from .routing import request_lane,reasoning_effort
from .model_router import ROUTER_INSTRUCTIONS,ROUTER_SCHEMA,parse_route
from .outbound import RuntimeReply

MODELS={'luna':'gpt-5.6-luna','sol':'gpt-5.6-sol','astra':'gpt-6-astra'}
LABELS={'luna':'Luna','sol':'Sol','astra':'Astra'}

class RoutedRuntime:
    def __init__(self,metadata_path,restricted_workspace):
        path=Path(metadata_path)
        self.normal=SessionRuntime(path,model=MODELS['astra'])
        self.sol=SessionRuntime(path.with_name('sol-sessions.json'),model=MODELS['sol'])
        self.luna=SessionRuntime(path.with_name('luna-sessions.json'),model=MODELS['luna'],execution_lock=threading.Lock())
        self.image=SessionRuntime(path.with_name('image-sol-sessions.json'),model=MODELS['sol'],execution_lock=threading.Lock())
        restricted=make_runtime_policy('conversation',restricted_workspace,load_user_config())
        self.image_profile=replace(restricted,developer_instructions=restricted.developer_instructions+' This is a self-contained image creation request authenticated by the host. Generate the requested image with the native image generation tool using only the supplied prompt. No other tools or outside information are needed.')
        self.router_profile=replace(restricted,config={**restricted.config,'features.image_generation':False,'features.shell_tool':False,'features.view_image':False,'features.sleep_tool':False},developer_instructions=restricted.developer_instructions+ROUTER_INSTRUCTIONS)
        self._local=threading.local();self._active=set();self._lock=threading.Lock();self._closed=False
    @property
    def _connection(self):return self.normal._connection or self.sol._connection or self.luna._connection
    @property
    def last_timings(self):return getattr(self._local,'timings',{})
    @property
    def last_models(self):return tuple(getattr(self._local,'models',[]))
    def stage_images(self,images):
        self.normal.stage_images(images)
        self._local.images=self.normal._local.images;self.normal._local.images=[]
    def respond(self,key,profile,prompt,reader=None,timeout=180):
        images=getattr(self._local,'images',[]);self._local.images=[]
        request=json.loads(prompt).get('request','')
        started=time.monotonic();deadline=started+timeout;cancel=threading.Event()
        self._local.timings={};self._local.models=[]
        with self._lock:
            if self._closed:raise RuntimeFailure('Runtime is closed')
            self._active.add(cancel)
        startup=0
        def run(runtime,chosen,policy,*,routing=False):
            nonlocal startup
            if cancel.is_set():raise RuntimeFailure('Request canceled')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise RuntimeFailure('Request timed out')
            self._local.models.append(LABELS[chosen]);runtime.stage_images(images)
            try:
                return runtime.respond(key,policy,prompt,None if routing else reader,min(20,remaining) if routing else remaining,
                    effort='low' if routing else reasoning_effort(request),output_schema=ROUTER_SCHEMA if routing else None,
                    cancel_event=cancel,delivery_instructions=not routing)
            finally:startup+=runtime.last_timings.get('runtime_startup_s',0)
        try:
            isolated=request_lane(request,owner=profile.access=='owner')=='image' and reader is None
            if isolated:
                result=run(self.image,'sol',self.image_profile)
                return replace(result,model='Sol')
            routed_by=None
            try:
                result=run(self.luna,'luna',self.router_profile,routing=True)
                route,answer=parse_route(result)
                routed_by='Luna'
            except (RuntimeFailure,ValueError):
                # This stage has no action tools; malformed/unavailable routing
                # can fall back, but cancellation must never start another model.
                if cancel.is_set():raise RuntimeFailure('Request canceled') from None
                route='astra'
            if cancel.is_set():raise RuntimeFailure('Request canceled')
            if route=='answer':return RuntimeReply(answer,model='Luna')
            result=run(self.sol if route=='sol' else self.normal,route,profile)
            return replace(result,model=LABELS[route],routed_by=routed_by)
        finally:
            self._local.timings={'runtime_startup_s':startup,'runtime_s':time.monotonic()-started,'total_s':time.monotonic()-started}
            with self._lock:self._active.discard(cancel)
    def cancel(self):
        with self._lock:
            active=bool(self._active)
            for event in self._active:event.set()
        for runtime in (self.normal,self.sol,self.luna,self.image):active=runtime.cancel() or active
        return active
    def close(self):
        with self._lock:self._closed=True
        self.cancel()
        for runtime in (self.normal,self.sol,self.luna,self.image):runtime.close()
