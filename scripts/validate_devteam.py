"""Run unchanged engineering suites with product fixtures in a private sibling."""
from __future__ import annotations
import argparse,hashlib,json,os,re,shutil,subprocess,sys,time,uuid
from pathlib import Path

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--product-root',type=Path,required=True);parser.add_argument('--workspace',type=Path,required=True);args=parser.parse_args()
 root=Path(__file__).resolve().parents[1];product=args.product_root.resolve(strict=True);workspace=args.workspace.resolve()
 for target in [root,product]:
  if workspace==target or workspace.is_relative_to(target):raise ValueError('Evidence workspace must be outside code roots')
 artifact=workspace/'validation'/('devteam-'+uuid.uuid4().hex)
 artifact.mkdir(parents=True,exist_ok=False);(artifact/'.gitignore').write_text('*\n',encoding='utf-8')
 composite=artifact/'product-qa-subject';composite.mkdir()
 raw=subprocess.run(['git','-C',str(product),'ls-files','--cached','--others','--exclude-standard','-z'],capture_output=True,check=True).stdout
 inventory={}
 for rel in sorted(set(x.decode('utf-8') for x in raw.split(b'\0') if x)):
  p=product/rel
  if not p.is_file():continue
  if set(Path(rel).parts)&{'.git','work','backups','node_modules','.openai'}:raise ValueError('Private path in product subject')
  out=composite/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,out)
  inventory[rel]=digest(out)
  if inventory[rel]!=digest(p):raise ValueError('Product snapshot changed')
 # The engineering acceptance harness remains in the engineering repo; copy it
 # only into this disposable cross-repository test environment.
 rel='tests/acceptance/lads-stage3-files.py';out=composite/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/rel,out)
 env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','LOCALAPPDATA','APPDATA','TEMP','TMP','USERPROFILE','HOME','PATHEXT','PROGRAMFILES','PROGRAMFILES(X86)'}}
 env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',LADS_PRODUCT_QA_ROOT=str(composite))
 results=[]
 for suite in ['automation','knowledge','lads']:
  argv=[sys.executable,'-B','-m','unittest','discover','-s','tests/'+suite,'-v'];start=time.time()
  log=artifact/(suite+'.log')
  with log.open('wb') as f:
   try:result=subprocess.run(argv,cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=600);code=result.returncode
   except subprocess.TimeoutExpired:code=-1
  text=log.read_text(encoding='utf-8',errors='replace');match=re.search(r'Ran (\d+) tests?',text)
  record={'suite':suite,'command':argv,'exit_code':code,'duration_seconds':round(time.time()-start,2),'test_count':int(match.group(1)) if match else None,'log_sha256':digest(log),'log':str(log),'status':'PASS' if code==0 and match else 'FAIL'}
  results.append(record);print(json.dumps(record),flush=True)
 unchanged=all((product/p).is_file() and digest(product/p)==h for p,h in inventory.items())
 result={'status':'PASS' if unchanged and all(x['status']=='PASS' for x in results) else 'FAIL','reviewer_independent':False,'acceptance_mode':'main-local-tests','results':results,'product_subject_unchanged':unchanged,'artifact_root':str(artifact),'fixture_scope':'Composite test copy outside both code roots; no product files versioned in DevTeam.'}
 (artifact/'receipt.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True);return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
