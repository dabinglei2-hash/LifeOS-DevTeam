"""Registered dual-repository RUN; MAIN-ACK/ACCEPT retain the Stage3 gates."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,sys,uuid
from dataclasses import replace
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lads_automation.project_registry import ProjectRegistry
from lads_automation import qa,unit_qa
from lads_automation.split_qa import SplitQABroker,COMMANDS,LADS_COMMAND
from lads_automation.team import BASE,Stage3Orchestrator,prepare_python_runtime
from lads_automation.rpc import CodexJsonRpcExecutor

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('command',choices=('RUN',));p.add_argument('--project',choices=('lifeos','devteam'),required=True);p.add_argument('--task-file',type=Path,required=True);p.add_argument('--base',type=Path,default=ROOT.parent);p.add_argument('--workspace',type=Path);p.add_argument('--timeout',type=int,default=900);p.add_argument('--credential-source',choices=('env','windows-user'),default='env')
 args=p.parse_args(argv)
 if not 30<=args.timeout<=3600:raise ValueError('Worker timeout outside bounds')
 registry=ProjectRegistry.load(ROOT/'config/project-registry.json',base=args.base,workspace=args.workspace or args.base/'LADS-Workspace')
 project=registry.project(args.project);registry.validate_repository(args.project)
 other=registry.project('devteam' if args.project=='lifeos' else 'lifeos');registry.validate_repository(other.key)
 manifest=BASE.load_manifest(args.task_file)
 if manifest.repo!=project.root or tuple(manifest.test_commands)!=qa.QA_TEST_COMMANDS:raise ValueError('Task source and base QA commands must bind the registered project')
 check_spec=importlib.util.spec_from_file_location('registered_source_safety',ROOT/'scripts/repository_check.py');safety=importlib.util.module_from_spec(check_spec);check_spec.loader.exec_module(safety)
 for item in (project,other):
  if safety.check(item.root,'product' if item.qa_profile=='product' else 'devteam','security')['status']!='PASS':raise ValueError('Source safety audit must pass before any external model call')
 digest=BASE.sha_bytes(BASE.canonical({'base_manifest':manifest.raw_sha256,'project_id':project.project_id,'repository':project.repository,'registry_sha256':hashlib.sha256((ROOT/'config/project-registry.json').read_bytes()).hexdigest(),'commands':list(COMMANDS)}))
 manifest=replace(manifest,test_commands=COMMANDS,raw_sha256=digest)
 spec=importlib.util.spec_from_file_location('lads_registered_stage3',ROOT/'scripts/lads_stage3.py');stage3=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage3)
 stage3._load_worker_environment(args.credential_source)
 work=registry.workspace/'qa'/('f-'+uuid.uuid4().hex[:8]);work.mkdir(parents=True,exist_ok=False);(work/'.gitignore').write_text('*\n',encoding='utf-8')
 _owner={'schema':'lads-split-workspace-v1','project_id':project.project_id,'repository':project.repository};(work/'owner.json').write_text(json.dumps(_owner),encoding='utf-8')
 runtime=prepare_python_runtime()
 commands=tuple(dict.fromkeys((*qa.QA_TEST_COMMANDS,LADS_COMMAND,*(x['command'] for x in qa.TRUSTED_SUITES))))
 executor=CodexJsonRpcExecutor(work_root=work,allowed_commands=commands,maximum_timeout=300,runtime_executables={'python':runtime})
 broker=SplitQABroker(executor=executor,work_root=work,kind=project.qa_profile,companion_root=other.root,timeout=300)
 flow=Stage3Orchestrator(worker=BASE.RealWorker(),broker=broker,unit_broker=broker,qa_commands=COMMANDS,timeout=args.timeout,log=lambda x:print(x,file=sys.stderr,flush=True))
 try:handoff=flow.run(manifest)
 finally:executor.terminate_all()
 summary={'task_id':handoff['task_id'],'task_status':handoff['task_status'],'candidate_sha256':handoff['candidate_sha256'],'repository':project.repository,'project_id':project.project_id,'run_root':str(flow.run_root),'private_qa_root':str(work),'source_head':handoff['source_head'],'reason':handoff.get('reason'),'publication':False}
 (work/'handoff-receipt.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=True));return 0 if handoff['task_status']=='AWAITING_MAIN_ACCEPTANCE' else 2

if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as exc:
  print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__}),file=sys.stderr);raise SystemExit(2)
