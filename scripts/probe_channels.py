#!/usr/bin/env python3
"""Read-only channel identity/readiness check. Never generates or sends replies."""
import json,logging,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sebastian.config import load_settings
from sebastian.contracts import Channel
from sebastian.policy import AdapterRegistry
from sebastian.messages import MessagesAdapter
from sebastian.slack import SlackConfig,SlackAPI,socket_client
root=Path(__file__).resolve().parents[1]
s=load_settings(root/'.private/config/installation.json')
bot,app=s.slack_credentials()
logging.getLogger('slack_sdk').disabled=True
client=socket_client(app,bot)
try:
 cfg=SlackConfig(*(s.slack[k] for k in ['team_id','bot_user_id','bot_id','app_id','owner_user_id']),datetime.now(timezone.utc))
 api=SlackAPI(client.web_client,cfg);identity=api.inspect_identity()
 dm=api.conversation(s.slack['owner_dm'])
 assert dm.get('is_im') and dm.get('user')==s.slack['owner_user_id']
 result={'slack_identity_verified':True,'slack_scopes':identity['scopes'],'existing_owner_dm_verified':True,'owner_group_replies':s.policy.owner_group_replies}
 if '--socket' in sys.argv:
  # Connection handshake only: no receiver, no history, no messages, no model.
  client.connect();result['socket_connected']=client.is_connected()
  assert result['socket_connected']
 registry=AdapterRegistry();signer=registry.register('messages',Channel.MESSAGES,s.messages.account_id)
 adapter=MessagesAdapter(s.messages_db,s.messages,signer)
 try:
  result['messages_readonly']=adapter.db.execute('PRAGMA query_only').fetchone()[0]==1
  result['latest_owner_service_metadata']={}
  for service in ['iMessage','SMS','RCS']:
   row=adapter.db.execute('SELECT m.account,m.account_guid,c.account_login FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID JOIN chat c ON c.ROWID=j.chat_id WHERE m.is_from_me=1 AND m.service=? ORDER BY m.ROWID DESC LIMIT 1',(service,)).fetchone()
   matched=bool(row and (row[0],row[1]) in s.messages.account_pairs and row[2] in s.messages.chat_logins)
   result['latest_owner_service_metadata'][service]=matched
   assert matched
  result['messages_self_chat_count']=len(s.messages.owner_private_chat_ids)
  result['first_start_backlog_excluded']=adapter.highwater==adapter.db.execute('SELECT COALESCE(MAX(ROWID),0) FROM message').fetchone()[0]
 finally:adapter.close()
 print(json.dumps(result))
finally:client.close()
