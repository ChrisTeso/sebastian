#!/usr/bin/env python3
"""Read-only receipts for the explicitly authorized synthetic image checks."""
import json,os,sqlite3,sys,time
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sebastian.config import load_settings
from sebastian.slack import SlackConfig,SlackAPI,socket_client
from sebastian.messages import MessagesAdapter
from sebastian.policy import AdapterRegistry
from sebastian.contracts import Channel
from sebastian.outbound import image_marker
ROOT=Path(__file__).resolve().parents[1]
def verify(since):
 s=load_settings(ROOT/'.private/config/installation.json');r=AdapterRegistry()
 a=MessagesAdapter(s.messages_db,s.messages,r.register('check',Channel.MESSAGES,s.messages.account_id))
 bot,app=s.slack_credentials();client=socket_client(app,bot)
 cfg=SlackConfig(*(s.slack[k] for k in ['team_id','bot_user_id','bot_id','app_id','owner_user_id']),datetime.now(timezone.utc))
 api=SlackAPI(client.web_client,cfg);api.inspect_identity()
 db=sqlite3.connect((s.state_dir/'ledger.sqlite').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 result={'checked_at':time.time(),'since':since,'tests':{}}
 try:
  for job in db.execute('SELECT * FROM jobs WHERE ingressed>? ORDER BY ingressed',(since,)):
   if job['channel']=='slack' and job['conversation']==s.slack['owner_dm']:
    args=dict(channel=job['conversation'],oldest=job['event'],latest=job['event'],inclusive=True,limit=1)
    res=api.client.conversations_replies(ts=job['thread'],**args) if job['thread'] else api.client.conversations_history(**args)
    row=next((x for x in res.get('messages',[]) if x.get('ts')==job['event']),{})
    if row.get('user')!=cfg.owner_user_id or 'IMAGE-LIVE-' not in row.get('text',''):continue
   elif job['channel']=='messages' and job['conversation'] in s.messages.owner_private_chat_ids:
    rows=a._rows('m.guid=? AND c.guid=?',(job['event'],job['conversation']),2)
    e=a._record(rows[0]) if len(rows)==1 else None
    if not e or not e.owner_metadata_verified or 'IMAGE-LIVE-' not in e.text:continue
   else:continue
   receipts=[]
   for d in db.execute('SELECT * FROM deliveries WHERE job=? ORDER BY part',(job['id'],)):
    if d['status']!='confirmed' or not d['receipt']:continue
    same=(d['channel'],d['account'],d['conversation'],d['thread'])==(job['channel'],job['account'],job['conversation'],job['thread'])
    if job['channel']=='slack' and d['receipt'].startswith('F'):
     f=api.client.files_info(file=d['receipt']).get('file',{})
     shares=[x for groups in f.get('shares',{}).values() for channel,items in groups.items() if channel==d['conversation'] for x in items]
     matched=[x for x in shares if x.get('thread_ts')==d['thread']]
     receipts.append({'same_destination':same,'provider_id':d['receipt'],'image':f.get('mimetype','').startswith('image/'),'bytes':f.get('size'),'bot_owned':f.get('user')==cfg.bot_user_id,'shared_in_original_thread':bool(matched)})
    elif job['channel']=='messages':
     rows=a._rows('m.guid=? AND c.guid=?',(d['receipt'],d['conversation']),2)
     e=a._record(rows[0]) if len(rows)==1 else None
     if not e or not e.attachments:continue
     files=a.db.execute('SELECT a.filename,a.total_bytes,a.transfer_state FROM attachment a JOIN message_attachment_join ma ON ma.attachment_id=a.ROWID JOIN message m ON m.ROWID=ma.message_id WHERE m.guid=?',(e.event_id,)).fetchall()
     receipts.append({'same_destination':same,'provider_id':e.event_id,'owner_verified':e.owner_metadata_verified,'image':any(image_marker(f[0] or '') for f in files),'attachment_copied':all(Path(f[0]).expanduser().is_file() and f[1]>0 for f in files),'transfer_states':[f[2] for f in files],'service':dict(rows[0]).get('service'),'message_error':dict(rows[0]).get('error'),'is_sent':dict(rows[0]).get('is_sent')})
   key=job['channel'];previous=result['tests'].get(key,{})
   result['tests'][key]={'job_id':job['id'],'completed':job['status']=='complete','matching_request_jobs':previous.get('matching_request_jobs',0)+1,'images':receipts,'delivery_states':[d['status'] for d in db.execute('SELECT status FROM deliveries WHERE job=?',(job['id'],))]}
  result['unexpected_messages_jobs']=sum(1 for row in db.execute("SELECT id FROM jobs WHERE ingressed>? AND channel='messages'",(since,)) if row['id']!=result['tests'].get('messages',{}).get('job_id'))
 finally:a.close();client.close();db.close()
 return result
if __name__=='__main__':
 os.umask(0o077)
 result=verify(float(sys.argv[1]));out=ROOT/'.private/image-live';out.mkdir(mode=0o700,exist_ok=True)
 (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
