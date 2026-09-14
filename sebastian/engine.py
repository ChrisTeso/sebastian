"""Persistent listener/worker integration. Durable storage contains references only."""
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import json,logging,os,re,tempfile,threading,time
from .contracts import Channel,Destination,Event
from .policy import AdapterRegistry,Policy
from .service import Sebastian
from .slack import SlackConfig,SlackAPI,SlackAdapter,SocketReceiver,socket_client,slack_time
from .messages import MessagesAdapter,MessagesSender
from .ledger import Ledger,JobMetadata
from .images import ConversationImages,ImageUnavailable
from .formatting import signed_reply
from .outbound import image_marker
from .routing import request_lane,needs_image_history
from .runtime_policy import load_user_config
from .config import private_read
from .toolbelt_reader import ToolbeltReader

CANCEL=re.compile(r'^(?:@sebastian\s+)?(?:cancel|stop)\s*[.!]?$',re.I)
INTERRUPTED='This request was interrupted or became stale. I did not replay its actions. Please send a fresh request if you still want it completed.\n\nModel: none (service notice)'

class Sources:
    def __init__(self,settings,registry,ledger,on_cancel=lambda:None):
        self.settings,self.registry,self.ledger=settings,registry,ledger
        self.on_cancel=on_cancel;self.bot,self.app=settings.slack_credentials()
        self.client=socket_client(self.app,self.bot)
        # Provider diagnostics can contain request bodies or signed URLs.
        for logger in (getattr(self.client,'logger',None),getattr(self.client.web_client,'_logger',None)):
            if logger is not None:logger.disabled=True
        since=ledger.get_checkpoint('slack-start-ms')
        if since is None:since=int(time.time()*1000);ledger.set_checkpoint('slack-start-ms',since)
        self.config=SlackConfig(*(settings.slack[k] for k in ['team_id','bot_user_id','bot_id','app_id','owner_user_id']),datetime.fromtimestamp(since/1000,timezone.utc))
        self.api=SlackAPI(self.client.web_client,self.config);self.api.inspect_identity()
        self.slack_signer=registry.register('slack',Channel.SLACK,self.config.team_id)
        self.messages_signer=registry.register('messages',Channel.MESSAGES,settings.messages.account_id)
        self.messages_sender=MessagesSender(settings.messages.account_id)
        self.receiver=SocketReceiver(self.client,self.accept_slack)
    def accept_slack(self,payload):
        if payload.get('type')!='event_callback' or payload.get('team_id')!=self.config.team_id or payload.get('api_app_id')!=self.config.app_id:return
        row=payload.get('event',{})
        if row.get('type') not in ('message','app_mention') or row.get('subtype') not in (None,'file_share') or row.get('bot_id') or row.get('user') in (None,self.config.bot_user_id):return
        text=row.get('text','')
        if not isinstance(text,str):return
        owner=row.get('user')==self.config.owner_user_id
        mentioned=bool(re.search(r'(?<![\w@])@sebastian\b',text,re.I)) or f'<@{self.config.bot_user_id}>' in text
        direct_owner=owner and row.get('channel_type') in ('im',None)
        # Slack thread membership does not mean a message addresses Sebastian.
        if not mentioned and not direct_owner:return
        occurred=slack_time(row['ts'])
        if occurred<=self.config.started_at:return
        meta=JobMetadata(Channel.SLACK,self.config.team_id,row['channel'],row.get('thread_ts'),row['ts'],occurred,datetime.now(timezone.utc))
        self.ledger.accept(meta,lane=request_lane(text,owner=owner,attachments=bool(row.get('files'))))
        normalized=text.replace(f'<@{self.config.bot_user_id}>','@sebastian').strip()
        if owner and (mentioned or direct_owner) and CANCEL.fullmatch(normalized):self.on_cancel()
    def _self_reply(self,event):
        if event.owner_metadata_verified:self.ledger.observe_self_reply(event)
        if self.ledger.is_self_reply(event):return True
        if not event.owner_metadata_verified or not event.attachments:return False
        adapter=self._messages()
        try:
            for marker in self._image_markers(adapter,event):
                self.ledger.observe_self_reply(event,marker)
                if self.ledger.is_self_reply(event,marker):return True
            return False
        finally:adapter.close()
    @staticmethod
    def _image_markers(adapter,event):
        if 'filename' not in adapter.columns['attachment']:return ()
        rows=adapter.db.execute('''SELECT a.filename FROM attachment a
            JOIN message_attachment_join ma ON ma.attachment_id=a.ROWID
            JOIN message m ON m.ROWID=ma.message_id
            JOIN chat_message_join cm ON cm.message_id=m.ROWID
            JOIN chat c ON c.ROWID=cm.chat_id
            WHERE m.guid=? AND c.guid=? LIMIT 32''',(event.event_id,event.conversation_id))
        return tuple(marker for row in rows if row[0] and (marker:=image_marker(row[0])))
    def _messages(self,highwater=0):
        return MessagesAdapter(self.settings.messages_db,self.settings.messages,self.messages_signer,highwater=highwater,is_self_reply=self._self_reply,
                               is_sebastian_message=self.ledger.is_confirmed_reply)
    def reconcile(self):
        destinations={d.destination for d in self.ledger.uncertain_deliveries()}
        for destination in destinations:
            try:
                if destination.channel==Channel.MESSAGES:
                    adapter=self._messages()
                    try:
                        rows=adapter._rows('c.guid=? AND m.is_from_me=1',(destination.conversation_id,),100,True)
                        for row in rows:
                            event=adapter._record(row)
                            if event and event.owner_metadata_verified:
                                self.ledger.observe_self_reply(event)
                                for marker in self._image_markers(adapter,event):self.ledger.observe_self_reply(event,marker)
                    finally:adapter.close()
                else:
                    args=dict(channel=destination.conversation_id,oldest=str(time.time()-3600),limit=100)
                    result=self.api.client.conversations_replies(ts=destination.thread_id,**args) if destination.thread_id else self.api.client.conversations_history(**args)
                    if not result.get('ok'):continue
                    for row in result.get('messages',[]):
                        if row.get('user')!=self.config.bot_user_id or row.get('bot_id')!=self.config.bot_id or row.get('thread_ts')!=destination.thread_id:continue
                        event=Event(Channel.SLACK,self.config.team_id,self.config.bot_user_id,destination.conversation_id,destination.thread_id,row['ts'],slack_time(row['ts']),row.get('text',''),is_from_me=True)
                        self.ledger.observe_self_reply(event)
            except Exception:continue  # Preserve uncertainty; never retry the send.

    def poll_messages(self):
        adapter=self._messages(self.ledger.get_checkpoint('messages-rowid'))
        try:
            events=adapter.poll()
            for envelope in events:
                event=envelope.event
                self.ledger.accept(JobMetadata.from_event(event),lane=request_lane(event.text,owner=event.owner_metadata_verified,attachments=bool(event.attachments)))
                if event.owner_metadata_verified and CANCEL.fullmatch(event.text.strip()):self.on_cancel()
            # Jobs commit first. A crash before this cursor commit is dedup-safe.
            self.ledger.set_checkpoint('messages-rowid',adapter.highwater)
            return adapter.invalid_record_count
        finally:adapter.close()
    def _slack_event(self,payload):
        # Durable queue owns retries/dedup. Ephemeral validation must not remember
        # a request before downstream image/provider work has succeeded.
        return SlackAdapter(self.config,self.api,self.slack_signer).receive(payload)
    def fetch(self,job):
        m=job.metadata
        expected=self.config.team_id if m.channel==Channel.SLACK else self.settings.messages.account_id
        if m.account_id!=expected:return None,[]
        if m.channel==Channel.SLACK:
            args=dict(channel=m.conversation_id,oldest=m.event_id,latest=m.event_id,inclusive=True,limit=1)
            response=self.api.client.conversations_replies(ts=m.thread_id,**args) if m.thread_id else self.api.client.conversations_history(**args)
            if not response.get('ok'):raise RuntimeError('Slack event unavailable')
            rows=[r for r in response.get('messages',[]) if r.get('ts')==m.event_id]
            if not rows:return None,[]
            row=dict(rows[0]);row['channel']=m.conversation_id
            # Restored events use the provider's current immutable identity fields.
            if row.get('thread_ts')!=m.thread_id:return None,[]
            envelope=self._slack_event({'type':'event_callback','team_id':m.account_id,'api_app_id':self.config.app_id,'event':row})
            if envelope is None:return None,[]
            images=ConversationImages(self.api,None,self.bot).load_context(envelope.event,include_history=needs_image_history(envelope.event.text))
            return envelope,images
        adapter=self._messages()
        try:
            rows=adapter._rows('m.guid=? AND c.guid=?',(m.event_id,m.conversation_id),2)
            if len(rows)!=1:return None,[]
            event=adapter._record(rows[0])
            if event is None or self._self_reply(event):return None,[]
            if not (event.owner_metadata_verified and not event.is_group and event.conversation_id in self.settings.messages.owner_private_chat_ids) and not re.search(r'(?<![\w@])@sebastian\b',event.text,re.I) and not adapter.is_reply_to_sebastian(rows[0]):return None,[]
            event=replace(event,history=adapter.history(event.conversation_id,rows[0]['message_rowid']))
            return self.messages_signer.sign(event),ConversationImages(self.api,adapter.db,self.bot).load_context(event,include_history=needs_image_history(event.text))
        finally:adapter.close()
    def send(self,destination,text):
        if destination.channel==Channel.SLACK:return self.api.send(destination,text)
        adapter=self._messages()
        try:
            before=adapter.db.execute('SELECT COALESCE(MAX(ROWID),0) FROM message').fetchone()[0]
            failed=False
            try:self.messages_sender.send(destination,text)
            except RuntimeError:failed=True
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                for row in adapter._rows('m.ROWID>? AND c.guid=?',(before,destination.conversation_id),100):
                    event=adapter._record(row)
                    if event and event.owner_metadata_verified and event.text==text:
                        return event.event_id
                time.sleep(.1)
            if failed:raise RuntimeError('Messages delivery is uncertain')
            return None
        finally:adapter.close()
    def send_image(self,destination,image):
        if destination.channel==Channel.SLACK:return self.api.send_image(destination,image)
        adapter=self._messages()
        try:
            before=adapter.db.execute('SELECT COALESCE(MAX(ROWID),0) FROM message').fetchone()[0]
            # Only the Messages transport needs a local file; keep it until the
            # provider has copied the attachment into its own message database.
            with tempfile.TemporaryDirectory(prefix='sebastian-image-') as folder:
                path=Path(folder)/image.filename
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as stream:stream.write(image.data)
                failed=False
                try:self.messages_sender.send_image(destination,path)
                except RuntimeError:failed=True
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    for row in adapter._rows('m.ROWID>? AND c.guid=?',(before,destination.conversation_id),100):
                        event=adapter._record(row)
                        if event and event.owner_metadata_verified and image.marker in self._image_markers(adapter,event):return event.event_id
                    time.sleep(.1)
                if failed:raise RuntimeError('Messages image delivery is uncertain')
                return None
        finally:adapter.close()
    def close(self):self.receiver.close()

class Engine:
    @property
    def last_timings(self):return getattr(self._worker_state,'timings',self._completed_timings)
    @last_timings.setter
    def last_timings(self,value):self._worker_state.timings=value
    @property
    def active_job(self):return getattr(self._worker_state,'job',None)
    @active_job.setter
    def active_job(self,value):self._worker_state.job=value
    def __init__(self,settings,runtime,*,ledger=None,sources=None):
        self.settings,self.runtime=settings,runtime
        self.ledger=ledger or Ledger(settings.state_dir/'ledger.sqlite')
        self.registry=AdapterRegistry()
        self.sources=sources or Sources(settings,self.registry,self.ledger,runtime.cancel)
        if sources is not None:self.registry=sources.registry
        readers={};scope_path=settings.state_dir.parent/'config/toolbelt-scopes.json'
        if scope_path.exists():
            approved_root=(Path.home()/'Code/Toolbelt').resolve()
            for scope in json.loads(private_read(scope_path)):
                root=Path(scope['root']).resolve()
                if not root.is_relative_to(approved_root):raise ValueError('Teammate documents must be within Toolbelt')
                identity=(scope['team_id'],scope['user_id'])
                if identity in readers:raise ValueError('Duplicate teammate scope')
                readers[identity]=ToolbeltReader(root,scope['documents'])
        self.policy=Policy(replace(settings.policy,toolbelt_slack=frozenset(readers)),self.registry)
        self.service=Sebastian(self.policy,self.registry,runtime,settings.owner_workspace,settings.restricted_workspace,load_user_config(),readers)
        self._worker_state=threading.local();self._completed_timings={}
        self.stopped=threading.Event();self.active_job=None;self.last_timings={};self.last_failure=None
        self.ledger.recover()
        if hasattr(self.sources,'reconcile'):self.sources.reconcile()
    def _deliver(self,job,destination,text,images=()):
        offset=len(self.ledger.deliveries(job.job_id))
        begun=time.monotonic()
        parts=[(part,None) for part in signed_reply(text,destination.channel)]
        parts.extend((image.marker,image) for image in images)
        plans=[self.ledger.plan(job.job_id,destination,part,index) for index,(part,_) in enumerate(parts,offset)]
        for position,(delivery,(part,image)) in enumerate(zip(plans,parts)):
            self.ledger.attempting(delivery.part_id)
            try:receipt=self.sources.send_image(destination,image) if image is not None else self.sources.send(destination,part)
            except Exception:
                self.ledger.uncertain(delivery.part_id)
                for unsent in plans[position+1:]:self.ledger.abandon(unsent.part_id)
                self.last_failure='Delivery is uncertain; it was not automatically retried.'
                break
            if receipt:self.ledger.confirmed(delivery.part_id,receipt)
            else:self.ledger.uncertain(delivery.part_id)
        self.ledger.complete(job.job_id)
        self.last_timings['delivery_s']=time.monotonic()-begun
    def run_once(self,lane=None):
        job=self.ledger.claim_interrupted(lane)
        if job:
            m=job.metadata
            self.last_timings={}
            try:
                deliveries=self.ledger.deliveries(job.job_id)
                if deliveries and all(d.status=='confirmed' for d in deliveries):
                    self.ledger.complete(job.job_id);return True
                self._deliver(job,Destination(m.channel,m.account_id,m.conversation_id,m.thread_id),INTERRUPTED)
                return True
            except Exception:
                self.ledger.recover_job(job.job_id);raise
        job=self.ledger.claim(lane)
        if job is None:return False
        began=time.monotonic();self.active_job=job.job_id
        self.last_timings={'queue_wait_s':max(0,time.time()-job.metadata.ingressed_at.timestamp())}
        try:
            fetch_started=time.monotonic()
            try:envelope,images=self.sources.fetch(job)
            except ImageUnavailable:
                self.ledger.start(job.job_id);m=job.metadata
                self._deliver(job,Destination(m.channel,m.account_id,m.conversation_id,m.thread_id),'I could not open the attached image. Please resend it as a smaller PNG or JPEG.\n\nModel: none (service notice)')
                return True
            except Exception:
                self.ledger.defer(job.job_id,min(30,2**job.retries));self.last_failure='Conversation retrieval failed; a bounded retry is queued.';return True
            self.last_timings['ingress_fetch_s']=time.monotonic()-fetch_started
            if envelope is None:self.ledger.discard(job.job_id);return True
            decision=self.policy.decide(self.registry.authenticate(envelope))
            self.ledger.start(job.job_id)
            if decision.access.value=='owner' and CANCEL.fullmatch(decision.event.text.strip()):
                self._deliver(job,decision.destination,'The active request was cancelled, if one was running.\n\nModel: none (service notice)');return True
            model_started=time.monotonic()
            try:
                self.runtime.stage_images(images)
                reply=self.service.handle(envelope);text=reply.text;destination=reply.destination;outbound=reply.images
            except Exception:
                text='I could not finish this request. Some actions may have started, so I did not replay them. Please check the result before trying again.';destination=decision.destination;outbound=()
                models=getattr(self.runtime,'last_models',())
                if models:text+='\n\nModels attempted: '+' → '.join(models)
                else:text+='\n\nModel: none (service notice)'
                self.last_failure='Runtime request failed or was cancelled; no automatic action replay.'
            self.last_timings['model_tools_s']=time.monotonic()-model_started
            self.last_timings['runtime_startup_s']=getattr(self.runtime,'last_timings',{}).get('runtime_startup_s',0)
            self._deliver(job,destination,text,outbound)
            return True
        except Exception:
            self.ledger.recover_job(job.job_id);raise
        finally:
            self.last_timings['total_s']=time.monotonic()-began
            self._completed_timings=dict(self.last_timings);self.active_job=None
    def run_forever(self):
        def worker(lane):
            while not self.stopped.is_set():
                try:worked=self.run_once(lane)
                except Exception:
                    self.last_failure='Request orchestration failed; check status before retrying.'
                    # run_once recovers only its own job; the other lane may
                    # still be running a native request or delivering a result.
                    worked=False
                if not worked:self.stopped.wait(.1)
        threads=[threading.Thread(target=worker,args=(lane,),name='sebastian-'+lane,daemon=True) for lane in ('normal','image')]
        for thread in threads:thread.start()
        reconnect_at=0;attempts=0
        try:
            while not self.stopped.is_set():
                try:self.sources.poll_messages()
                except Exception:self.last_failure='Messages is unavailable; check local permissions and account status.'
                if not self.sources.client.is_connected() and time.monotonic()>=reconnect_at:
                    try:self.sources.receiver.connect();attempts=0
                    except Exception:
                        attempts+=1;self.last_failure='Slack connection is unavailable; reconnecting with backoff.'
                    reconnect_at=time.monotonic()+min(60,2**min(attempts,6))
                self.stopped.wait(1)
        finally:
            self.runtime.cancel()
            for thread in threads:thread.join(timeout=30)
            if any(thread.is_alive() for thread in threads):raise RuntimeError('Worker did not stop cleanly')
    def close(self):
        self.stopped.set();self.runtime.cancel();self.sources.close();self.runtime.close();self.ledger.close()
