"""Compact release receipts remain complete without expanding Agent context."""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock
from lads_knowledge import KnowledgeStore
from lads_knowledge.queries import KnowledgeReader
from lads_knowledge.safety import KnowledgeError
from lads_knowledge.service import NotFoundError
from lads_automation import github_release as release

class CompactReleaseReceiptTests(unittest.TestCase):
 def setUp(self):
  self.store=KnowledgeStore.memory();self.addCleanup(self.store.close)
  self.project=self.store.register_project('Synthetic release evidence')['id']
  self.task=self.store.create_task(self.project,'Synthetic complete receipt verification')['id']
 def change(self,path,digest='a'*64):
  return self.store.record_change(self.project,self.task,file_path=path,change_type='document_update',source_ref=path,source_raw_sha=digest,observed_head='b'*40,dirty=True)
 def test_compact_receipts_keep_all_documents_when_agent_context_is_trimmed(self):
  for n in range(8):self.change(f'docs/FACT-{n}.md')
  for n in range(20):self.store.record_event(self.project,self.task,'agent_stage',{'summary':'Synthetic engineering evidence. '*50,'ordinal':n})
  self.store.record_event(self.project,self.task,'index_synced',{'evidence':'synthetic index receipt'})
  with self.store.reader(self.project) as reader:
   context=reader.get_task_history(self.task,limit=20)
   self.assertTrue(context['truncated']);self.assertLess(len(context['items'][0]['changes']),8)
   compact=reader.get_release_receipts(self.task)
  self.assertFalse(compact['truncated']);self.assertEqual(8,len(compact['items'][0]['changes']))
  self.assertEqual(1,len(compact['items'][0]['events']))
  self.assertNotIn('payload_json',json.dumps(compact));self.assertNotIn('requirement_summary',compact['items'][0])
 def test_controller_loader_uses_compact_api_not_truncated_context(self):
  self.change('docs/STATUS.md')
  with mock.patch.object(release.KnowledgeReader,'open',return_value=self.store.reader(self.project)),mock.patch.object(KnowledgeReader,'get_task_history',side_effect=AssertionError('Agent context is unsuitable for release evidence')):
   result=release._knowledge_history('unused-owned-home','unused.sqlite3',self.project,self.task)
  self.assertEqual(1,len(result['items'][0]['changes']));self.assertTrue(result['controller_receipts'])
 def test_other_project_task_is_not_visible(self):
  other=self.store.register_project('Other synthetic project')['id']
  with self.store.reader(other) as reader:
   with self.assertRaises(NotFoundError):reader.get_release_receipts(self.task)
 def test_explicit_overflow_fails_instead_of_discarding_documents(self):
  for n in range(21):self.change(f'docs/FACT-{n}.md')
  with self.store.reader(self.project) as reader:
   with self.assertRaises(KnowledgeError):reader.get_release_receipts(self.task,limit=20)
 def test_limits_remain_bounded(self):
  with self.store.reader(self.project) as reader:
   for value in (0,-1,21):
    with self.assertRaises(KnowledgeError):reader.get_release_receipts(self.task,limit=value)
 def test_preserved_older_rows_do_not_replace_current_receipts(self):
  commands=tuple(release.unit_qa.STAGE4_QA_COMMANDS);qa_sha='1'*64;doc_sha='2'*64
  self.change('docs/STATUS.md','3'*64)
  self.change('docs/STATUS.md',doc_sha)
  for command in commands:
   self.store.record_test(self.project,self.task,test_name='Synthetic prior failure',command=command,status='failed',output_sha='4'*64,started_at='2026-01-01T00:00:00Z')
   self.store.record_test(self.project,self.task,test_name='LADS prescribed QA command',command=command,status='passed',output_sha=qa_sha,started_at='2026-01-02T00:00:00Z')
  event_id=str(uuid.uuid4());self.store.record_event(self.project,self.task,'index_synced',{},event_id=event_id)
  with self.store.reader(self.project) as reader:history=reader.get_release_receipts(self.task)
  index={'index_event_id':event_id,'source_head':'a'*40,'candidate_sha256':'b'*64,'working_content_commit':None,'documents':[{'path':'docs/STATUS.md','raw_sha256':doc_sha}],'tests':[{'name':'LADS prescribed QA command'} for _ in commands]}
  gate={'bundle':SimpleNamespace(task_id=self.task,source_head='a'*40,candidate_sha256='b'*64),'project_id':self.project,'journal_state':{'status':'DELIVERY_READY','ready':True,'documents_receipt':{'documents':{'docs/STATUS.md':{'before_sha256':'c'*64,'after_sha256':doc_sha}}},'index_receipt':index},'evidence':{'qa_sha256':qa_sha},'commands':commands,'index_receipt':index}
  release._validate_knowledge_receipt(gate,history)
  self.assertEqual(2*len(commands),len(history['items'][0]['tests']))
  history['items'][0]['tests'][0]['output_sha']='f'*64
  with self.assertRaises(release.ReleaseGateError):release._validate_knowledge_receipt(gate,history)

if __name__=='__main__':unittest.main()
