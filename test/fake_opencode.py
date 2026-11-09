#!/usr/bin/env python3
import base64
import http.server
import json
import os
from pathlib import Path
import signal
import threading
import time
import sys


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        credentials = f"{os.environ.get('OPENCODE_SERVER_USERNAME', 'opencode')}:{os.environ['OPENCODE_SERVER_PASSWORD']}"
        authorization = 'Basic ' + base64.b64encode(credentials.encode()).decode()
        if self.headers.get('Authorization') != authorization:
            self.send_error(401)
            return
        if self.path.startswith('/session'):
            if os.environ.get('SHUNT_TEST_FAKE_NOT_READY'):
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')
            return
        if self.path != '/global/health' and self.path != '/provider':
            self.send_error(404)
            return
        unhealthy_file = os.environ.get('SHUNT_TEST_FAKE_UNHEALTHY_PID_FILE')
        if unhealthy_file and Path(unhealthy_file).exists() and Path(unhealthy_file).read_text() == str(os.getpid()):
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if self.path == '/provider':
            self.wfile.write(json.dumps({'all': [{'id': 'fake', 'models': {'m1': {}}}]}).encode())
            return
        self.wfile.write(json.dumps({'healthy': True}).encode())

    def do_POST(self):
        if self.path != '/shutdown':
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format, *args):
        pass


with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
    marker = os.environ.get('SHUNT_TEST_FAKE_RUN_MARKER')
    if marker and sys.argv[1:2] == ['run']:
        Path(marker).write_text('ran')
    def stop_on_signal(signum, frame):
        delay = float(os.environ.get('SHUNT_TEST_FAKE_STOP_DELAY_SECONDS', '0'))

        def shutdown():
            time.sleep(delay)
            server.shutdown()

        threading.Thread(target=shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_on_signal)
    runtime_file = os.environ.get('SHUNT_TEST_FAKE_RUNTIME_FILE')
    if runtime_file:
        runtime = {
            'args': sys.argv[1:],
            'cwd': os.getcwd(),
            'xdg_config_home': os.environ.get('XDG_CONFIG_HOME'),
            'xdg_data_home': os.environ.get('XDG_DATA_HOME'),
            'xdg_cache_home': os.environ.get('XDG_CACHE_HOME'),
            'xdg_state_home': os.environ.get('XDG_STATE_HOME'),
            'config': json.loads(os.environ['OPENCODE_CONFIG_CONTENT']),
        }
        Path(runtime_file).write_text(json.dumps(runtime))
    url = f'http://127.0.0.1:{server.server_port}'
    Path(os.environ['SHUNT_TEST_FAKE_URL_FILE']).write_text(url)
    launch_file = os.environ.get('SHUNT_TEST_FAKE_LAUNCH_FILE')
    if launch_file:
        with open(launch_file, 'a') as launches:
            launches.write('started\n')
    print(url, flush=True)
    server.serve_forever()
