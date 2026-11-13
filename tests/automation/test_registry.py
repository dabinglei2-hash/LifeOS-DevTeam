import copy,json,tempfile,unittest,uuid
from pathlib import Path
from lads_automation.project_registry import ProjectRegistry,RegistryError,EXPECTED
from lads_knowledge.service import KnowledgeStore
from lads_knowledge.queries import KnowledgeReader

def document():
 return {'schema':'lads-project-registry-v1','projects':{
  key:{'project_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'https://github.com/dabinglei2-hash/'+row[0])),'namespace':'lads.'+key,'repository':'dabinglei2-hash/'+row[0],'directory':row[1],'qa_profile':row[2]} for key,row in EXPECTED.items()}}

class RegistryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name).resolve();self.data=document()
 def load(self,data=None,workspace=None):return ProjectRegistry(data or self.data,base=self.base,workspace=workspace or self.base/'LADS-Workspace')
 def test_independent_ids_and_namespaces(self):
  r=self.load();self.assertNotEqual(r.project('lifeos').project_id,r.project('devteam').project_id);self.assertNotEqual(r.project('lifeos').namespace,r.project('devteam').namespace)
 def test_private_workspace_sibling(self):self.assertEqual(self.load().knowledge_home(),self.base/'LADS-Workspace/knowledge')
 def test_duplicate_ids_rejected(self):
  self.data['projects']['devteam']['project_id']=self.data['projects']['lifeos']['project_id']
  with self.assertRaises(RegistryError):self.load()
 def test_wrong_repository_rejected(self):
  self.data['projects']['lifeos']['repository']='foreign/other'
  with self.assertRaises(RegistryError):self.load()
 def test_wrong_profile_rejected(self):
  self.data['projects']['lifeos']['qa_profile']='engineering'
  with self.assertRaises(RegistryError):self.load()
 def test_path_escape_rejected(self):
  self.data['projects']['lifeos']['directory']='../foreign'
  with self.assertRaises(RegistryError):self.load()
 def test_product_workspace_rejected(self):
  with self.assertRaises(RegistryError):self.load(workspace=self.base/'LifeOS-Product/work')
 def test_unknown_project_rejected(self):
  with self.assertRaises(RegistryError):self.load().project('foreign')
 def test_uninitialized_repo_fails_closed(self):
  with self.assertRaises(RegistryError):self.load().validate_repository('lifeos')
 def test_unborn_repository_with_correct_origin_is_rejected(self):
  import subprocess
  root=self.base/'LifeOS-Product';root.mkdir()
  for command in (['git','init','-q',str(root)],['git','-C',str(root),'remote','add','origin','https://github.com/dabinglei2-hash/LifeOS.git']):subprocess.run(command,check=True)
  with self.assertRaises(RegistryError):self.load().validate_repository('lifeos')
 def test_existing_commit_with_correct_origin_is_accepted(self):
  import subprocess
  root=self.base/'LifeOS-Product';root.mkdir()
  for command in (['git','init','-q',str(root)],['git','-C',str(root),'remote','add','origin','https://github.com/dabinglei2-hash/LifeOS.git'],['git','-C',str(root),'-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','--allow-empty','-qm','synthetic baseline']):subprocess.run(command,check=True)
  self.assertRegex(self.load().validate_repository('lifeos')['head'],r'^[0-9a-f]{40}$')
 def test_unexpected_fields_rejected(self):
  self.data['token']='never-allowed'
  with self.assertRaises(RegistryError):self.load()
 def test_project_namespaces_isolate_search(self):
  from lads_knowledge.indexer import KnowledgeIndexer
  from sqlalchemy import text
  with KnowledgeStore.memory() as store:
   r=self.load();ids=[r.project(x).project_id for x in EXPECTED]
   for key,ident in zip(EXPECTED,ids):store.register_project('registry:'+key,project_id=ident)
   first=store.create_task(ids[0],'product-only-marker');second=store.create_task(ids[1],'devteam-only-marker')
   # Queries are project-bound even when task IDs from another project are supplied.
   from lads_knowledge.service import NotFoundError
   with store.reader(ids[0]) as reader:
    with self.assertRaises(NotFoundError):reader.get_task_history(second['id'])
   self.assertNotEqual(first['project_id'],second['project_id'])

if __name__=='__main__':unittest.main()
