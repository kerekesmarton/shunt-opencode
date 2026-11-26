import http.server
import base64
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest


WORKER = Path(os.environ.get(
    'SHUNT_TEST_WORKER_PATH',
    Path(__file__).resolve().parents[1] / 'scripts/lib/worker.py',
))


class MalformedHealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"healthy": false}')

    def log_message(self, format, *args):
        pass


class PasswordHealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Authorization') != self.server.authorization:
            self.send_error(401)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"healthy": true}')

    def log_message(self, format, *args):
        pass


class WorkerHealthScenarios(unittest.TestCase):
    def test_given_a_non_object_health_response_when_ensuring_then_reject_cleanly(self):
        with http.server.ThreadingHTTPServer(('127.0.0.1', 0), MalformedHealthHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env.pop('OPENCODE_SERVER_PASSWORD', None)
                env.pop('OPENCODE_SERVER_USERNAME', None)
                env['SHUNT_OPENCODE_SERVER'] = f'http://127.0.0.1:{server.server_port}'
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'ensure'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: Configured OpenCode server is unreachable, unhealthy, or has an unsafe URL'),
        )

    def test_given_an_authenticated_external_server_when_ensuring_with_valid_credentials_then_return_its_url(self):
        password = 'test-password'
        authorization = 'Basic ' + base64.b64encode(f'opencode:{password}'.encode()).decode()
        with http.server.ThreadingHTTPServer(('127.0.0.1', 0), PasswordHealthHandler) as server:
            server.authorization = authorization
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env['SHUNT_OPENCODE_SERVER'] = f'http://127.0.0.1:{server.server_port}'
                env['OPENCODE_SERVER_PASSWORD'] = password
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'ensure'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, env['SHUNT_OPENCODE_SERVER'] + '\n', ''))

    def test_given_an_authenticated_external_server_when_ensuring_without_credentials_then_reject_cleanly(self):
        with http.server.ThreadingHTTPServer(('127.0.0.1', 0), PasswordHealthHandler) as server:
            server.authorization = 'Basic ' + base64.b64encode(b'opencode:test-password').decode()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env.pop('OPENCODE_SERVER_PASSWORD', None)
                env['SHUNT_OPENCODE_SERVER'] = f'http://127.0.0.1:{server.server_port}'
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'ensure'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: Configured OpenCode server is unreachable, unhealthy, or has an unsafe URL'),
        )


if __name__ == '__main__':
    unittest.main()
