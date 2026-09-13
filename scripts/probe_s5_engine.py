#!/usr/bin/env python3
"""Synthetic runtime -> durable engine -> in-memory transport, no channel sends."""
import json,os,sys,time
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sebastian.contracts import Channel,Event
from sebastian.policy import AdapterRegistry,PolicyConfig
from sebastian.engine import Engine
from sebastian.ledger import JobMetadata
from sebastian.session_runtime import SessionRuntime
os.umask(0o077)
root=Path(__file__).resolve().parents[1]/'.private/s5-engine';root.mkdir(mode=0o700,exist_ok=True)
event=Event(Channel.SLACK,'SYNTHETIC','GUEST','FIXTURE-GROUP','FIXTURE-THREAD','fixture-'+str(time.time_ns()),datetime.now(timezone.utc),'@sebastian Reply only S5-ENGINE-OK',is_group=True)
class Transport:
 def __init__(self):
  self.registry=AdapterRegistry();self.signer=self.registry.register('fixture',Channel.SLACK,'SYNTHETIC');self.sent=[]
 def fetch(self,job):return self.signer.sign(event),[]
 def send(self,destination,text):self.sent.append((destination,text));return 'synthetic-receipt'
 def close(self):pass
settings=SimpleNamespace(policy=PolicyConfig(owner_group_replies=True),state_dir=root,owner_workspace=root,restricted_workspace=root)
runtime=SessionRuntime(root/'sessions.json');transport=Transport();engine=Engine(settings,runtime,sources=transport)
try:
 engine.ledger.accept(JobMetadata.from_event(event));engine.ledger.accept(JobMetadata.from_event(event))
 assert engine.run_once();assert not engine.run_once()
 assert len(transport.sent)==1 and 'S5-ENGINE-OK' in transport.sent[0][1]
 assert transport.sent[0][0].conversation_id=='FIXTURE-GROUP' and transport.sent[0][0].thread_id=='FIXTURE-THREAD'
 result={'native_final_delivered_to_fixture':True,'duplicate_suppressed':True,'destination_preserved':True,'timings':engine.last_timings,'ledger':engine.ledger.stats()}
 (root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
finally:engine.close()
