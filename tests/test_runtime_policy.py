import tempfile,unittest
from pathlib import Path
from sebastian.runtime_policy import make_runtime_policy,PermissionDenied
from sebastian.toolbelt_reader import ToolbeltReader
class RuntimePolicyTests(unittest.TestCase):
 def test_restricted_policy_removes_tools_and_memory(self):
  p=make_runtime_policy('conversation',Path('/tmp/probe'),{'mcp_servers':{'private':{}},'plugins':{'x@remote':{}},'apps':{'specific':{'enabled':True}}})
  self.assertEqual(p.thread_params()['environments'],[]);self.assertFalse(p.config['features.apps']);self.assertFalse(p.config['apps.specific.enabled']);self.assertFalse(p.config['memories.use_memories']);self.assertFalse(p.config['mcp_servers.private.enabled'])
  for tool in ['shell','github.get_profile','cua_repl.js','toolbelt_read']:
   with self.assertRaises(PermissionDenied):p.authorize_tool(tool)
  with self.assertRaises(PermissionDenied):p.verify_mcp_inventory({'data':[{'tools':{'secret':{}}}]})
  with self.assertRaises(PermissionDenied):p.verify_mcp_inventory({'data':[],'nextCursor':'more'})
 def test_custom_private_instructions_fail_closed(self):
  with self.assertRaises(PermissionDenied):make_runtime_policy('conversation',Path('/tmp/probe'),{'model_instructions_file':'/private/prompt'})
 def test_named_teammate_only_has_curated_read(self):
  p=make_runtime_policy('toolbelt_read_only',Path('/tmp/probe'),{});p.authorize_tool('toolbelt_read')
  with self.assertRaises(PermissionDenied):p.authorize_tool('exec')
 def test_read_scope_cannot_escape_or_follow_symlink(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'doc.md').write_text('Reviewed product requirements');reader=ToolbeltReader(root,{'prd':'doc.md'})
   self.assertEqual(reader.read({'document_id':'prd'}),'Reviewed product requirements')
   with self.assertRaises(PermissionDenied):reader.read({'document_id':'../secret'})
   with self.assertRaises(PermissionDenied):reader.read({'document_id':'prd','path':'/etc/passwd'})
   (root/'doc.md').unlink();(root/'doc.md').symlink_to('/etc/passwd')
   with self.assertRaises(PermissionDenied):reader.read({'document_id':'prd'})
 def test_credential_documents_are_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'doc.md').write_text('password = "synthetic_secret_password"')
   with self.assertRaises(PermissionDenied):ToolbeltReader(root,{'prd':'doc.md'}).read({'document_id':'prd'})
   with self.assertRaises(ValueError):ToolbeltReader(root,{'env':'.env'})
if __name__=='__main__':unittest.main()
