#!/usr/bin/env python3
"""Controlled native warm replies; fixture ingress/delivery, no channel sends."""
import argparse,json,math,os,statistics,sys,time
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sebastian.contracts import Channel,Destination,Event
from sebastian.policy import AdapterRegistry,PolicyConfig
from sebastian.engine import Engine
from sebastian.formatting import SIGNATURE
from sebastian.ledger import JobMetadata
from sebastian.session_runtime import SessionRuntime,_Connection
parser=argparse.ArgumentParser();parser.add_argument('--runs',type=int,default=20);parser.add_argument('--model');args=parser.parse_args()
if args.runs<20:raise SystemExit('At least 20 warm runs are required')
os.umask(0o077)
root=Path(__file__).resolve().parents[1]/'.private/s6-latency'/str(time.time_ns());root.mkdir(parents=True,mode=0o700)
for p in [root.parent,root]:p.chmod(0o700)
class Transport:
 def __init__(self):
  self.registry=AdapterRegistry();self.signer=self.registry.register('fixture',Channel.SLACK,'BENCH');self.event=None;self.sent=[]
 def fetch(self,job):return self.signer.sign(self.event),[]
 def send(self,destination,text):self.sent.append((destination,text));return 'fixture-'+str(len(self.sent))
 def close(self):pass
transport=Transport();policy=PolicyConfig(owner_slack=frozenset({('BENCH','OWNER')}),owner_group_replies=True,owner_destinations=(Destination(Channel.SLACK,'BENCH','DM'),))
settings=SimpleNamespace(policy=policy,state_dir=root,owner_workspace=root,restricted_workspace=root)
runtime=SessionRuntime(root/'sessions.json',model=args.model);engine=Engine(settings,runtime,sources=transport)
original_next=_Connection._next;tools=[]
def inspected_next(self,deadline):
 message=original_next(self,deadline)
 if message.get('method')=='item/completed':
  item=message.get('params',{}).get('item',{})
  if item.get('type') not in ('userMessage','agentMessage'):tools.append(item.get('type'))
 return message
_Connection._next=inspected_next
results=[]
try:
 for index in range(args.runs+1):
  expected=str(index+17);stamp=time.time_ns()
  transport.event=Event(Channel.SLACK,'BENCH','OWNER','DM',None,'fixture-'+str(stamp),datetime.now(timezone.utc),f'What is {index} + 17? Reply with only the number. Do not use tools.')
  ingress=time.monotonic();engine.ledger.accept(JobMetadata.from_event(transport.event));accepted=time.monotonic()
  before=len(tools);engine.run_once();elapsed=time.monotonic()-ingress
  correct=transport.sent[-1][1].strip()==expected+'\n\n'+SIGNATURE
  row={'run':index,'warm':index>0,'elapsed_s':elapsed,'ingress_accept_s':accepted-ingress,**engine.last_timings,'correct':correct,'tool_items':tools[before:]}
  results.append(row)
  if index%5==0:print(json.dumps({'completed':index,'elapsed_s':round(elapsed,3),'correct':correct}),flush=True)
 warm=[r['elapsed_s'] for r in results[1:]];p95=sorted(warm)[math.ceil(.95*len(warm))-1]
 summary={'runs':args.runs,'median_s':statistics.median(warm),'p95_s':p95,'all_correct':all(r['correct'] for r in results),'no_tools':all(not r['tool_items'] for r in results),'target_pass':statistics.median(warm)<5 and p95<10,'transport':'fixture ingress and delivery; actual selected native owner runtime','model_override':args.model,'cold_s':results[0]['elapsed_s'],'stage_medians':{k:statistics.median(r.get(k,0) for r in results[1:]) for k in ['ingress_accept_s','queue_wait_s','ingress_fetch_s','runtime_startup_s','model_tools_s','delivery_s']}}
 (root/'results.json').write_text(json.dumps({'summary':summary,'runs':results},indent=2));print(json.dumps(summary),flush=True);print('Evidence:',root/'results.json')
finally:_Connection._next=original_next;engine.close()
