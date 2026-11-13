"""Current dual-repository policy tests, distinct from legacy workflow fixtures."""
import importlib.util,json,re,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('repository_safety',ROOT/'scripts/repository_check.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class DualRepositorySafetyTests(unittest.TestCase):
 def test_current_workflows_are_readonly_and_do_not_use_untrusted_privileged_runners(self):
  paths=list((ROOT/'.github/workflows').glob('*.yml'))
  self.assertEqual(len(paths),5)
  for p in paths:
   with self.subTest(path=p.name):
    s=p.read_text(encoding='utf-8')
    self.assertIn('contents: read',s);self.assertNotIn('pull_request_target',s);self.assertNotIn('self-hosted',s)
    self.assertNotIn('secrets.',s);self.assertNotIn('contents: write',s)
    for action in re.findall(r'uses:\s*(\S+)',s):self.assertRegex(action,r'@[a-f0-9]{40}$')
 def test_product_revision_must_be_explicit_without_stale_fallback(self):
  s=(ROOT/'.github/workflows/ci.yml').read_text(encoding='utf-8')
  self.assertIn('vars.LIFEOS_PRODUCT_QA_SHA',s);self.assertIn('no stale fallback',s)
  self.assertNotIn('checkout 607fb7687e7966ef1acfad02c07e558625044d2c',s)
 def test_mcp_stays_disabled_and_has_no_embedded_server_credentials(self):
  s=json.loads((ROOT/'config/mcp-policy.json').read_text(encoding='utf-8'))
  self.assertIs(s['enabled'],False);self.assertEqual(s['servers'],{})
 def test_empty_source_is_not_a_passing_audit(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);subprocess.run(['git','init','-q',str(root)],check=True)
   for mode in ('ownership','security'):
    result=module.check(root,'devteam',mode)
    self.assertEqual(result['status'],'FAIL');self.assertIn('empty-public-source',{x['rule'] for x in result['issues']})
 def test_secret_scan_rejects_synthetic_secret_without_echoing_it(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);subprocess.run(['git','init','-q',str(root)],check=True)
   value='sk-'+('a' *32);(root/'example.py').write_text('credential = '+repr(value),encoding='utf-8')
   result=module.check(root,'devteam','security')
   self.assertEqual(result['status'],'FAIL');self.assertNotIn(value,json.dumps(result))
 def test_private_database_and_product_source_are_rejected(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);subprocess.run(['git','init','-q',str(root)],check=True)
   (root/'knowledge.sqlite3').write_bytes(b'synthetic');(root/'app.js').write_text('const synthetic=true;',encoding='utf-8')
   result=module.check(root,'devteam','ownership')
   self.assertEqual({x['rule'] for x in result['issues']},{'private-path','product-in-engineering'})
 def test_force_tracked_private_runtime_is_rejected(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);subprocess.run(['git','init','-q',str(root)],check=True)
   (root/'.gitignore').write_text('/work/\n',encoding='utf-8')
   private=root/'work/lads/private.json';private.parent.mkdir(parents=True);private.write_text('{"synthetic":true}',encoding='utf-8')
   subprocess.run(['git','-C',str(root),'add','-f','--','work/lads/private.json'],check=True)
   result=module.check(root,'devteam','security')
   self.assertEqual(result['status'],'FAIL');self.assertIn({'path':'work/lads/private.json','rule':'private-path'},result['issues'])
 def test_hooks_use_selected_runtime_without_machine_private_path(self):
  s=(ROOT/'.codex/hooks.json').read_text(encoding='utf-8')
  self.assertNotIn('C:/Users/',s);self.assertIn('LADS_PYTHON',s)
  launcher=(ROOT/'scripts/lads_session.py').read_text(encoding='utf-8')
  self.assertIn('sys.executable',launcher);self.assertIn('env["LADS_PYTHON"]',launcher)

if __name__=='__main__':unittest.main()
