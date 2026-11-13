"""Actual native event probe; saves bounded protocol diagnostics even on failure."""
from pathlib import Path
import sys,os,json,subprocess,queue,threading,time,uuid,importlib.util,hashlib
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root))
from lads_automation.state import FileJournal,acceptance_root
from lads_automation.engine import AutomationEngine
from lads_automation.evidence import TaskSpec
from lads_automation.team import BASE
from scripts import lads_session

def execute_live(*, namespace: str, timeout: int = 360):
    base=acceptance_root(namespace);base.mkdir(parents=True,exist_ok=False)
    helper=BASE.load_helper();baseline=helper.main_config_snapshot()
    env={k:v for k,v in os.environ.items() if (not k.startswith('CODEX_') or k=='CODEX_HOME') and not any(s in k.upper() for s in ('API_KEY','TOKEN','SECRET','PASSWORD')) or k=='OPENAI_API_KEY'}
    env['LADS_HOOK_ACCEPTANCE_NAMESPACE']=namespace;env['PYTHONDONTWRITEBYTECODE']='1'
    argv=lads_session.command();argv=argv[:argv.index('--sandbox')]+['--enable','windows_sandbox_service','--disable','apps','--disable','plugins','--disable','multi_agent','--disable','multi_agent_v2','-c','shell_environment_policy.exclude=["*KEY*","*TOKEN*","*SECRET*","*PASSWORD*"]','-c','agents.enabled=false','-c','mcp_servers.node_repl.enabled=false','app-server']
    p=subprocess.Popen(argv,cwd=root,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8');q=queue.Queue();events=[];stderr=[]
    def read():
     for line in p.stdout:
      try:q.put(json.loads(line))
      except ValueError:pass
    def errread():
     for line in p.stderr:
      if len(stderr)<35:stderr.append(line[:350])
    threading.Thread(target=read,daemon=True).start();threading.Thread(target=errread,daemon=True).start()
    def redact(text):
     import re
     return re.sub(r'(?i)(?:sk-[\w-]{12,}|Bearer\s+[\w.-]+)','[REDACTED]',str(text))[:600]
    def send(v):p.stdin.write(json.dumps(v)+'\n');p.stdin.flush()
    def note(x):
     method=x.get('method');params=x.get('params',{});run=params.get('run',{});item=params.get('item',{})
     row={'method':method,'params_keys':list(params)[:15],'id_present':'id' in x}
     if run:row['hook']={k:run.get(k) for k in ('eventName','status','handlerType','startedAt','completedAt','entries')}
     if item:row['item']={k:item.get(k) for k in ('type','status','exitCode')}
     if method in ('error','codex/event/error') or 'error' in params:
      row['error']=redact(params.get('error',params))
     if params.get('msg',{}).get('type') in ('error','warning'):row['message']=redact(params['msg'])
     events.append(row)
     if 'id' in x and method:
      send({'id':x['id'],'error':{'code':-32601,'message':'Acceptance client declines unexpected server request'}})
      print('DECLINED SERVER REQUEST '+str(method),flush=True)
     if method in ('hook/started','hook/completed','error','turn/completed'):print(json.dumps(row,ensure_ascii=True)[:1100],flush=True)
    def call(i,method,params):
     send({'id':i,'method':method,'params':params});deadline=time.monotonic()+35
     while time.monotonic()<deadline:
      try:x=q.get(timeout=2)
      except queue.Empty:continue
      if x.get('id')==i and not x.get('method'):
       if 'error' in x:raise RuntimeError(method+' '+redact(x['error']))
       return x['result']
      note(x)
     raise TimeoutError(method)
    out={'status':'FAIL','private_root':str(base),'native_trigger':True,'synthetic_only':True,'main_model_override':False,'started_at':BASE.now(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()};journal=None;task=None;thread=None;turn=None
    try:
     call(1,'initialize',{'clientInfo':{'name':'lads-native-hooks-live-v2','version':'2'},'capabilities':{'experimentalApi':True}});send({'method':'initialized'})
     started=call(2,'thread/start',{'cwd':str(root),'sandbox':'read-only','approvalPolicy':'never','ephemeral':True,
     'baseInstructions':'You are the main controller performing a bounded protocol acceptance test. Use only the requested read-only shell command. Do not inspect the repository, delegate or write files. A synthetic task deliberately remains incomplete.',
     'developerInstructions':'Run the requested command exactly once. Its exit 7 is intentional. Afterward say PENDING. If Stop requests continuation, say PENDING without tools. Do not investigate or fix anything in this probe.'})
     thread=started['thread']['id'];out['thread_id']=thread;out['actual_provider']=started.get('modelProvider');out['actual_model']=started.get('model');assert out['actual_provider']=='openai'
     journal=FileJournal.create(acceptance_namespace=namespace)
     task=str(uuid.uuid4());engine=AutomationEngine(TaskSpec(task,'a'*40,'b'*64,('synthetic-probe',),('native-readonly-probe',)),journal);engine.register('Synthetic native event test; deliberately incomplete',session_id=thread)
     started_turn=call(3,'turn/start',{'threadId':thread,'input':[{'type':'text','text':'Use exec_command once to execute this PowerShell built-in command exactly: Write-Output \'LADS_NATIVE_PROBE\'; exit 7. Exit 7 is intended. Then say PENDING and stop; no other tools or investigations.'}]})
     turn=started_turn['turn']['id'];out['turn_id']=turn;deadline=time.monotonic()+timeout
     while time.monotonic()<deadline:
      try:x=q.get(timeout=3)
      except queue.Empty:continue
      note(x)
      if x.get('method')=='turn/completed':out['turn_status']=x.get('params',{}).get('turn',{}).get('status');break
     else:raise TimeoutError('native model turn exceeded its explicit time bound')
    except Exception as exc:
     out['error']=type(exc).__name__+': '+redact(exc)
     if thread and turn:
      try:call(9,'turn/interrupt',{'threadId':thread,'turnId':turn});out['interrupt_sent']=True
      except Exception:out['interrupt_sent']=False
    finally:
     if journal is not None:
      rows=journal.list_events(task);out['journal_events']=rows;out['gate_still_closed']=not engine.status()['ready'];out['stop_requests']=sum(x['event_type']=='stop_continuation_requested' for x in rows);out['post_tool_recorded']=any(x['event_type']=='hook_post_tool' for x in rows)
     complete_hooks=[x.get('hook',{}) for x in events if x.get('method')=='hook/completed'];out['completed_hook_events']=[{k:x.get(k) for k in ('eventName','status')} for x in complete_hooks]
     out['command_exits']=[x['item']['exitCode'] for x in events if x.get('method')=='item/completed' and x.get('item',{}).get('type')=='commandExecution'];out['events']=events;out['stderr_summary']=[redact(x) for x in stderr];out['ended_at']=BASE.now()
     if out.get('command_exits')==[7] and out.get('post_tool_recorded') and out.get('stop_requests')==1 and out.get('gate_still_closed') and out.get('turn_status')=='completed' and all(x.get('status') in ('completed','blocked') for x in complete_hooks) and {x.get('eventName') for x in complete_hooks}>={'preToolUse','postToolUse','stop'}:out['status']='PASS'
     try:out['main_config_guard']=helper.main_config_transition(baseline,helper.main_config_snapshot(),(root,))
     except Exception as exc:out['main_config_guard']={'status':'BLOCKED','error':redact(exc)};out['status']='FAIL'
     p.stdin.close()
     try:p.wait(timeout=7)
     except subprocess.TimeoutExpired:BASE.kill_tree(p);p.wait(timeout=10)
     (base/'native-report.json').write_text(json.dumps(out,ensure_ascii=True,indent=2),encoding='utf-8');print(json.dumps({'status':out['status'],'report':str(base/'native-report.json'),'error':out.get('error'),'hooks':out['completed_hook_events'],'stop_requests':out.get('stop_requests')},ensure_ascii=True),flush=True)

    return out

def main():
    import argparse
    parser=argparse.ArgumentParser(description="Actual OpenAI native Hook probe with synthetic local data; explicit opt-in required")
    parser.add_argument('--namespace',required=True)
    parser.add_argument('--timeout',type=int,default=360)
    parser.add_argument('--execute-live',action='store_true')
    args=parser.parse_args()
    if not args.execute_live:parser.error('Use --execute-live only with explicit native-model test authorization')
    if not 90 <= args.timeout <= 600:parser.error('Timeout must be between 90 and 600 seconds')
    if os.name!='nt':parser.error('This acceptance probe requires Windows PowerShell')
    result=execute_live(namespace=args.namespace,timeout=args.timeout)
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
