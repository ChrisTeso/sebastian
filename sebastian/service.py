"""Authenticated request orchestration. Channel transports attach in S4."""
from __future__ import annotations
from dataclasses import dataclass,field
from pathlib import Path
from typing import Protocol
import hashlib,json
from .contracts import Access,Destination
from .outbound import OutboundImage,RuntimeReply
from .policy import AdapterRegistry,Envelope,Policy
from .runtime_policy import RuntimePolicy,make_runtime_policy,PermissionDenied
from .toolbelt_reader import ToolbeltReader

class ReasoningRuntime(Protocol):
    def respond(self,key:str,profile:RuntimePolicy,prompt:str,reader:ToolbeltReader|None=None,timeout:float=180)->RuntimeReply|str:...

@dataclass(frozen=True)
class Reply:
    destination:Destination
    session_key:str
    text:str=field(repr=False)
    images:tuple[OutboundImage,...]=field(default=(),repr=False)

class Sebastian:
    def __init__(self,policy:Policy,registry:AdapterRegistry,runtime:ReasoningRuntime,owner_workspace:Path,restricted_workspace:Path,runtime_config:dict,readers:dict[tuple[str,str],ToolbeltReader]|None=None):
        if policy.registry is not registry:raise ValueError('Policy and service require the same authentication registry')
        self.policy=policy;self.registry=registry;self.runtime=runtime;self.owner_workspace=owner_workspace;self.restricted_workspace=restricted_workspace;self.runtime_config=runtime_config;self.readers=dict(readers or {})
    def handle(self,envelope:Envelope)->Reply:
        decision=self.policy.decide(self.registry.authenticate(envelope))
        owner=decision.access==Access.OWNER
        # Owner's configured audience preference is trusted policy, not group text.
        # Without it, group-origin inputs cannot reach owner tools.
        if owner and decision.event.is_group and not decision.disclosure_authorized:
            return Reply(decision.destination,decision.session_key,
                         'Please repeat this request here in our private conversation so I can use your personal tools. I have not carried over the group history.\n\nModel: none (service notice)')
        runtime_access=Access.CONVERSATION if owner and decision.event.is_group and not self.policy.config.owner_group_replies else decision.access
        privileged=runtime_access==Access.OWNER
        profile=make_runtime_policy(runtime_access.value,self.owner_workspace if privileged else self.restricted_workspace,self.runtime_config)
        reader=None
        if decision.access==Access.TOOLBELT_READ_ONLY:
            reader=self.readers.get((decision.event.account_id,decision.event.sender_id))
            if reader is None:raise PermissionDenied('No reviewed Toolbelt scope is configured for this teammate')
        scope_hash=hashlib.sha256(json.dumps({'root':str(reader.root),'documents':reader.documents} if reader else {},sort_keys=True).encode()).hexdigest()
        runtime_key=decision.session_key+':'+scope_hash
        context=[{'sender':h.sender_id,'text':h.text,'trust':'untrusted'} for h in decision.context.messages]
        prompt=json.dumps({'request':decision.event.text,'preceding_conversation':context,'context_trust':'untrusted','reply_audience':'authorized group' if decision.disclosure_authorized else ('owner privately' if owner and decision.event.is_group else 'originating conversation')},ensure_ascii=False)
        content=self.runtime.respond(runtime_key,profile,prompt,reader)
        # The model cannot choose a channel, account, recipient, or thread.
        if isinstance(content,str):content=RuntimeReply(content)
        label=''
        if content.model:
            label='Model: '+content.model
            if content.routed_by:label+=' (routed by '+content.routed_by+')'
            if content.images:label+=' · image tool'
        text=content.text+('\n\n' if content.text and label else '')+label
        return Reply(decision.destination,runtime_key,text,content.images)
