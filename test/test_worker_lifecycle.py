import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request


WORKER = Path(os.environ.get(
    'SHUNT_TEST_WORKER_PATH',
    Path(__file__).resolve().parents[1] / 'scripts/lib/worker.py',
))


class HealthyServer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'healthy': True}).encode())

    def log_message(self, format, *args):
        pass


def long_lived_command():
    return [sys.executable, '-c', 'import time; time.sleep(30)']


def signature_of(pid):
    return subprocess.check_output(
        ['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='],
        text=True, stderr=subprocess.DEVNULL,
    ).strip()


class WorkerLifecycleScenarios(unittest.TestCase):
    def test_given_no_worker_when_ensuring_twice_then_the_same_worker_is_reused_and_can_be_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(root / 'state')
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)

            def cli(command):
                return subprocess.run(
                    [sys.executable, str(WORKER), command],
                    env=env, capture_output=True, text=True, timeout=10,
                )

            try:
                first = cli('ensure')
                self.assertEqual(first.returncode, 0, first.stderr)
                second = cli('ensure')
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(first.stdout, second.stdout)
                stopped = cli('stop')
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_concurrent_ensures_when_no_worker_exists_then_only_one_worker_is_started(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            launches = root / 'launches'
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(root / 'state')
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)
            env['SHUNT_TEST_FAKE_LAUNCH_FILE'] = str(launches)
            commands = [
                subprocess.Popen(
                    [sys.executable, str(WORKER), 'ensure'],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                for _ in range(2)
            ]
            try:
                results = [command.communicate(timeout=10) for command in commands]
                self.assertTrue(all(command.returncode == 0 for command in commands), results)
                self.assertEqual(len({stdout for stdout, _ in results}), 1)
                self.assertEqual(launches.read_text().splitlines(), ['started'])
                stopped = subprocess.run(
                    [sys.executable, str(WORKER), 'stop'],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_a_managed_worker_that_delays_shutdown_when_stopping_then_wait_for_exit_before_removing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            state_dir = root / 'state'
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(state_dir)
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)
            env['SHUNT_TEST_FAKE_STOP_DELAY_SECONDS'] = '0.3'

            def cli(command):
                return subprocess.run(
                    [sys.executable, str(WORKER), command],
                    env=env, capture_output=True, text=True, timeout=10,
                )

            try:
                ensured = cli('ensure')
                self.assertEqual(ensured.returncode, 0, ensured.stderr)
                started = time.monotonic()
                stopped = cli('stop')
                elapsed = time.monotonic() - started
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertGreaterEqual(elapsed, 0.3)
                self.assertFalse((state_dir / 'worker.json').exists())
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_a_verified_unhealthy_managed_worker_when_ensuring_then_restart_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            launches = root / 'launches'
            unhealthy = root / 'unhealthy'
            state_dir = root / 'state'
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(state_dir)
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)
            env['SHUNT_TEST_FAKE_LAUNCH_FILE'] = str(launches)
            env['SHUNT_TEST_FAKE_UNHEALTHY_PID_FILE'] = str(unhealthy)

            def cli(command):
                return subprocess.run(
                    [sys.executable, str(WORKER), command],
                    env=env, capture_output=True, text=True, timeout=10,
                )

            try:
                first = cli('ensure')
                self.assertEqual(first.returncode, 0, first.stderr)
                first_state = json.loads((state_dir / 'worker.json').read_text())
                unhealthy.write_text(str(first_state['pid']))
                restarted = cli('ensure')
                self.assertEqual(restarted.returncode, 0, restarted.stderr)
                restarted_state = json.loads((state_dir / 'worker.json').read_text())
                self.assertNotEqual(restarted_state['pid'], first_state['pid'])
                self.assertNotEqual(restarted.stdout, first.stdout)
                self.assertEqual(launches.read_text().splitlines(), ['started', 'started'])
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_a_worker_that_never_becomes_ready_when_ensuring_then_fail_and_stop_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(root / 'state')
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)
            env['SHUNT_TEST_FAKE_NOT_READY'] = '1'
            env['SHUNT_CATALOG_TIMEOUT_SECONDS'] = '2'
            try:
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'ensure'],
                    env=env, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr.strip(), 'shunt: OpenCode worker did not become ready in time; inspect ' + str(root / 'state/worker.log'))
                self.assertFalse((root / 'state/worker.json').exists())
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_a_global_provider_config_when_starting_then_the_worker_inherits_providers_but_not_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            runtime_file = root / 'runtime.json'
            state_dir = root / 'state'
            home = root / 'home'
            config_dir = home / '.config/opencode'
            config_dir.mkdir(parents=True)
            (config_dir / 'opencode.json').write_text(json.dumps({
                'plugin': ['should-not-inherit'],
                'provider': {'openrouter': {'options': {'apiKey': '{env:OPENROUTER_API_KEY}'}}},
                'model': 'openrouter/google/gemini-3.1-flash-lite',
                'disabled_providers': ['open-router'],
            }))
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(state_dir)
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)
            env['SHUNT_TEST_FAKE_RUNTIME_FILE'] = str(runtime_file)
            env['HOME'] = str(home)
            try:
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'ensure'],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                runtime = json.loads(runtime_file.read_text())
                config = runtime['config']
                self.assertNotIn('plugin', config)
                self.assertNotIn('skills', config)
                self.assertEqual(config['provider'], {'openrouter': {'options': {'apiKey': '{env:OPENROUTER_API_KEY}'}}})
                self.assertEqual(config['model'], 'openrouter/google/gemini-3.1-flash-lite')
                self.assertEqual(config['disabled_providers'], ['open-router'])
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_a_managed_worker_when_starting_then_isolate_its_runtime_and_deny_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            runtime_file = root / 'runtime.json'
            state_dir = root / 'state'
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(state_dir)
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)
            env['SHUNT_TEST_FAKE_RUNTIME_FILE'] = str(runtime_file)
            try:
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'ensure'],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                runtime = json.loads(runtime_file.read_text())
                self.assertEqual(runtime['cwd'], str((state_dir / 'workspace').resolve()))
                self.assertEqual(runtime['args'], ['serve', '--hostname', '127.0.0.1', '--port', '0'])
                self.assertEqual(runtime['xdg_config_home'], str(state_dir / 'config'))
                self.assertEqual(runtime['xdg_data_home'], str(state_dir / 'data'))
                self.assertEqual(runtime['xdg_cache_home'], str(state_dir / 'cache'))
                self.assertEqual(runtime['xdg_state_home'], str(state_dir / 'runtime'))
                self.assertNotIn('plugin', runtime['config'])
                self.assertNotIn('skills', runtime['config'])
                self.assertEqual(runtime['config']['permission']['*'], 'deny')
                self.assertEqual(runtime['config']['agent']['shunt-worker']['permission']['*'], 'deny')
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_unparseable_worker_state_when_stopping_then_preserve_it_for_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            state_file = state_dir / 'worker.json'
            state_file.write_text('null')
            env = os.environ.copy()
            env['SHUNT_STATE_DIR'] = str(state_dir)
            result = subprocess.run(
                [sys.executable, str(WORKER), 'stop'],
                env=env, capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(state_file.read_text(), 'null')
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr.strip()),
            (1, '', 'shunt: Managed worker state is invalid; inspect and remove it explicitly'),
        )

    def test_given_a_managed_worker_without_provider_auth_when_invoking_then_fail_with_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'opencode'
            shutil.copyfile(Path(__file__).with_name('fake_opencode.py'), fake)
            fake.chmod(0o700)
            address_file = root / 'address'
            ran_marker = root / 'ran'
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(root / 'state')
            env['SHUNT_OPENCODE_BIN'] = str(fake)
            env['SHUNT_TEST_FAKE_URL_FILE'] = str(address_file)
            env['SHUNT_OPENCODE_MODEL'] = 'test-provider/test-model'
            env['SHUNT_TEST_FAKE_RUN_MARKER'] = str(ran_marker)
            env['HOME'] = str(root / 'empty-home')
            env.pop('OPENCODE_CONFIG_DIR', None)
            env.pop('OPENCODE_CONFIG', None)
            (root / 'empty-home').mkdir()

            def cli(*args):
                return subprocess.run(
                    [sys.executable, str(WORKER), *args],
                    env=env, capture_output=True, text=True, timeout=10,
                )

            try:
                ensured = cli('ensure')
                self.assertEqual(ensured.returncode, 0, ensured.stderr)
                message = root / 'msg.txt'
                message.write_text('payload\n')
                invoked = cli('invoke', 'bulk-reader', str(message))
                self.assertEqual(
                    (invoked.returncode, invoked.stdout, invoked.stderr.strip()),
                    (1, '', 'shunt: No provider credentials found for the managed worker. Run `opencode providers login` first.'),
                )
                self.assertFalse(ran_marker.exists(), 'worker run must not launch without provider auth')
            finally:
                if address_file.exists():
                    request = urllib.request.Request(address_file.read_text() + '/shutdown', method='POST')
                    try:
                        with urllib.request.urlopen(request, timeout=2):
                            pass
                    except (OSError, urllib.error.URLError):
                        pass

    def test_given_a_foreign_live_process_in_worker_state_when_stopping_then_the_process_is_never_signaled(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            decoy = subprocess.Popen(long_lived_command())
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HealthyServer)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                state = {
                    'pid': decoy.pid,
                    'signature': 'not the decoy signature',
                    'url': f'http://127.0.0.1:{server.server_port}',
                    'password': 'test-password',
                    'auth': 'linked',
                    'workspace': str(state_dir / 'workspace'),
                }
                (state_dir / 'worker.json').write_text(json.dumps(state))
                env = os.environ.copy()
                env['SHUNT_STATE_DIR'] = str(state_dir)
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'stop'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
                self.assertEqual(decoy.poll(), None)
                self.assertFalse((state_dir / 'worker.json').exists())
            finally:
                decoy.terminate()
                decoy.wait(timeout=5)
                server.shutdown()
                server.server_close()
                thread.join()


    def test_given_an_invalid_pid_in_worker_state_when_stopping_then_preserve_it_without_signaling(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            decoy = subprocess.Popen(long_lived_command())
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HealthyServer)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                state = {
                    'pid': str(decoy.pid),
                    'signature': signature_of(decoy.pid),
                    'url': f'http://127.0.0.1:{server.server_port}',
                    'password': 'test-password',
                    'auth': 'linked',
                    'workspace': str(state_dir / 'workspace'),
                }
                (state_dir / 'worker.json').write_text(json.dumps(state))
                env = os.environ.copy()
                env['SHUNT_STATE_DIR'] = str(state_dir)
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'stop'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr.strip()),
                    (1, '', 'shunt: Managed worker state is invalid; inspect and remove it explicitly'),
                )
                self.assertEqual(decoy.poll(), None)
                self.assertTrue((state_dir / 'worker.json').exists())
            finally:
                decoy.terminate()
                decoy.wait(timeout=5)
                server.shutdown()
                server.server_close()
                thread.join()

    def test_given_an_empty_password_in_worker_state_when_stopping_then_preserve_it_without_signaling(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            decoy = subprocess.Popen(long_lived_command())
            try:
                state_file = state_dir / 'worker.json'
                state = {
                    'pid': decoy.pid,
                    'signature': signature_of(decoy.pid),
                    'url': 'http://127.0.0.1:4096',
                    'password': '',
                    'workspace': str(state_dir / 'workspace'),
                }
                state_file.write_text(json.dumps(state))
                env = os.environ.copy()
                env['SHUNT_STATE_DIR'] = str(state_dir)
                result = subprocess.run(
                    [sys.executable, str(WORKER), 'stop'],
                    env=env, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr.strip()),
                    (1, '', 'shunt: Managed worker state is invalid; inspect and remove it explicitly'),
                )
                self.assertEqual(decoy.poll(), None)
                self.assertTrue(state_file.exists())
            finally:
                decoy.terminate()
                decoy.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
