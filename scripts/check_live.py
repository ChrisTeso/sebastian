#!/usr/bin/env python3
"""Read-only verification of explicitly requested synthetic owner live tests."""
import json,os,sqlite3,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sebastian.config import load_settings
from sebastian.contracts import Channel
from sebastian.policy import AdapterRegistry
from sebastian.slack import SlackAPI,SlackConfig,socket_client
from sebastian.messages import MessagesAdapter
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
MARKERS={'slack':'SLACK-LIVE-739126','messages':'MESSAGES-LIVE-739127','computer':'CHROME-LIVE-739126'}
def verify():
    os.umask(0o077)
    settings=load_settings(ROOT/'.private/config/installation.json')
    result={'checked_at':time.time(),'tests':{},'native_transcripts_owner_only':False}
    matching_messages_jobs = 0
    path=settings.state_dir/'ledger.sqlite'
    if not path.exists():return result
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    bot,app=settings.slack_credentials();client=socket_client(app,bot)
    cfg=SlackConfig(*(settings.slack[k] for k in ['team_id','bot_user_id','bot_id','app_id','owner_user_id']),datetime.now(timezone.utc))
    api=SlackAPI(client.web_client,cfg);api.inspect_identity()
    registry=AdapterRegistry();signer=registry.register('messages',Channel.MESSAGES,settings.messages.account_id)
    messages=MessagesAdapter(settings.messages_db,settings.messages,signer)
    def slack_row(channel,stamp,thread):
        args=dict(channel=channel,oldest=stamp,latest=stamp,inclusive=True,limit=1)
        response=api.client.conversations_replies(ts=thread,**args) if thread else api.client.conversations_history(**args)
        return next((r for r in response.get('messages',[]) if r.get('ts')==stamp),None)
    try:
        jobs=db.execute('SELECT * FROM jobs WHERE ingressed>? ORDER BY ingressed',(time.time()-7200,)).fetchall()
        for job in jobs:
            if job['channel']=='slack' and job['account']==cfg.team_id and job['conversation']==settings.slack['owner_dm']:
                incoming=slack_row(job['conversation'],job['event'],job['thread'])
                if not incoming or incoming.get('user')!=cfg.owner_user_id:continue
                text=incoming.get('text','')
                test=next((name for name in ('slack','computer') if MARKERS[name] in text),None)
            elif job['channel']=='messages' and job['account']==settings.messages.account_id and job['conversation'] in settings.messages.owner_private_chat_ids:
                rows=messages._rows('m.guid=? AND c.guid=?',(job['event'],job['conversation']),2)
                incoming=messages._record(rows[0]) if len(rows)==1 else None
                if not incoming:continue
                if MARKERS['messages'] in incoming.text:matching_messages_jobs += 1
                if not incoming.owner_metadata_verified or not incoming.is_from_me:continue
                text=incoming.text;test='messages' if MARKERS['messages'] in text else None
            else:continue
            if not test:continue
            deliveries=db.execute('SELECT * FROM deliveries WHERE job=? ORDER BY part',(job['id'],)).fetchall()
            verified=[];response_matches=[]
            for d in deliveries:
                if d['status']!='confirmed' or not d['receipt']:continue
                same=(d['channel'],d['account'],d['conversation'],d['thread'])==(job['channel'],job['account'],job['conversation'],job['thread'])
                if test=='messages':
                    rows=messages._rows('m.guid=? AND c.guid=?',(d['receipt'],d['conversation']),2)
                    outgoing=messages._record(rows[0]) if len(rows)==1 else None
                    sender=bool(outgoing and outgoing.is_from_me and outgoing.owner_metadata_verified)
                    body=outgoing.text if outgoing else ''
                else:
                    outgoing=slack_row(d['conversation'],d['receipt'],d['thread'])
                    sender=bool(outgoing and outgoing.get('user')==cfg.bot_user_id and outgoing.get('bot_id')==cfg.bot_id)
                    body=outgoing.get('text','') if outgoing else ''
                verified.append(same and sender)
                response_matches.append(MARKERS[test] in body and ('Example Domain' in body if test=='computer' else body.strip()==MARKERS[test]))
            result['tests'][test]={'job_id':job['id'],'event_id':job['event'],'ingressed_at':job['ingressed'],'completed':job['status']=='complete','delivery_count':len(deliveries),'confirmed_receipts':len(verified),'same_conversation_and_sender':bool(verified) and all(verified),'expected_final':bool(response_matches) and all(response_matches)}
        if 'messages' in result['tests']:
            result['tests']['messages']['matching_request_jobs']=matching_messages_jobs
            result['tests']['messages']['no_duplicate_job']=matching_messages_jobs==1
        metadata=settings.state_dir/'sessions.json'
        ids=set(json.loads(metadata.read_text()).values()) if metadata.exists() else set()
        files=[p for sid in ids for p in (Path.home()/'.codex/sessions').glob('**/*'+sid+'*')]
        result['native_transcript_count']=len(files)
        result['native_transcripts_owner_only']=bool(files) and len(files)==len(ids) and all(p.stat().st_uid==os.getuid() and p.stat().st_mode&0o077==0 for p in files)
    finally:
        messages.close();client.close();db.close()
    return result
if __name__=='__main__':
    try:
        result=verify();out=ROOT/'.private/s7-live';out.mkdir(mode=0o700,exist_ok=True)
        (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    except Exception:
        print(json.dumps({'check_failed':True}));sys.exit(1)
