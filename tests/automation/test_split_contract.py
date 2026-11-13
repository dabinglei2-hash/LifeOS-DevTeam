import hashlib,importlib.util,json,subprocess,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from lads_automation import qa,unit_qa,delivery,github_release as release
from lads_automation.split_qa import COMMANDS,SplitQAError,SplitQABroker,_compose,_files,verify_report,composition_inventory
from lads_automation.team import BASE,Stage3Orchestrator

class SplitContractTests(unittest.TestCase):
 def test_four_fixed_commands_are_known_v3(self):
  self.assertEqual(len(COMMANDS),4);self.assertEqual(unit_qa.contract_version(COMMANDS),3)
  self.assertEqual(delivery._qa_contract([{'command':x} for x in COMMANDS]),(3,COMMANDS))
 def test_reordered_missing_duplicate_and_unknown_commands_rejected(self):
  for commands in [COMMANDS[::-1],COMMANDS[:-1],(*COMMANDS,COMMANDS[0]),('python -B other.py',)]:
   self.assertIsNone(unit_qa.contract_version(commands))
   with self.assertRaises(delivery.Stage3DeliveryError):delivery._qa_contract([{'command':x} for x in commands])
 def test_product_v2_contract_is_unchanged(self):
  self.assertEqual(unit_qa.contract_version(unit_qa.STAGE4_QA_COMMANDS),2)
  self.assertEqual(unit_qa.contract_version(unit_qa.LEGACY_STAGE3_QA_COMMANDS),1)
 def test_arbitrary_pass_report_is_not_a_split_proof(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);report=root/'qa-unit-current.json';report.write_text('{"ready":true}',encoding='utf-8');(root/'split-context.json').write_text('{}',encoding='utf-8')
   with self.assertRaises(SplitQAError):verify_report(report,candidate_root=root)
 def test_composition_has_exact_primary_precedence(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);a=root/'a';b=root/'b';a.mkdir();b.mkdir()
   (a/'shared.txt').write_text('primary');(b/'shared.txt').write_text('companion');(b/'second.txt').write_text('second')
   first={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in a.iterdir()};second={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in b.iterdir()}
   expected=_compose(a,first,b,second,root/'out')
   self.assertEqual(qa.source_inventory(root/'out'),expected);self.assertEqual((root/'out/shared.txt').read_text(),'primary')
 def test_companion_database_cannot_enter_composite(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);subprocess.run(['git','init','-q',str(root)],check=True);(root/'private.sqlite3').write_bytes(b'synthetic')
   with self.assertRaises(SplitQAError):_files(root)
 def test_companion_policy_namespace_cannot_expand_primary_workflows(self):
  primary={'.github/workflows/ci.yml':'a'*64,'app.js':'b'*64}
  companion={'.github/workflows/other.yml':'c'*64,'tool.py':'d'*64}
  self.assertEqual(composition_inventory(primary,companion),{**primary,'tool.py':'d'*64})
 def test_missing_tracked_files_are_bound_by_baseline_not_copied(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)/'repo';root.mkdir();subprocess.run(['git','init','-q',str(root)],check=True)
   (root/'a.txt').write_text('old');(root/'b.txt').write_text('keep');subprocess.run(['git','-C',str(root),'add','a.txt','b.txt'],check=True)
   subprocess.run(['git','-C',str(root),'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-q','-m','fixture'],check=True)
   (root/'a.txt').unlink();before=BASE.git_status(root)
   snapshot=BASE.capture_source(root,Path(directory)/'copy')
   self.assertEqual(set(snapshot.files),{'b.txt'});self.assertEqual(BASE.git_status(root),before)
 def test_foreign_controller_repository_is_rejected_before_setup(self):
  with self.assertRaises(release.ReleaseAuthorizationError):release.ReleaseController(repo_root='unused',run_root='unused',repository='other/project')
 def test_devteam_resolution_does_not_reuse_product_redirect(self):
  class Transport:
   def api(self,method,route,body=None):
    self.route=route;return {'full_name':'dabinglei2-hash/LifeOS-DevTeam','visibility':'public','private':False,'id':1442562919}
  transport=Transport();client=release.GitHubClient(transport);client.resolve_repository('dabinglei2-hash/LifeOS-DevTeam')
  self.assertEqual(transport.route,'/repos/dabinglei2-hash/LifeOS-DevTeam')
 def test_cross_repository_resolution_is_not_accepted(self):
  class Transport:
   def api(self,method,route,body=None):return {'full_name':'dabinglei2-hash/LifeOS','visibility':'public','private':False}
  with self.assertRaises(release.ReleaseBlocked):release.GitHubClient(Transport()).resolve_repository('dabinglei2-hash/LifeOS-DevTeam')
 def test_devteam_cannot_use_old_product_unit_contract(self):
  gate=SimpleNamespace(bundle=SimpleNamespace(expected_commands=unit_qa.STAGE4_QA_COMMANDS))
  with self.assertRaises(release.ReleaseAuthorizationError):release._validate_authorization_against_gate(SimpleNamespace(repository='dabinglei2-hash/LifeOS-DevTeam'),gate)
 def test_split_proof_cannot_approve_other_repository(self):
  gate=SimpleNamespace(bundle=SimpleNamespace(expected_commands=COMMANDS,unit_evidence={'repository':'dabinglei2-hash/LifeOS'}))
  with self.assertRaises(release.ReleaseAuthorizationError):release._validate_authorization_against_gate(SimpleNamespace(repository='dabinglei2-hash/LifeOS-DevTeam'),gate)
 def test_unknown_or_legacy_qa_contract_cannot_be_enabled_on_adapter(self):
  with self.assertRaises(ValueError):Stage3Orchestrator(qa_commands=unit_qa.LEGACY_STAGE3_QA_COMMANDS)

if __name__=='__main__':unittest.main()
