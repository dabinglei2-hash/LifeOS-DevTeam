"""Explicit repository and knowledge boundaries; never discovers product data."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

EXPECTED={
 'lifeos':('LifeOS','LifeOS-Product','product'),
 'devteam':('LifeOS-DevTeam','LifeOS-DevTeam','engineering'),
}

class RegistryError(ValueError):pass

def safe_path(path:Path)->Path:
 if not path.is_absolute():raise RegistryError('Absolute local root required')
 for p in (path,*path.parents):
  if p.exists() and (p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&1024):
   raise RegistryError('Reparse paths are not accepted')
 return path.resolve()

@dataclass(frozen=True)
class Project:
 key:str
 project_id:str
 namespace:str
 repository:str
 root:Path
 qa_profile:str

class ProjectRegistry:
 def __init__(self,document:dict,*,base:Path,workspace:Path):
  self.base=safe_path(base);self.workspace=safe_path(workspace)
  if self.workspace!=self.base/'LADS-Workspace':raise RegistryError('Private workspace must be the separate LADS-Workspace sibling')
  if document.get('schema')!='lads-project-registry-v1' or set(document)!= {'schema','projects'}:
   raise RegistryError('Unsupported registry format')
  if set(document['projects'])!=set(EXPECTED):raise RegistryError('Exact two-project registry required')
  self.projects={};seen=set()
  for key,(repo,directory,profile) in EXPECTED.items():
   row=document['projects'][key]
   if set(row)!= {'project_id','namespace','repository','directory','qa_profile'}:raise RegistryError('Unexpected project fields')
   ident=str(uuid.UUID(row['project_id']))
   if ident in seen:raise RegistryError('Project IDs must be independent')
   seen.add(ident)
   if row['repository']!='dabinglei2-hash/'+repo or row['directory']!=directory or row['qa_profile']!=profile or row['namespace']!='lads.'+key:
    raise RegistryError('Project identity or QA profile differs from the fixed registry')
   root=safe_path(self.base/directory)
   if root==self.workspace or root.is_relative_to(self.workspace) or self.workspace.is_relative_to(root):raise RegistryError('Workspace and code roots overlap')
   self.projects[key]=Project(key,ident,row['namespace'],row['repository'],root,profile)

 @classmethod
 def load(cls,path:Path,*,base:Path,workspace:Path):
  if path.stat().st_size>16384:raise RegistryError('Registry is too large')
  return cls(json.loads(path.read_text(encoding='utf-8')),base=base,workspace=workspace)

 def project(self,key:str)->Project:
  if key not in self.projects:raise RegistryError('Unknown project')
  return self.projects[key]

 def validate_repository(self,key:str)->dict:
  p=self.project(key)
  if not (p.root/'.git').exists():raise RegistryError('Repository is not initialized')
  result=subprocess.run(['git','-C',str(p.root),'remote','get-url','origin'],capture_output=True,text=True,encoding='utf-8',timeout=15)
  expected='https://github.com/'+p.repository+'.git'
  if result.returncode or result.stdout.strip()!=expected:raise RegistryError('Origin does not bind the selected project')
  head=subprocess.run(['git','-C',str(p.root),'rev-parse','--verify','HEAD^{commit}'],capture_output=True,text=True,encoding='utf-8',timeout=15)
  if head.returncode or len(head.stdout.strip())!=40 or any(c not in '0123456789abcdef' for c in head.stdout.strip()):raise RegistryError('Repository requires a verified existing HEAD commit')
  return {'project_id':p.project_id,'namespace':p.namespace,'repository':p.repository,'root':str(p.root),'qa_profile':p.qa_profile,'head':head.stdout.strip() if head.returncode==0 else None,'automatic_publication':'BLOCKED_PENDING_INDEPENDENT_ACCEPTANCE'}

 def knowledge_home(self)->Path:
  return self.workspace/'knowledge'

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__)
 root=Path(__file__).resolve().parents[1]
 parser.add_argument('--registry',type=Path,default=root/'config/project-registry.json')
 parser.add_argument('--base',type=Path,default=root.parent)
 parser.add_argument('--workspace',type=Path)
 parser.add_argument('--db',default='dual-repository.sqlite3')
 sub=parser.add_subparsers(dest='command',required=True)
 for command in ['status','init-knowledge','index','search','create-task']:
  item=sub.add_parser(command);item.add_argument('--project',choices=tuple(EXPECTED),required=True)
  if command=='search':item.add_argument('--query',required=True);item.add_argument('--limit',type=int,default=8)
  if command=='create-task':item.add_argument('--summary',required=True)
 args=parser.parse_args(argv)
 registry=ProjectRegistry.load(args.registry,base=args.base,workspace=args.workspace or args.base/'LADS-Workspace')
 project=registry.project(args.project);status=registry.validate_repository(args.project)
 if args.command=='status':return status
 from lads_knowledge.database import FileStore
 from lads_knowledge.indexer import KnowledgeIndexer,repository_ref,DEFAULT_DOCS
 from lads_knowledge.queries import KnowledgeReader
 if args.command=='search':
  with KnowledgeReader.open(registry.knowledge_home(),args.db,project_id=project.project_id) as reader:
   return reader.search_project_knowledge(args.query,limit=args.limit)
 with FileStore.open(registry.knowledge_home(),args.db) as store:
  record=store.register_project('registry:'+project.key,project_id=project.project_id,root_ref=repository_ref(project.root))
  if record['id']!=project.project_id:raise RegistryError('Existing project has a different ID')
  if args.command=='init-knowledge':return {'status':'REGISTERED','project_id':project.project_id,'namespace':project.namespace,'home':str(registry.knowledge_home())}
  if args.command=='create-task':return store.create_task(project.project_id,args.summary,assigned_agent='gpt-controller')
  documents=[p for p in DEFAULT_DOCS if (project.root/p).is_file()]
  return KnowledgeIndexer(store,project.project_id,documents=documents).sync_documents(project.root)

if __name__=='__main__':
 try:print(json.dumps(main(),ensure_ascii=True,sort_keys=True))
 except Exception as exc:
  print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__,'message':'Registry operation failed; use the bounded local diagnostics.'}),file=sys.stderr);raise SystemExit(2)
