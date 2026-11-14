"""Opt-in stdio only; fixed Registry project and existing engineering database."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lads_automation.project_registry import ProjectRegistry
from lads_knowledge.mcp_server import KnowledgeMCP, serve
from lads_knowledge.queries import KnowledgeReader


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', choices=('lifeos', 'devteam'), required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    registry = ProjectRegistry.load(ROOT / 'config/project-registry.json', base=ROOT.parent, workspace=ROOT.parent / 'LADS-Workspace')
    project = registry.project(args.project)
    registry.validate_repository(args.project)
    with KnowledgeReader.open(registry.knowledge_home(), 'dual-repository.sqlite3', project_id=project.project_id) as reader:
        if args.check:
            print(json.dumps({'status': 'READONLY_READY', 'project_id': project.project_id, 'tools': sorted(tool['name'] for tool in KnowledgeMCP.catalog()), 'network': False, 'writes': False}))
        else:
            serve(KnowledgeMCP(reader), sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'event':'knowledge_mcp_startup_failed','error_type':type(exc).__name__,
                          'message':'Validate the registered engineering environment; no private arguments are logged.'}), file=sys.stderr)
        raise SystemExit(2)
