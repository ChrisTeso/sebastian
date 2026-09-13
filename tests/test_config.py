import json,os,tempfile,unittest
from pathlib import Path
from sebastian.config import load_settings,private_read

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_secret_modes_links_and_ownership(self):
        p=self.root/'token';p.write_text('synthetic');p.chmod(0o600)
        self.assertEqual(private_read(p),'synthetic')
        p.chmod(0o644)
        with self.assertRaises(PermissionError):private_read(p)
        p.chmod(0o600);link=self.root/'link';link.symlink_to(p)
        with self.assertRaises(OSError):private_read(link)
        os.link(p,self.root/'hardlink')
        with self.assertRaises(PermissionError):private_read(p)
    def test_same_chat_is_required_and_identities_are_exact(self):
        c={'version':1,'owner_group_replies':True,'slack':{'team_id':'TEAM','owner_user_id':'OWNER','owner_dm':'DM'},'messages':{'account_id':'ACCOUNT','sender_id':'SELF','account_pairs':[['LOGIN','GUID']],'chat_logins':['LOGIN'],'owner_private_chat_ids':['SELFCHAT']},'messages_db':'/tmp/db','owner_workspace':'/tmp/owner','restricted_workspace':'/tmp/guest','state_dir':'/tmp/state'}
        p=self.root/'config';p.write_text(json.dumps(c));p.chmod(0o600)
        s=load_settings(p)
        self.assertTrue(s.policy.owner_group_replies)
        self.assertEqual(s.policy.owner_slack,frozenset({('TEAM','OWNER')}))
        self.assertEqual(s.messages.owner_private_chat_ids,frozenset({'SELFCHAT'}))
        for invalid in [False,'true',None]:
            c['owner_group_replies']=invalid;p.write_text(json.dumps(c))
            with self.assertRaises(ValueError):load_settings(p)
