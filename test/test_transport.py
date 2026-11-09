import base64
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / 'scripts'


class FakeWorkerServer(http.server.BaseHTTPRequestHandler):
    def send_json(self, code, payload):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length) or b'{}')

    def do_GET(self):
        if self.path.startswith('/global/health'):
            return self.send_json(200, {'healthy': True})
        if self.path.startswith('/session/'):
            session_id = self.path.split('/')[2].split('?')[0]
            return self.send_json(200, self.server.messages.get(session_id, []))
        self.send_error(404)

    def do_POST(self):
        record = self.server.requests
        record.append({'path': self.path, 'body': self.read_json()})
        delay = getattr(self.server, 'delay', 0)
        if delay:
            time.sleep(delay)
        if self.path.startswith('/session?') or self.path.rstrip('/') == '/session':
            return self.send_json(200, {'id': 'ses_test'})
        if '/message' in self.path:
            if getattr(self.server, 'fail_next_message', False):
                return self.send_json(500, {'name': 'UnknownError'})
            session_id = self.path.split('/')[2].split('?')[0]
            events = self.server.parts
            self.server.messages[session_id] = events
            return self.send_json(200, {'parts': events})
        self.send_error(404)

    def log_message(self, format, *args):
        pass


class TransportScenarios(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        self.workspace = root / 'workspace'
        self.workspace.mkdir()
        self.parts = root / 'parts.json'
        self.requests = root / 'requests.json'
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeWorkerServer)
        self.server.messages = {}
        self.server.requests = []
        self.server.parts = []
        self.server.delay = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = os.environ.copy()
        self.env.pop('SHUNT_TIMEOUT_SECONDS', None)
        self.env['SHUNT_OPENCODE_SERVER'] = f'http://127.0.0.1:{self.server.server_port}'
        self.env['SHUNT_OPENCODE_DIR'] = str(self.workspace)
        self.env['SHUNT_OPENCODE_MODEL'] = 'test-provider/test-model'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self._tmp.cleanup()

    def cli(self, *args, env=None):
        return subprocess.run(
            args, env=env or self.env, capture_output=True, text=True, timeout=20,
        )

    def bulk_read(self, *paths, question='What is this?', env=None):
        files = []
        for index, lines in enumerate(paths):
            path = self.root / f'file{index}.txt'
            path.write_text(lines)
            files.append(str(path))
        return self.cli(str(SCRIPTS / 'bulk-read'), '--question', question, '--paths', *files, env=env)

    def code_write(self, target=None, spec='Write it.', env=None):
        reference = self.root / 'ref.txt'
        reference.write_text('existing patterns')
        args = [str(SCRIPTS / 'code-write'), '--spec', spec, '--reference', str(reference)]
        if target is not None:
            args += ['--target', str(target)]
        return self.cli(*args, env=env)

    def emit(self, *parts):
        self.server.parts = list(parts)

    def requests_seen(self):
        return self.server.requests

    def test_given_text_parts_when_reading_then_the_answer_is_printed(self):
        self.emit(
            {'type': 'step-start'},
            {'type': 'text', 'text': '- file0.txt:1 uses Alpha'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        result = self.bulk_read('one\n')
        self.assertEqual((result.returncode, result.stdout), (0, '- file0.txt:1 uses Alpha\n'), result.stderr)
        posts = [request for request in self.requests_seen() if request['path'].startswith('/session')]
        self.assertEqual(len(posts), 2)
        create = posts[0]
        self.assertIn('directory=', create['path'])
        self.assertEqual(create['body']['agent'], 'shunt-worker')
        self.assertEqual(create['body']['model'], {'providerID': 'test-provider', 'id': 'test-model'})
        message = posts[1]
        self.assertIn(str(self.workspace), message['path'])
        self.assertEqual(message['body']['agent'], 'shunt-worker')
        self.assertEqual(message['body']['model'], {'providerID': 'test-provider', 'modelID': 'test-model'})
        prompt = message['body']['parts'][0]['text']
        self.assertIn('file0.txt', prompt)
        self.assertIn('1\tone', prompt)
        self.assertIn('What is this?', prompt)
        self.assertIn('Do not use any tools', prompt)

    def test_given_an_error_part_when_reading_then_fail_cleanly(self):
        self.emit({'type': 'error', 'error': {'name': 'ModelError'}})
        result = self.bulk_read('one\n')
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: OpenCode reported an error during delegation'),
        )

    def test_given_a_missing_step_finish_when_reading_then_fail_cleanly(self):
        self.emit({'type': 'text', 'text': 'a partial answer'})
        result = self.bulk_read('one\n')
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: OpenCode returned an incomplete answer'),
        )

    def test_given_an_empty_answer_when_reading_then_fail_cleanly(self):
        self.emit({'type': 'step-finish', 'reason': 'stop'})
        result = self.bulk_read('one\n')
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: OpenCode returned an empty answer'),
        )

    def test_given_a_server_error_when_reading_then_fail_cleanly(self):
        self.server.fail_next_message = True
        self.emit(
            {'type': 'text', 'text': 'unused'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        result = self.bulk_read('one\n')
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: OpenCode delegation failed (HTTP 500): {"name": "UnknownError"}'),
        )

    def test_given_a_slow_answer_when_the_timeout_expires_then_fail_cleanly(self):
        self.env['SHUNT_TIMEOUT_SECONDS'] = '1'
        self.server.delay = 3
        self.emit(
            {'type': 'text', 'text': 'late'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        result = self.bulk_read('one\n')
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: OpenCode delegation timed out'),
        )

    def test_given_an_oversized_request_when_reading_then_reject_without_launching(self):
        env = self.env.copy()
        env['SHUNT_MAX_PAYLOAD_BYTES'] = '10'
        self.emit(
            {'type': 'text', 'text': 'unused'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        result = self.bulk_read('one line that is definitely longer than ten bytes\n', env=env)
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip().split(': ')[0]),
            (1, '', 'shunt'),
        )
        self.assertIn('byte limit', result.stderr)
        self.assertEqual(self.requests_seen(), [])

    def test_given_a_missing_model_when_reading_then_fail_cleanly(self):
        env = self.env.copy()
        env.pop('SHUNT_OPENCODE_MODEL')
        self.emit(
            {'type': 'text', 'text': 'unused'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        result = self.bulk_read('one\n', env=env)
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'Error: set SHUNT_OPENCODE_MODEL to a configured provider/model (e.g. openrouter/google/gemini-3.1-flash-lite).'),
        )

    def test_given_code_write_with_a_target_when_the_answer_has_fences_then_write_stripped_code(self):
        target = self.root / 'out.txt'
        self.emit(
            {'type': 'step-start'},
            {'type': 'text', 'text': '```\nprint("hi")\n```'},
            {'type': 'step-finish', 'reason': 'stop'},
        )
        result = self.code_write(target=target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), 'print("hi")\n')
        self.assertIn('Wrote 1 lines', result.stderr)
        posts = [request for request in self.requests_seen() if request['path'].startswith('/session')]
        self.assertEqual(len(posts), 2)
        self.assertIn('Spec:', posts[1]['body']['parts'][0]['text'])
        self.assertIn('existing patterns', posts[1]['body']['parts'][0]['text'])


if __name__ == '__main__':
    unittest.main()
