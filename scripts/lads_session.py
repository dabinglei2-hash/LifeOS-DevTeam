#!/usr/bin/env python3
"""Load the reviewed repository hooks into a normal GPT controller session.

Project hooks are preferred; legacy inline fallback is explicit opt-in.
Keeps native hash trust review; never changes model, provider, login, or config.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
EVENTS={'PreToolUse','PostToolUse','SubagentStop','Stop'}

def toml_value(value):
    if isinstance(value,str):return json.dumps(value,ensure_ascii=True)
    if isinstance(value,list):return '['+','.join(toml_value(x) for x in value)+']'
    if isinstance(value,dict):return '{'+','.join(json.dumps(k)+'='+toml_value(v) for k,v in value.items())+'}'
    if type(value) in (bool,int):return json.dumps(value)
    raise ValueError('Unsupported hook config value')

def environment(base=None):
    original = os.environ if base is None else base
    env = {k:v for k,v in original.items() if not k.startswith('CODEX_') or k=='CODEX_HOME'}
    env["LADS_PYTHON"] = str(Path(sys.executable).resolve(strict=True))
    if os.name == 'nt' and not env.get('HTTPS_PROXY'):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
                enabled = winreg.QueryValueEx(key, 'ProxyEnable')[0]
                server = winreg.QueryValueEx(key, 'ProxyServer')[0] if enabled else ''
        except OSError:
            # No Windows proxy configuration is a supported direct connection.
            server = ''
        if server:
            if '=' in server:
                options = dict(part.split('=', 1) for part in server.split(';') if '=' in part)
                server = options.get('https') or options.get('http') or ''
            if server:
                candidate = server if '://' in server else 'http://' + server
                parsed = urlsplit(candidate)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError('Windows proxy configuration is not a credential-free HTTP proxy')
                env['HTTPS_PROXY'] = candidate
    return env


def command(sandbox='read-only', knowledge_project=None, legacy_inline_hooks=False):
    value=json.loads((ROOT/'.codex/hooks.json').read_text(encoding='utf-8'))
    hooks=value.get('hooks')
    if not isinstance(hooks,dict) or set(hooks)!=EVENTS:raise ValueError('Expected the exact four reviewed events')
    if sandbox not in ('read-only','workspace-write'):raise ValueError('Unsupported sandbox')
    exe=shutil.which('codex')
    if not exe:raise ValueError('Codex CLI not found')
    argv = [exe,'--no-daemon','-C',str(ROOT),'--enable','hooks',
            '--sandbox',sandbox,'--ask-for-approval','never','--no-alt-screen']
    if legacy_inline_hooks:
        argv += ['-c','hooks='+toml_value(hooks)]
    if knowledge_project is not None:
        if knowledge_project not in ('lifeos', 'devteam'):
            raise ValueError('Unknown engineering knowledge project')
        tools = ['search_project_knowledge','get_task_history','get_related_changes','get_known_issues','get_architecture_decisions']
        settings = {'command': str(Path(sys.executable).resolve(strict=True)),
                    'args': ['-B',str(ROOT/'scripts/lads_knowledge_mcp.py'),'--project',knowledge_project],
                    'cwd':str(ROOT), 'enabled':True, 'required':True,
                    'enabled_tools':tools, 'startup_timeout_sec':30, 'tool_timeout_sec':15}
        argv += ['-c','mcp_servers.lads_knowledge='+toml_value(settings)]
    return argv

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sandbox',choices=('read-only','workspace-write'),default='read-only');p.add_argument('--check',action='store_true');p.add_argument('--knowledge-project',choices=('lifeos','devteam'));p.add_argument('--legacy-inline-hooks',action='store_true',help='Only for older CLI versions whose native hooks/list reports no project hooks');a=p.parse_args()
    argv=command(a.sandbox, a.knowledge_project, a.legacy_inline_hooks)
    if a.check:
        print(json.dumps({'status':'CONFIG_VALID','events':sorted(EVENTS),'model_override':False,'trust_bypass':False,'native_trust_required':True,'knowledge_project':a.knowledge_project,'hook_source':'legacy-inline' if a.legacy_inline_hooks else 'project'}));return 0
    env=environment()
    return subprocess.run(argv,cwd=ROOT,env=env,check=False).returncode
if __name__=='__main__':raise SystemExit(main())
