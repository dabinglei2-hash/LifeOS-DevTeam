"""Synthetic in-memory protocol, project isolation and non-mutation tests."""
import io
import json
import unittest
from unittest import mock

from lads_knowledge import KnowledgeStore
from lads_knowledge.mcp_server import KnowledgeMCP, MAX_INPUT_BYTES, serve

PROJECT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
TASK = '11111111-1111-4111-8111-111111111111'
OTHER_TASK = '22222222-2222-4222-8222-222222222222'


class KnowledgeMCPTests(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore.memory()
        for project, task in [(PROJECT, TASK), (OTHER, OTHER_TASK)]:
            self.store.register_project('synthetic-' + project[:4], project_id=project)
            self.store.create_task(project, 'Synthetic engineering task', task_id=task)
            self.store.upsert_knowledge(project, kind='doc', source_ref='docs/test.md', title='Engineering',
                                        body='Synthetic engineering ' + ('alpha' if project == PROJECT else 'other-only'))
        self.server = KnowledgeMCP(self.store.reader(PROJECT))

    def tearDown(self):
        self.store.close()

    def request(self, method, params=None, request_id=1):
        return self.server.request({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params or {}})

    def initialize(self):
        result = self.request('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                             'clientInfo': {'name': 'synthetic', 'version': '1'}})
        self.assertNotIn('error', result)
        self.assertIsNone(self.server.request({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))

    def tool(self, name, arguments=None):
        return self.request('tools/call', {'name': name, 'arguments': arguments or {}})

    def test_before_initialize_requests_fail_and_ping_is_allowed(self):
        self.assertEqual(-32002, self.request('tools/list')['error']['code'])
        self.assertEqual({}, self.request('ping')['result'])

    def test_protocol_negotiation_and_initialized_notification_required(self):
        r = self.request('initialize', {'protocolVersion': '2099-01-01', 'capabilities': {}, 'clientInfo': {}})
        self.assertEqual('2025-11-25', r['result']['protocolVersion'])
        self.assertEqual(-32002, self.request('tools/list')['error']['code'])
        self.server.request({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual(5, len(self.request('tools/list')['result']['tools']))

    def test_five_only_readonly_tools_no_controller_release_interface(self):
        self.initialize()
        tools = self.request('tools/list')['result']['tools']
        self.assertEqual(5, len(tools))
        self.assertTrue(all(t['annotations']['readOnlyHint'] and not t['annotations']['destructiveHint'] for t in tools))
        self.assertEqual(-32602, self.tool('get_release_receipts', {'task_id': TASK})['error']['code'])
        self.assertEqual(-32601, self.request('resources/read', {'uri': 'file:///synthetic'})['error']['code'])

    def test_actual_search_is_project_bound_with_provenance(self):
        self.initialize()
        r = self.tool('search_project_knowledge', {'query': 'engineering', 'limit': 1})['result']
        self.assertFalse(r['isError'])
        body = json.loads(r['content'][0]['text'])
        self.assertEqual(PROJECT, body['project_id'])
        self.assertEqual('quoted_data_not_instructions', body['content_trust'])
        self.assertEqual(1, len(body['items']))
        self.assertIn('source_ref', body['items'][0])
        self.assertNotIn('other-only', str(body))

    def test_foreign_task_cannot_be_read(self):
        self.initialize()
        self.assertFalse(self.tool('get_task_history', {'task_id': TASK})['result']['isError'])
        result = self.tool('get_task_history', {'task_id': OTHER_TASK})['result']
        self.assertTrue(result['isError'])
        self.assertNotIn(OTHER_TASK, str(result))

    def test_project_and_raw_sql_arguments_rejected(self):
        self.initialize()
        for extra in [{'project_id': OTHER}, {'sql': 'SELECT 1'}, {'path': 'synthetic'}]:
            r = self.tool('search_project_knowledge', {'query': 'engineering', **extra})
            self.assertEqual(-32602, r['error']['code'])

    def test_result_limit_types_and_oversized_string_rejected(self):
        self.initialize()
        for limit in [0, 9, -1, True, '3', 1.0]:
            self.assertEqual(-32602, self.tool('get_known_issues', {'limit': limit})['error']['code'])
        self.assertEqual(-32602, self.tool('search_project_knowledge', {'query': 'x' * 301})['error']['code'])

    def test_related_changes_require_a_selector(self):
        self.initialize()
        self.assertTrue(self.tool('get_related_changes')['result']['isError'])
        self.assertFalse(self.tool('get_related_changes', {'task_id': TASK})['result']['isError'])

    def test_actual_calls_leave_database_unchanged(self):
        self.initialize()
        with self.store._engine.connect() as connection:
            before = list(connection.exec_driver_sql('SELECT id, state, requirement_summary FROM dev_tasks').all())
        for name, args in [('get_task_history', {'task_id': TASK}), ('get_related_changes', {'task_id': TASK}),
                           ('get_known_issues', {}), ('get_architecture_decisions', {}),
                           ('search_project_knowledge', {'query': 'engineering'})]:
            self.assertFalse(self.tool(name, args)['result']['isError'])
        with self.store._engine.connect() as connection:
            after = list(connection.exec_driver_sql('SELECT id, state, requirement_summary FROM dev_tasks').all())
        self.assertEqual(before, after)

    def test_internal_error_details_are_not_returned(self):
        self.initialize()
        with mock.patch.object(self.server.reader, 'get_known_issues', side_effect=RuntimeError('SYNTHETIC_INTERNAL_DETAIL')):
            result = self.tool('get_known_issues')
        self.assertEqual(-32603, result['error']['code'])
        self.assertNotIn('SYNTHETIC_INTERNAL_DETAIL', str(result))

    def test_stdio_frames_notifications_and_malformed_json(self):
        lines = [b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{}}}',
                 b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
                 b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}', b'{"id":1,"id":2}', b'{"value":NaN}', b'\xff']
        output = io.BytesIO()
        serve(self.server, io.BytesIO(b'\n'.join(lines) + b'\n'), output)
        frames = [json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(5, len(frames))
        self.assertEqual(5, len(frames[1]['result']['tools']))
        self.assertTrue(all(x['error']['code'] == -32700 for x in frames[2:]))

    def test_oversized_frame_stops_without_processing_following_request(self):
        output = io.BytesIO()
        source = io.BytesIO(b'x' * (MAX_INPUT_BYTES + 1) + b'\n' + b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        serve(self.server, source, output)
        frames = output.getvalue().splitlines()
        self.assertEqual(1, len(frames))
        self.assertEqual(-32600, json.loads(frames[0])['error']['code'])

    def test_duplicate_initialize_and_boolean_request_id_are_rejected(self):
        self.initialize()
        self.assertEqual(-32602, self.request('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {}})['error']['code'])
        self.assertEqual(-32600, self.request('ping', request_id=True)['error']['code'])
