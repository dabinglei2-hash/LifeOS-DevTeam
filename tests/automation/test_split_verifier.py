"""Outer binding regressions; inner verifier mocks are NOT live QA evidence."""
import copy,hashlib,json,tempfile,unittest,uuid
from pathlib import Path
from unittest import mock
from lads_automation import qa,unit_qa
from lads_automation.split_qa import COMMANDS,LADS_COMMAND,SCHEMA,REPOSITORIES,_compose,_write,verify_report

class SplitVerifierBindingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name).resolve();self.task=str(uuid.uuid4());self.run=self.base/self.task;self.run.mkdir();self.candidate=self.run/'candidate';self.candidate.mkdir();(self.candidate/'tool.py').write_text('synthetic')
  self.artifact=self.base/('split-'+self.task+'-'+uuid.uuid4().hex);self.artifact.mkdir();companion=self.artifact/'companion';companion.mkdir();(companion/'app.js').write_text('synthetic')
  self.files=qa.source_inventory(self.candidate);self.other=qa.source_inventory(companion);self.head='a'*40;self.digest=qa.inventory_digest(self.head,self.files)
  _compose(self.candidate,self.files,companion,self.other,self.artifact/'unit-subject');_compose(companion,self.other,self.candidate,self.files,self.artifact/'browser-subject')
  self.unit=self.artifact/'unit-reports'/unit_qa.UNIT_REPORT_NAME;_write(self.unit,{'suites':[]})
  self.browser=self.artifact/'browser-reports/qa-broker-current.json';_write(self.browser,{'suites':[],'checks':[{'id':'synthetic','status':'PASS'}]})
  output='Ran 1 test in 0.001s\n\nOK\n';raw={'stdout':output,'stderr':'','exit_code':0,'timed_out':False,'output_overflow':False,'output_sha256':hashlib.sha256(output.encode()+b'\0').hexdigest()}
  references=[]
  for i,command in enumerate([*qa.QA_TEST_COMMANDS,LADS_COMMAND,*(x['command'] for x in qa.TRUSTED_SUITES if x['required'])]):
   p=self.artifact/'processes'/f'{i:03d}.json';_write(p,raw);references.append({'path':p.relative_to(self.artifact).as_posix(),'sha256':self.sha(p),'command':command})
  self.raw=raw;process,counts=unit_qa._process_from_receipt(raw,output+'\n');self.lads={'id':'lads','command':LADS_COMMAND,'process':process,**counts}
  self.report={'schema':SCHEMA,'qa_contract_version':3,'repository':REPOSITORIES['engineering'],'kind':'engineering','task_id':self.task,'source_head':self.head,'source_inventory_sha256':self.digest,'source_files':self.files,'companion_files':self.other,'companion_head':'b'*40,'artifact_root':str(self.artifact),'unit_inventory_sha256':qa.inventory_digest(self.head,{**self.other,**self.files}),'browser_inventory_sha256':qa.inventory_digest(self.head,{**self.files,**self.other}),'unit_report_sha256':self.sha(self.unit),'browser_report_sha256':self.sha(self.browser),'lads':self.lads,'processes':references,'suites':[self.lads],'checks':[{'id':'synthetic','status':'PASS'}],'ready':True,'simulation':False,'candidate_unchanged':True}
  a=mock.patch('lads_automation.split_qa.unit_qa.load_and_verify_unit_report',return_value={'evidence_sha256':'c'*64,'suites':[]});a.start();self.addCleanup(a.stop)
  b=mock.patch('lads_automation.split_qa.qa.load_and_verify_broker_report',return_value={'report_receipt_sha256':'d'*64});b.start();self.addCleanup(b.stop)
  self.save()
 def sha(self,p):return hashlib.sha256(p.read_bytes()).hexdigest()
 def save(self):
  self.report=qa.seal_broker_report({k:v for k,v in self.report.items() if k!='receipt_sha256'});self.path=self.run/'qa-unit-current.json';_write(self.path,self.report)
  _write(self.run/'split-context.json',{'schema':'lads-split-context-v1',**{k:self.report[k] for k in ('task_id','source_head','source_inventory_sha256','repository','artifact_root','receipt_sha256')},'commands':list(COMMANDS),'report_sha256':self.sha(self.path)})
 def verify(self):return verify_report(self.path,candidate_root=self.candidate,task_id=self.task,source_head=self.head)
 def test_control_binding_accepts_mock_inner_contracts_only(self):self.assertEqual(self.verify()['status'],'PASS')
 def test_candidate_edit_is_rejected(self):
  (self.candidate/'tool.py').write_text('changed')
  with self.assertRaises(ValueError):self.verify()
 def test_composite_edit_is_rejected(self):
  (self.artifact/'browser-subject/app.js').write_text('changed')
  with self.assertRaises(ValueError):self.verify()
 def test_raw_inner_receipt_edit_is_rejected(self):
  self.unit.write_text('{}')
  with self.assertRaises(ValueError):self.verify()
 def test_forged_ready_with_actual_lads_failure_is_rejected(self):
  ref=next(x for x in self.report['processes'] if x['command']==LADS_COMMAND);p=self.artifact/ref['path'];_write(p,{**self.raw,'exit_code':1});ref['sha256']=self.sha(p);self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_raw_output_cannot_change_without_matching_digest(self):
  ref=self.report['processes'][0];p=self.artifact/ref['path'];_write(p,{**self.raw,'stdout':'different'});ref['sha256']=self.sha(p);self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_human_suite_summary_cannot_differ(self):
  self.report['suites']=[];self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_human_check_summary_cannot_differ(self):
  self.report['checks']=[];self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_missing_actual_command_is_rejected(self):
  self.report['processes'].pop();self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_duplicate_actual_command_is_rejected(self):
  self.report['processes'].append(self.report['processes'][0]);self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_process_path_escape_is_rejected(self):
  self.report['processes'][0]['path']='../outside';self.save()
  with self.assertRaises((ValueError,FileNotFoundError)):self.verify()
 def test_simulated_receipt_cannot_pass(self):
  self.report['simulation']=True;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_other_repository_cannot_use_engineering_identity(self):
  self.report['repository']=REPOSITORIES['product'];self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_unknown_or_missing_command_contract_is_rejected(self):
  context=json.loads((self.run/'split-context.json').read_text());context['commands']=list(COMMANDS[:-1]);_write(self.run/'split-context.json',context)
  with self.assertRaises(ValueError):self.verify()

if __name__=='__main__':unittest.main()
