"""v3 split-repository QA envelope over the unchanged v2 unit/UI verifiers.

Both repositories still execute all product checks. Test subjects are composed
only in an owned private workspace; neither repository receives the other's
source files. The outer proof binds the selected candidate and the frozen
companion inventory independently.
"""
from __future__ import annotations
import hashlib,json,shutil,subprocess,sys,uuid
from pathlib import Path
from typing import Any
from . import qa,unit_qa

VERSION=3
SCHEMA='lads-split-qa-v1'
REPOSITORIES={'product':'dabinglei2-hash/LifeOS','engineering':'dabinglei2-hash/LifeOS-DevTeam'}
COMMANDS=unit_qa.SPLIT_QA_COMMANDS
LADS_COMMAND='python -B -m unittest discover -s tests/lads -v'
PRIVATE_PARTS={'.git','work','backups','.env','personal-data','data-backups','node_modules','__pycache__','.openai'}
REPO_KEYS={'product','engineering'}
IDENTITY_NAMESPACES=('.github/','.codex/','.agents/','config/')

class SplitQAError(ValueError):pass
class SplitQAInfrastructureError(SplitQAError):pass

def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _write(path,value):
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 unit_qa._atomic_json(Path(path),value)
def _read(path):return unit_qa._strict_json(Path(path),maximum=4*1024*1024)[0]
def _safe(path):
 path=Path(path);unit_qa._reject_reparse_chain(path);return path.resolve(strict=True)
def _contained(root,relative):
 p=Path(relative)
 if p.is_absolute() or any(x in {'..','.'} for x in p.parts) or '\\' in relative or ':' in relative:raise SplitQAError('Unsafe artifact path')
 result=_safe(root/p)
 if not result.is_relative_to(root):raise SplitQAError('Artifact escaped owned root')
 return result
def _files(root):
 result=subprocess.run(['git','-C',str(root),'ls-files','--cached','--others','--exclude-standard','-z'],capture_output=True,check=True)
 rows={}
 for raw in result.stdout.split(b'\0'):
  if not raw:continue
  rel=raw.decode('utf-8');p=root/rel
  if p.is_symlink():raise SplitQAError('Companion symlink rejected')
  if not p.exists():continue
  if set(Path(rel).parts)&PRIVATE_PARTS or p.suffix.lower() in {'.db','.sqlite','.sqlite3','.key','.pem','.log'}:raise SplitQAError('Private companion source rejected')
  _safe(p)
  if not p.is_file():raise SplitQAError('Non-file companion source rejected')
  rows[rel]=_sha(p)
 if not rows:raise SplitQAError('Empty companion source')
 return dict(sorted(rows.items()))
def _copy(root,out,files):
 out.mkdir(parents=True,exist_ok=False)
 for rel,digest in files.items():
  p=_contained(root,rel);target=out/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
  if _sha(target)!=digest or _sha(p)!=digest:raise SplitQAError('Source changed while composing QA')
def composition_inventory(first,second):
 blocked=tuple(prefix for prefix in IDENTITY_NAMESPACES if any(p.startswith(prefix) for p in first))
 support={p:h for p,h in second.items() if p not in first and not p.startswith(blocked)}
 return {**support,**first}
def _compose(first_root,first,second_root,second,out):
 _copy(first_root,out,first)
 expected=composition_inventory(first,second)
 for rel,digest in expected.items():
  if rel in first:continue
  p=_contained(second_root,rel);target=out/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
  if _sha(target)!=digest:raise SplitQAError('Companion copy drift')
 return expected

class RecordingExecutor:
 def __init__(self,inner,artifact,product_root):self.inner=inner;self.artifact=artifact;self.product_root=product_root;self.calls=[]
 def run(self,**kwargs):
  if kwargs['command'] in {*qa.QA_TEST_COMMANDS,LADS_COMMAND}:
   kwargs['environment']={**kwargs.get('environment',{}),'LADS_PRODUCT_QA_ROOT':str(self.product_root)}
  receipt=self.inner.run(**kwargs)
  index=len(self.calls);path=self.artifact/'processes'/f'{index:03d}.json';_write(path,receipt)
  self.calls.append({'path':path.relative_to(self.artifact).as_posix(),'sha256':_sha(path),'command':kwargs['command']})
  return receipt

class SplitQABroker:
 def __init__(self,*,executor,work_root,kind,companion_root,timeout=300):
  if kind not in REPO_KEYS:raise SplitQAError('Unknown registered QA kind')
  self.executor=executor;self.work_root=_safe(work_root);self.kind=kind;self.companion=_safe(companion_root);self.timeout=timeout;self.cache={}
  expected=REPOSITORIES['engineering' if kind=='product' else 'product']
  r=subprocess.run(['git','-C',str(self.companion),'remote','get-url','origin'],capture_output=True,text=True,check=True)
  if r.stdout.strip()!='https://github.com/'+expected+'.git':raise SplitQAError('Companion origin does not bind the other registered project')

 def prepare_run(self,run_root,state):
  root=_safe(run_root)
  if root.name!=state['task_id']:raise SplitQAError('Managed run identity mismatch')
  controller=Path(__file__).resolve().parents[1]
  code='import sys\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(controller))+')\nfrom lads_automation.split_qa import verifier_main\nif __name__=="__main__": raise SystemExit(verifier_main())\n'
  target=root/'split-verifier.py'
  if target.exists() and target.read_text(encoding='utf-8')!=code:raise SplitQAError('Controller verifier drift')
  if not target.exists():target.write_text(code,encoding='utf-8')

 def run(self,*,task_id,source_head,candidate_root,report_path,build_number=None):
  candidate=_safe(candidate_root);run_root=_safe(Path(report_path).parent)
  if run_root.name!=task_id or Path(report_path).name not in {'qa-unit-current.json','qa-broker-current.json'}:raise SplitQAError('Unmanaged outer report')
  files=qa.source_inventory(candidate);digest=qa.inventory_digest(source_head,files)
  if digest in self.cache:
   report=self.cache[digest]
   _write(Path(report_path),report);return report
  artifact=self.work_root/('split-'+task_id+'-'+uuid.uuid4().hex[:8]);artifact.mkdir()
  companion_head=subprocess.run(['git','-C',str(self.companion),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
  companion_files=_files(self.companion)
  primary=artifact/'primary';companion=artifact/'companion'
  _copy(candidate,primary,files);_copy(self.companion,companion,companion_files)
  engine,product=(companion,primary) if self.kind=='product' else (primary,companion)
  engine_files,product_files=(companion_files,files) if self.kind=='product' else (files,companion_files)
  unit_root=artifact/'unit-subject';browser_root=artifact/'browser-subject'
  unit_files=_compose(engine,engine_files,product,product_files,unit_root)
  browser_files=_compose(product,product_files,engine,engine_files,browser_root)
  # Existing Windows helper boundary tests require real Git metadata. This is
  # a synthetic test image, not a commit or another published repository.
  subprocess.run(['git','init','-q','-b','main',str(unit_root)],capture_output=True,check=True)
  recorder=RecordingExecutor(self.executor,artifact,browser_root)
  unit_dir=artifact/'unit-reports';unit_dir.mkdir()
  units=unit_qa.UnitQABroker(executor=recorder,work_root=self.work_root,timeout=self.timeout)
  unit_report=units.run(task_id=task_id,source_head=source_head,candidate_root=unit_root,report_path=unit_dir/unit_qa.UNIT_REPORT_NAME)
  # Run the third engineering suite through the same credential-free executor.
  temp=self.work_root/('lt-'+uuid.uuid4().hex[:8]);temp.mkdir();local=temp/'local';local.mkdir()
  receipt=recorder.run(command=LADS_COMMAND,cwd=str(unit_root),timeout=self.timeout,network_access=False,writable_roots=[str(unit_root),str(temp),str(local)],environment={'TEMP':str(temp),'TMP':str(temp),'LOCALAPPDATA':str(local)})
  output=str(receipt.get('stdout',''))+'\n'+str(receipt.get('stderr',''))
  process,counts=unit_qa._process_from_receipt(receipt,output)
  lads={'id':'lads','command':LADS_COMMAND,'process':process,**counts}
  ui=qa.QABroker(executor=recorder,work_root=self.work_root,timeout=self.timeout)
  ui_dir=artifact/'browser-reports';ui_dir.mkdir()
  ui_report=ui.run(task_id=task_id,source_head=source_head,candidate_root=browser_root,build_number=build_number or 1,report_path=ui_dir/'qa-broker-current.json')
  unchanged=qa.source_inventory(candidate)==files and _files(self.companion)==companion_files and qa.source_inventory(unit_root)==unit_files and qa.source_inventory(browser_root)==browser_files and subprocess.run(['git','-C',str(self.companion),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()==companion_head
  ready=bool(unit_report.get('ready') and ui_report.get('ready') and unchanged and process['status']=='completed' and process['exit_code']==0 and counts['test_count']>0 and counts['failure_count']==counts['error_count']==counts['skip_count']==0)
  report=qa.seal_broker_report({'schema':SCHEMA,'qa_contract_version':VERSION,'repository':REPOSITORIES[self.kind],'kind':self.kind,'task_id':task_id,'source_head':source_head,'source_inventory_sha256':digest,'source_files':files,'companion_files':companion_files,'companion_head':subprocess.run(['git','-C',str(self.companion),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip(),'artifact_root':str(artifact),'unit_inventory_sha256':qa.inventory_digest(source_head,unit_files),'browser_inventory_sha256':qa.inventory_digest(source_head,browser_files),'unit_report_sha256':_sha(unit_dir/unit_qa.UNIT_REPORT_NAME),'browser_report_sha256':_sha(ui_dir/'qa-broker-current.json'),'lads':lads,'processes':recorder.calls,'suites':[*unit_report['suites'],lads],'checks':ui_report['checks'],'ready':ready,'simulation':False,'candidate_unchanged':unchanged})
  _write(run_root/'qa-unit-current.json',report);_write(run_root/'qa-broker-current.json',report)
  context={'schema':'lads-split-context-v1','task_id':task_id,'source_head':source_head,'source_inventory_sha256':digest,'repository':REPOSITORIES[self.kind],'artifact_root':str(artifact),'receipt_sha256':report['receipt_sha256'],'report_sha256':_sha(run_root/'qa-unit-current.json'),'commands':list(COMMANDS)}
  _write(run_root/'split-context.json',context)
  self.cache[digest]=report
  infrastructure = any(row.get('process',{}).get('status') not in {'completed','failed'} for row in [*unit_report['suites'],lads,*ui_report['suites']])
  if infrastructure:
   raise SplitQAInfrastructureError('A fixed QA command has no valid terminal process receipt; retained raw evidence requires controller diagnosis')
  return report

def verify_report(path,*,candidate_root,task_id=None,source_head=None,expected_inventory_sha256=None):
 path=_safe(path);root=_safe(path.parent);report=_read(path);context=_read(root/'split-context.json')
 if report.get('schema')!=SCHEMA or report.get('qa_contract_version')!=VERSION or context.get('schema')!='lads-split-context-v1':raise SplitQAError('Unknown split contract')
 if context.get('commands')!=list(COMMANDS):raise SplitQAError('Split verifier commands changed')
 if path.name not in {'qa-unit-current.json','qa-broker-current.json'} or _sha(path)!=context.get('report_sha256'):raise SplitQAError('Outer raw report digest mismatch')
 if report.get('receipt_sha256')!=qa.report_receipt(report) or report['receipt_sha256']!=context.get('receipt_sha256'):raise SplitQAError('Outer seal mismatch')
 if report.get('ready') is not True or report.get('simulation') is not False or report.get('candidate_unchanged') is not True:raise SplitQAError('Split QA is not a real ready receipt')
 if report.get('kind') not in REPOSITORIES or report.get('repository')!=REPOSITORIES[report['kind']]:raise SplitQAError('Split project identity mismatch')
 for key in ('task_id','source_head','source_inventory_sha256','repository','artifact_root'):
  if context.get(key)!=report.get(key):raise SplitQAError('Split context identity mismatch')
 if root.name!=report['task_id']:raise SplitQAError('Unmanaged report root')
 if task_id is not None and task_id!=report['task_id']:raise SplitQAError('Wrong task')
 if source_head is not None and source_head!=report['source_head']:raise SplitQAError('Wrong source HEAD')
 candidate=_safe(candidate_root);actual=qa.source_inventory(candidate)
 digest=qa.inventory_digest(report['source_head'],actual)
 if actual!=report['source_files'] or digest!=report['source_inventory_sha256'] or expected_inventory_sha256 is not None and digest!=expected_inventory_sha256:raise SplitQAError('Candidate bytes changed')
 artifact=_safe(report['artifact_root'])
 if not artifact.name.startswith('split-'+report['task_id']+'-') or artifact.is_relative_to(candidate) or candidate.is_relative_to(artifact):raise SplitQAError('Artifact boundary violation')
 primary=report['source_files'];companion=report['companion_files']
 engine,product=(companion,primary) if report['kind']=='product' else (primary,companion)
 expected_unit=composition_inventory(engine,product);expected_browser=composition_inventory(product,engine)
 unit_root=_contained(artifact,'unit-subject');browser_root=_contained(artifact,'browser-subject')
 if qa.source_inventory(unit_root)!=expected_unit or qa.source_inventory(browser_root)!=expected_browser:raise SplitQAError('Composite source does not exactly match its two inputs')
 unit_path=_contained(artifact,'unit-reports/'+unit_qa.UNIT_REPORT_NAME);ui_path=_contained(artifact,'browser-reports/qa-broker-current.json')
 if _sha(unit_path)!=report['unit_report_sha256'] or _sha(ui_path)!=report['browser_report_sha256']:raise SplitQAError('Inner raw report mismatch')
 units=unit_qa.load_and_verify_unit_report(unit_path,candidate_root=unit_root,task_id=report['task_id'],source_head=report['source_head'],expected_inventory_sha256=report['unit_inventory_sha256'])
 ui=qa.load_and_verify_broker_report(ui_path,candidate_root=browser_root,task_id=report['task_id'],source_head=report['source_head'],expected_inventory_sha256=report['browser_inventory_sha256'])
 found=[];actual_processes={}
 for reference in report.get('processes',[]):
  p=_contained(artifact,reference['path'])
  if _sha(p)!=reference['sha256']:raise SplitQAError('Actual process receipt changed')
  receipt=_read(p);command=reference['command']
  if command in actual_processes:raise SplitQAError('Duplicate actual command receipt')
  if receipt.get('output_sha256')!=hashlib.sha256(str(receipt.get('stdout','')).encode()+b'\0'+str(receipt.get('stderr','')).encode()).hexdigest():raise SplitQAError('Actual process output digest mismatch')
  actual_processes[command]=receipt
  if command==LADS_COMMAND:found.append(receipt)
 expected_commands={*qa.QA_TEST_COMMANDS,LADS_COMMAND,*(x['command'] for x in qa.TRUSTED_SUITES if x['required'])}
 if set(actual_processes)!=expected_commands:raise SplitQAError('Missing or extra actual broker command')
 inner_units=_read(unit_path)
 for row in inner_units['suites']:
  raw=actual_processes[row['command']]
  process,counts=unit_qa._process_from_receipt(raw,str(raw.get('stdout',''))+'\n'+str(raw.get('stderr','')))
  if row['process']!=process or any(row[k]!=counts[k] for k in ('test_count','failure_count','error_count','skip_count')):raise SplitQAError('Unit summary differs from actual command output')
 for row in _read(ui_path)['suites']:
  raw=actual_processes[row['command']]
  if raw.get('exit_code')!=0 or raw.get('timed_out') is True or raw.get('output_overflow') is True or raw.get('output_sha256')!=row['process']['output_sha256']:raise SplitQAError('Browser suite differs from actual command output')
 if len(found)!=1:raise SplitQAError('Exactly one actual LADS suite receipt is required')
 receipt=found[0];process,counts=unit_qa._process_from_receipt(receipt,str(receipt.get('stdout',''))+'\n'+str(receipt.get('stderr','')))
 lads={'id':'lads','command':LADS_COMMAND,'process':process,**counts}
 if lads!=report.get('lads') or process['status']!='completed' or process['exit_code']!=0 or counts['test_count']<=0 or counts['failure_count']+counts['error_count']+counts['skip_count']!=0:raise SplitQAError('LADS suite did not actually pass')
 if report.get('suites')!=[*_read(unit_path)['suites'],lads] or report.get('checks')!=_read(ui_path)['checks']:raise SplitQAError('Human summaries differ from verified inner receipts')
 body={'task_id':report['task_id'],'source_head':report['source_head'],'source_inventory_sha256':digest,'qa_contract_version':VERSION,'repository':report['repository'],'report_sha256':_sha(path),'report_receipt_sha256':report['receipt_sha256'],'context_sha256':_sha(root/'split-context.json'),'unit_evidence_sha256':units['evidence_sha256'],'browser_evidence_sha256':ui['report_receipt_sha256'],'suites':[*units['suites'],lads]}
 return {'status':'PASS',**body,'evidence_sha256':hashlib.sha256(qa.canonical(body)).hexdigest()}

def verifier_main():
 try:
  if len(sys.argv)!=3 or sys.argv[1] not in {'automation','knowledge','lads','broker'}:raise SplitQAError('Fixed split verifier arguments required')
  result=verify_report(Path.cwd()/sys.argv[2],candidate_root=Path.cwd())
  print(json.dumps({'status':'PASS','suite':sys.argv[1],'candidate_sha256':result['source_inventory_sha256'],'evidence_sha256':result['evidence_sha256']},sort_keys=True));return 0
 except Exception as exc:
  print(json.dumps({'status':'FAIL','error_type':type(exc).__name__}),file=sys.stderr);return 1
