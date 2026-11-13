"""The opt-in main exception must never turn into implicit independent approval."""
from pathlib import Path
import unittest
from unittest import mock
from lads_automation.engine import AutomationEngine,DeliveryError
from lads_automation.evidence import TaskSpec,EvidenceProof,EvidenceError
from lads_automation.state import MemoryJournal
from lads_automation.main_acceptance import accept_main_exception
from lads_automation.qa import BrokerVerificationError

class MainExceptionTests(unittest.TestCase):
    def setUp(self):
        self.spec=TaskSpec('1c432bf8-ae19-4148-aef4-ed6f51ce7cc7','a'*40,'b'*64,('index.html',),('node --check app.js',))
        self.engine=AutomationEngine(self.spec,MemoryJournal());self.engine.register('Synthetic main exception')
        binding={'task_id':self.spec.task_id,'source_head':self.spec.source_head,'candidate_sha256':self.spec.candidate_sha256}
        self.grant={**binding,'grantor':'user','scope':'stage3-engineering-only','allow_main_implementation':True,'allow_main_testing':True,'allow_main_review':True,'instruction_sha256':'c'*64}
        self.review={**binding,'actor':'GPT main controller','independent':False,'status':'APPROVED','review_type':'final','simulation':False}
        self.report={'receipt_sha256':'d'*64,'suites':[{'command':'node --check app.js','process':{'exit_code':0}}]}
    def accept(self):
        return accept_main_exception(self.engine,candidate_root=Path('.'),broker_report=self.report,authorization=self.grant,review=self.review)
    def test_default_gate_does_not_invoke_exception(self):
        self.assertFalse(self.engine.status()['ready'])
        self.grant.pop('allow_main_review')
        with self.assertRaises(DeliveryError):self.accept()
    def test_scope_and_task_are_explicit(self):
        self.grant['task_id']='other'
        with self.assertRaises(DeliveryError):self.accept()
    def test_cannot_impersonate_independent_reviewer(self):
        self.review['independent']=True
        with self.assertRaises(DeliveryError):self.accept()
    def test_failed_real_broker_closes_delivery(self):
        with mock.patch('lads_automation.main_acceptance.validate_broker_report',side_effect=BrokerVerificationError('synthetic failed process')):
            with self.assertRaises(BrokerVerificationError):self.accept()
        self.assertEqual('QA_FAILED',self.engine.status()['status'])
        with self.assertRaises(DeliveryError):self.engine.sync_documents({})
    def test_pass_remains_truthful_and_cannot_be_replaced(self):
        with mock.patch('lads_automation.main_acceptance.validate_broker_report',return_value={'status':'PASS'}):
            state=self.accept()
            self.assertFalse(state['ready'])
            self.assertFalse(state['evidence']['reviewer_independent'])
            self.assertFalse(self.engine._proof(state).as_dict()['reviewer_independent'])
            self.assertEqual('main-authorized-exception',state['evidence']['acceptance_mode'])
            with self.assertRaises(DeliveryError):self.accept()
    def test_prescribed_process_must_actually_exit_zero(self):
        self.report['suites'][0]['process']['exit_code']=1
        with mock.patch('lads_automation.main_acceptance.validate_broker_report',return_value={'status':'PASS'}):
            with self.assertRaises(DeliveryError):self.accept()
    def test_exception_proof_requires_authorization_digest(self):
        with self.assertRaises(EvidenceError):
            EvidenceProof(self.spec.task_id,'a'*64,'b'*64,'c'*64,'d'*64,(),acceptance_mode='main-authorized-exception')

class SessionConfigTests(unittest.TestCase):
    def test_native_toml_preserves_exact_reviewed_commands(self):
        import json,tomllib
        from scripts import lads_session
        hooks=json.loads((lads_session.ROOT/'.codex/hooks.json').read_text())['hooks']
        self.assertEqual(hooks,tomllib.loads('hooks='+lads_session.toml_value(hooks))['hooks'])
    def test_launcher_never_switches_provider_or_bypasses_trust(self):
        from scripts import lads_session
        with mock.patch.object(lads_session.shutil,'which',return_value='codex.exe'):
            argv=lads_session.command()
        self.assertNotIn('--dangerously-bypass-hook-trust',argv)
        self.assertNotIn('--profile',argv)
        self.assertNotIn('--model',argv)
        self.assertFalse(any('model_provider=' in x for x in argv))
        self.assertEqual('read-only',argv[argv.index('--sandbox')+1])
