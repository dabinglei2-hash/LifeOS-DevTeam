#!/usr/bin/env python3
"""Explicitly authorized main-controller UI drill, with real sandboxed QA.

No model calls. No default-store writes. No independent-agent claims.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,shutil,sys,uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from lads_automation.qa import QABroker,TRUSTED_SUITES,STAGE3_DEMO_REQUIRED_CHECKS
from lads_automation.rpc import CodexJsonRpcExecutor
from lads_automation.team import BASE
from lads_automation.state import FileJournal,acceptance_root
from lads_automation.engine import AutomationEngine,DocumentPublisher,KnowledgePublisher,DeliveryError
from lads_automation.evidence import TaskSpec
from lads_automation.main_acceptance import accept_main_exception
from lads_knowledge.database import FileStore
from lads_knowledge.indexer import repository_ref

def run(base,grant_path):
    module_spec=importlib.util.spec_from_file_location('main_ui_fixture',ROOT/'tests/acceptance/lads-stage3-live.py')
    fixture=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(fixture)
    fixture_base,manifest=fixture._make_fixture(base)
    repo=Path(manifest['repo']);head=fixture._git(repo,'rev-parse','HEAD')
    executor=CodexJsonRpcExecutor(work_root=base,allowed_commands=tuple(s['command'] for s in TRUSTED_SUITES),maximum_timeout=180,
        runtime_executables={'python':sys.executable,'node':shutil.which('node'),'codex':shutil.which('codex')})
    broker=QABroker(executor=executor,work_root=base,timeout=180,required_checks=STAGE3_DEMO_REQUIRED_CHECKS)
    results=[]
    instruction_sha=hashlib.sha256(grant_path.read_bytes()).hexdigest()
    namespace='s3-main-ui-'+uuid.uuid4().hex[:12]
    journal=FileJournal.create(acceptance_namespace=namespace)
    try:
        for build in (1,2):
            if build==2:fixture._set_notes_aria(repo/'index.html',fixture._LABEL)
            candidate=base/('c'+str(build));snapshot=BASE.capture_source(repo,candidate)
            task=str(uuid.uuid4());report=broker.run(task_id=task,source_head=head,candidate_root=candidate,build_number=build,report_path=base/('qa-'+str(build)+'.json'))
            commands=tuple(x['command'] for x in report['suites'])
            spec=TaskSpec(task,head,report['source_inventory_sha256'],('index.html',),commands)
            engine=AutomationEngine(spec,journal);engine.register('Isolated notes aria label UI drill; main-controller exception')
            binding={'task_id':task,'source_head':head,'candidate_sha256':spec.candidate_sha256}
            grant={**binding,'grantor':'user','scope':'stage3-engineering-only','allow_main_implementation':True,'allow_main_testing':True,'allow_main_review':True,'instruction_sha256':instruction_sha}
            review={**binding,'actor':'GPT main controller','independent':False,'status':'APPROVED','review_type':'final','simulation':False}
            if build==1:
                if report['ready']:raise AssertionError('Negative UI fixture unexpectedly passed')
                try:accept_main_exception(engine,candidate_root=candidate,broker_report=report,authorization=grant,review=review)
                except ValueError:pass
                else:raise AssertionError('Negative QA was accepted')
                if engine.status()['status']!='QA_FAILED' or engine.status()['ready']:raise AssertionError('Failed QA did not close delivery')
                results.append({'build':1,'qa':'FAIL','review':'CHANGES_REQUESTED','actor':'GPT main controller','independent':False,'delivery':engine.status()})
                print('Negative QA blocked delivery; main review requests correction.',flush=True)
                continue
            if not report['ready']:raise AssertionError('Corrected UI did not pass actual QA')
            changed=fixture._git(repo,'diff','--name-only').splitlines()
            if changed!=['index.html']:raise AssertionError('UI correction escaped index.html')
            review_path=base/'main-final-review.json';review_path.write_text(json.dumps(review,indent=2),encoding='utf-8')
            accept_main_exception(engine,candidate_root=candidate,broker_report=report,authorization=grant,review=review)
            store=FileStore.open(base/'knowledge','knowledge.sqlite3')
            try:
                project=store.register_project('Main-authorized Stage3 UI drill',root_ref=repository_ref(repo))
                knowledge=KnowledgePublisher(store,project['id'],repo);knowledge.ensure_task(task,'Synthetic UI correction with real QA')
                engine.repo_root=repo;engine.repository_ref=repository_ref(repo)
                engine.publisher=DocumentPublisher(repo,backup_root=base/'doc-backups',registered_repository_ref=engine.repository_ref)
                engine.knowledge=knowledge
                for event in ('task_state_changed','task_delivered'):
                    engine.record_event(event,{'summary':'Synthetic notes card accessibility label corrected','reason':'Main-authorized Stage3 drill; actual QA passed; independent review not claimed'})
                docs=engine.required_documents();old={p:hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in docs}
                original_replace=engine.publisher._replace
                def fail_doc(*a,**k):raise OSError('Injected synthetic document-write failure')
                engine.publisher._replace=fail_doc
                try:engine.sync_documents(old)
                except DeliveryError:pass
                else:raise AssertionError('Injected document failure did not block')
                if engine.status()['status']!='DOCS_FAILED' or engine.status()['ready']:raise AssertionError('Document failure reported completion')
                engine.publisher._replace=original_replace;engine.retry(reason='Recover injected document fault')
                original_index=knowledge.indexer.sync_documents
                def fail_index(*a,**k):raise OSError('Injected synthetic index failure')
                knowledge.indexer.sync_documents=fail_index
                try:engine.sync_index(reason='Real local index after UI QA')
                except DeliveryError:pass
                else:raise AssertionError('Injected index failure did not block')
                if engine.status()['status']!='INDEX_FAILED' or engine.status()['ready']:raise AssertionError('Index failure reported completion')
                knowledge.indexer.sync_documents=original_index
                delivered=engine.retry(reason='Recover injected index fault')
                if not delivered['ready']:raise AssertionError('Recovery did not finish document/index gate')
                state=journal.get_task(task)
                results.append({'build':2,'qa':'PASS','review':review,'documents':state['documents_receipt'],'index':state['index_receipt'],'delivery':delivered,'evidence':state['evidence']})
            finally:store.close()
        return {'status':'PASS','acceptance_mode':'main-authorized-exception','independent_agents':False,'simulation':False,'synthetic_data':True,'model_calls':False,'results':results,'private_root':str(base),'fixture_root':str(repo),'journal_namespace':namespace,'product_source_modified':False}
    finally:executor.terminate_all()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--authorization-file',required=True,type=Path);p.add_argument('--namespace',required=True);p.add_argument('--execute-main-exception',action='store_true');a=p.parse_args()
    if not a.execute_main_exception:p.error('Explicit main-controller exception opt-in required')
    base=acceptance_root(a.namespace);base.mkdir(parents=True,exist_ok=False)
    try:result=run(base,a.authorization_file)
    except Exception as exc:result={'status':'FAIL','error_type':type(exc).__name__,'error':str(exc)[:1500],'private_root':str(base),'retained':True}
    (base/'main-demo-report.json').write_text(json.dumps(result,ensure_ascii=True,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=True),flush=True)
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');raise SystemExit(main())
