import base64
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name('plugin_driver_v2.mjs')


class FakeWorkerHandler(http.server.BaseHTTPRequestHandler):
    """Answers worker /session and /message routes and records every request."""

    def send_json(self, code, payload):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length) or b'{}')

    def do_GET(self):
        if self.path.startswith('/api/info'):
            return self.send_json(200, {'version': 'fake'})
        route = self.path.split('?')[0].rstrip('/')
        if route.startswith('/api/session/') and '/message/' in route:
            message_id = route.split('/message/')[1]
            return self.send_json(200, {'data': self.server.messages.get(message_id, {})})
        if route.startswith('/api/session/') and route.endswith('/message'):
            entries = [{'type': 'assistant', 'id': mid} for mid in self.server.messages]
            return self.send_json(200, {'data': entries})
        self.send_error(404)

    def do_POST(self):
        self.server.requests.append({'path': self.path, 'body': self.read_json()})
        route = self.path.split('?')[0].rstrip('/')
        if route == '/api/session':
            return self.send_json(200, {'data': {'id': 'ses_test'}})
        if route.startswith('/api/session/') and route.endswith('/model'):
            return self.send_json(204, {})
        if route.startswith('/api/session/') and route.endswith('/prompt'):
            if getattr(self.server, 'fail_next_message', False):
                return self.send_json(500, {'name': 'UnknownError'})
            return self.send_json(200, {'data': {'id': 'msg_user', 'type': 'user'}})
        if '/wait' in route:
            return self.send_json(204, {})
        self.send_error(404)

    def log_message(self, format, *args):
        pass


class WrappedReadScenarios(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeWorkerHandler)
        self.server.messages = {}
        self.server.requests = []
        self.server.parts = []
        self.server.fail_next_message = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.big = root / 'big.txt'
        self.big.write_text('\n'.join(f'line {n}' for n in range(1, 401)) + '\n')
        self.small = root / 'small.txt'
        self.small.write_text('first\nsecond\n')
        self.byte_heavy = root / 'byte_heavy.txt'
        self.byte_heavy.write_text('\n'.join(['x' * 500 for _ in range(300)]) + '\n')
        self.env = os.environ.copy()
        self.env.pop('SHUNT_DISABLE', None)
        self.env.pop('SHUNT_ALLOW_FALLBACK', None)
        self.env.pop('SHUNT_MIN_LINES', None)
        self.env.pop('SHUNT_MAX_BYTES', None)
        self.env['SHUNT_ROOT'] = str(REPO)
        self.env['SHUNT_OPENCODE_SERVER'] = f'http://127.0.0.1:{self.server.server_port}'
        self.env['SHUNT_OPENCODE_MODEL'] = 'test-provider/test-model'
        self.env['SHUNT_TIMEOUT_SECONDS'] = '10'
        self.env['SHUNT_TEST_SESSION_ID'] = 'ses_test'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self._tmp.cleanup()

    def drive(self, scenario, payload='', tool='', env_extra=None):
        env = self.env.copy()
        env.update(env_extra or {})
        args = ['bun', str(DRIVER), scenario, payload]
        if tool:
            args.append(tool)
        result = subprocess.run(
            args, env=env, capture_output=True, text=True, timeout=40,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def message_prompt(self):
        posts = [r for r in self.server.requests if r['path'].split('?')[0].rstrip('/').endswith('/prompt')]
        return posts[-1]['body']['text'] if posts else ''

    def emit(self, *parts, finish='stop'):
        self.server.messages['msg_a1'] = {'content': list(parts), 'finish': finish}

    def test_given_an_oversized_full_read_when_wrapped_then_the_worker_summary_is_returned(self):
        self.emit(
            {'type': 'step-start'},
            {'type': 'text', 'text': '- line 12 uses alpha'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        outcome = self.drive('read', json.dumps({'path': str(self.big)}))
        self.assertTrue(outcome['ok'], outcome)
        self.assertTrue(outcome['delegated'], outcome)
        self.assertFalse(outcome['passthrough'])
        self.assertIn('- line 12 uses alpha', outcome['content'])
        self.assertIn('400 lines', outcome['content'])
        prompt = self.message_prompt()
        self.assertIn('numbered="true"', prompt)
        self.assertIn('1\tline 1', prompt)
        self.assertIn('Question:', prompt)

    def test_given_an_oversized_read_when_a_task_is_present_then_the_delegation_serves_it(self):
        self.emit(
            {'type': 'text', 'text': '- line 12 uses alpha'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        task = [{'info': {'role': 'user'}, 'parts': [{'type': 'text', 'text': 'Fix the login bug'}]}]
        outcome = self.drive('read', json.dumps({'path': str(self.big)}),
                             env_extra={'SHUNT_TEST_SESSION_CONTEXT': json.dumps(task)})
        self.assertTrue(outcome['delegated'], outcome)
        self.assertIn('Fix the login bug', self.message_prompt())

    def test_given_a_small_file_when_wrapped_then_the_original_read_passes_through(self):
        self.emit({'type': 'text', 'text': 'unused'})
        outcome = self.drive('read', json.dumps({'path': str(self.small)}))
        self.assertTrue(outcome['passthrough'], outcome)
        self.assertEqual(self.server.requests, [])

    def test_given_a_targeted_read_when_offset_or_limit_is_set_then_it_passes_through(self):
        self.emit({'type': 'text', 'text': 'unused'})
        for targeted in ({'offset': 10}, {'limit': 50}, {'offset': 0, 'limit': 1}):
            outcome = self.drive('read', json.dumps({'path': str(self.big), **targeted}))
            self.assertTrue(outcome['passthrough'], (targeted, outcome))
        self.assertEqual(self.server.requests, [])

    def test_given_a_byte_heavy_file_under_the_line_cap_when_wrapped_then_it_delegates(self):
        self.assertLess(self.byte_heavy.read_text().count('\n'), 350)
        self.assertGreater(self.byte_heavy.stat().st_size, 131_072)
        self.emit({'type': 'text', 'text': '- summary'}, {'type': 'step-finish', 'reason': 'stop'})
        outcome = self.drive('read', json.dumps({'path': str(self.byte_heavy)}))
        self.assertTrue(outcome['delegated'], outcome)

    def test_given_a_missing_file_when_wrapped_then_the_original_read_surfaces_the_error(self):
        self.emit({'type': 'text', 'text': 'unused'})
        outcome = self.drive('read', json.dumps({'path': str(self.root / 'nope.txt')}))
        self.assertTrue(outcome['passthrough'], outcome)

    def test_given_threshold_overrides_when_wrapped_then_they_are_respected(self):
        self.emit({'type': 'text', 'text': 'unused'})
        outcome = self.drive('read', json.dumps({'path': str(self.big)}),
                             env_extra={'SHUNT_MIN_LINES': '1000', 'SHUNT_MAX_BYTES': str(1024 * 1024)})
        self.assertTrue(outcome['passthrough'], outcome)

    def test_given_a_worker_error_when_wrapped_then_the_read_fails_closed_with_remediation(self):
        self.emit({'type': 'error', 'error': {'name': 'ModelError'}})
        outcome = self.drive('read', json.dumps({'path': str(self.big)}))
        self.assertFalse(outcome['ok'], outcome)
        self.assertIn('NOT read', outcome['error'])
        self.assertIn('SHUNT_ALLOW_FALLBACK', outcome['error'])
        self.assertIn('worker.py ensure', outcome['error'])

    def test_given_a_worker_error_when_fallback_is_enabled_then_the_original_read_runs(self):
        self.emit({'type': 'error', 'error': {'name': 'ModelError'}})
        outcome = self.drive('read', json.dumps({'path': str(self.big)}),
                             env_extra={'SHUNT_ALLOW_FALLBACK': '1'})
        self.assertTrue(outcome['passthrough'], outcome)

    def test_given_a_slow_worker_when_the_timeout_expires_then_the_read_fails_closed(self):
        self.server.fail_next_message = True
        self.emit({'type': 'text', 'text': 'unused'}, {'type': 'step-finish', 'reason': 'stop'})
        env = self.env.copy()
        env['SHUNT_TIMEOUT_SECONDS'] = '1'
        outcome = self.drive('read', json.dumps({'path': str(self.big)}), env_extra=env)
        self.assertFalse(outcome['ok'], outcome)
        self.assertIn('exit', outcome['error'])

    def test_given_shunt_is_disabled_when_wrapped_then_everything_passes_through(self):
        self.emit({'type': 'text', 'text': 'unused'})
        outcome = self.drive('read', json.dumps({'path': str(self.big)}),
                             env_extra={'SHUNT_DISABLE': '1'})
        self.assertTrue(outcome['passthrough'], outcome)
        self.assertEqual(self.server.requests, [])

    def test_given_a_full_read_when_the_before_hook_fires_then_it_is_not_blocked(self):
        # Delegation is the wrapper's job; the hook must not block reads first.
        outcome = self.drive('hook', json.dumps({'path': str(self.big)}), 'read')
        self.assertFalse(outcome['blocked'], outcome)

    def test_given_a_bare_cat_of_an_oversized_file_when_the_hook_fires_then_it_blocks(self):
        outcome = self.drive('hook', json.dumps({'command': f'cat {self.big}'}), 'shell')
        self.assertTrue(outcome['blocked'], outcome)
        self.assertIn('bulk-reader', outcome['message'])

    def test_given_an_unrelated_shell_command_when_the_hook_fires_then_it_passes(self):
        outcome = self.drive('hook', json.dumps({'command': 'git status'}), 'shell')
        self.assertFalse(outcome['blocked'], outcome)


if __name__ == '__main__':
    unittest.main()