"""Repository ownership, secret-pattern and maintained-document checks.

No network, credentials, model calls, deployment or product storage access.
Legacy documents are retained as historical evidence; current docs are checked.
"""
from __future__ import annotations
import argparse,hashlib,json,re,subprocess,sys
from pathlib import Path

PATTERNS=(
 re.compile(rb'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}'),
 re.compile(rb'\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_-]{20,}\b'),
 re.compile(rb'\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{20,}\b'),
 re.compile(rb'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----'),
 re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
)
EXEMPT={('tests/knowledge/test_indexer.py','defa8d583c51f7c8e48e88a918a7e852400aba72305165bc563e4f204d5d4fc9')}
EXEMPT.update({
 ('tests/automation/test_rpc.py', 'ff2e400560d1aee09003c27b22f0adc8153f4c1bdca1346ea4b4257359754273'),
 ('engineering/lads-stage3-install/tests/lads/test_orchestrator.py', '3b971770d38f7af2e3a63f23ab7ee4095676d12b35516b44b0afc943011eca5c'),
 ('tests/lads/test_orchestrator.py', '241d4e445bdd8eee9f54563c361a8b6f42dd41f050f84d0c6243948c24017742'),
})
PRIVATE_PARTS={'.git','node_modules','.venv','venv','backups','work','personal-data','data-backups','runtime-homes','sessions','logs','exports','__pycache__','.openai'}
PRIVATE_SUFFIXES={'.sqlite','.sqlite3','.db','.key','.pem','.pfx','.p12','.log','.pyc'}
ENGINE_PREFIXES=('.agents/','.codex/','lads_automation/','lads_knowledge/','engineering/','schemas/','tests/automation/','tests/lads/','tests/knowledge/')
PRODUCT_NAMES={'app.js','index.html','health.js','learning.js','schedule.js','notes.js','sw.js','manifest.webmanifest'}
CURRENT_DOCS=('README.md','AGENTS.md','docs/PRD.md','docs/ARCHITECTURE.md','docs/STATUS.md','docs/DATA_MODEL.md','docs/DEVELOPMENT_PROGRESS.md','docs/DUAL_REPOSITORY_MIGRATION_REPORT.md')

def selected_files(root):
 result=subprocess.run(['git','-C',str(root),'ls-files','--cached','--others','--exclude-standard','-z'],capture_output=True,check=True)
 return sorted(set(x.decode('utf-8') for x in result.stdout.split(b'\0') if x and (root/x.decode('utf-8')).is_file()))

def check(root,kind,mode):
 issues=[];paths=selected_files(root);binary=0
 if not paths:issues.append(('.', 'empty-public-source'))
 for rel in paths:
  p=Path(rel);parts=set(p.parts)
  if p.suffix.lower() in PRIVATE_SUFFIXES or parts & PRIVATE_PARTS or any(x.startswith('.env') and x!='.env.example' for x in p.parts):issues.append((rel,'private-path'))
  if kind=='product' and (rel.startswith(ENGINE_PREFIXES) or rel=='requirements-lads.txt' or rel.startswith('scripts/lads')):issues.append((rel,'engineering-in-product'))
  if kind=='devteam' and (rel in PRODUCT_NAMES or rel.startswith(('server/','vendor/','assets/'))):issues.append((rel,'product-in-engineering'))
  if mode=='security':
   data=(root/rel).read_bytes()
   if b'\0' in data[:8192]:binary+=1;continue
   if (rel,hashlib.sha256(data).hexdigest()) not in EXEMPT and any(p.search(data) for p in PATTERNS):issues.append((rel,'credential-pattern'))
 if mode=='docs':
  for rel in CURRENT_DOCS:
   path=root/rel
   if not path.is_file():issues.append((rel,'missing-current-document'));continue
   text=path.read_text(encoding='utf-8')
   if not text.strip():issues.append((rel,'empty-document'))
   for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)',text):
    target=target.strip('<>').split('#')[0]
    if not target or re.match(r'^(?:[a-z]+:|/|[A-Za-z]:)',target):continue
    destination=(path.parent/target).resolve()
    if not destination.is_relative_to(root) or not destination.exists():issues.append((rel,'broken-local-link'))
 result={'status':'PASS' if not issues else 'FAIL','kind':kind,'mode':mode,'files':len(paths),'binary_assets':binary,'issues':[{'path':p,'rule':r} for p,r in issues],'credential_values_output':False}
 return result

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--kind',choices=('product','devteam'),required=True);parser.add_argument('--mode',choices=('ownership','security','docs'),required=True);args=parser.parse_args()
 result=check(Path(__file__).resolve().parents[1],args.kind,args.mode);print(json.dumps(result,ensure_ascii=True,sort_keys=True));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
