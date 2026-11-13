#!/usr/bin/env python3
"""Load the reviewed repository hooks into a normal GPT controller session.

Fallback for Codex builds that omit project hooks.json during discovery.
Keeps native hash trust review; never changes model, provider, login, or config.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EVENTS={'PreToolUse','PostToolUse','SubagentStop','Stop'}

def toml_value(value):
    if isinstance(value,str):return json.dumps(value,ensure_ascii=True)
    if isinstance(value,list):return '['+','.join(toml_value(x) for x in value)+']'
    if isinstance(value,dict):return '{'+','.join(json.dumps(k)+'='+toml_value(v) for k,v in value.items())+'}'
    if type(value) in (bool,int):return json.dumps(value)
    raise ValueError('Unsupported hook config value')

def command(sandbox='read-only'):
    value=json.loads((ROOT/'.codex/hooks.json').read_text(encoding='utf-8'))
    hooks=value.get('hooks')
    if not isinstance(hooks,dict) or set(hooks)!=EVENTS:raise ValueError('Expected the exact four reviewed events')
    if sandbox not in ('read-only','workspace-write'):raise ValueError('Unsupported sandbox')
    exe=shutil.which('codex')
    if not exe:raise ValueError('Codex CLI not found')
    return [exe,'--no-daemon','-C',str(ROOT),'--enable','hooks','-c','hooks='+toml_value(hooks),
            '--sandbox',sandbox,'--ask-for-approval','never','--no-alt-screen']

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sandbox',choices=('read-only','workspace-write'),default='read-only');p.add_argument('--check',action='store_true');a=p.parse_args()
    argv=command(a.sandbox)
    if a.check:
        print(json.dumps({'status':'CONFIG_VALID','events':sorted(EVENTS),'model_override':False,'trust_bypass':False,'native_trust_required':True}));return 0
    env={k:v for k,v in os.environ.items() if not k.startswith('CODEX_') or k=='CODEX_HOME'}
    env["LADS_PYTHON"] = str(Path(sys.executable).resolve(strict=True))
    return subprocess.run(argv,cwd=ROOT,env=env,check=False).returncode
if __name__=='__main__':raise SystemExit(main())
