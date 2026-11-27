import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request


WORKER = Path(__file__).resolve().parents[1] / 'scripts/lib/worker.py'
INSTALL = Path(__file__).resolve().parents[1] / 'install.sh'

INSTALLED_PLUGIN_DRIVER = '''#!/usr/bin/env bun
import { argv, env } from 'process'

const pluginModule = await import(env.SHUNT_PLUGIN_PATH)
const plugin = pluginModule.default ?? pluginModule.ShuntPlugin
const hooks = await plugin({})
try {
  await hooks['tool.execute.before'](
    { tool: argv[2], sessionID: 'smoke', callID: 'smoke' },
    { args: JSON.parse(argv[3]) },
  )
  console.log(JSON.stringify({ blocked: false }))
} catch (error) {
  console.log(JSON.stringify({ blocked: true, message: error.message }))
}
'''


@unittest.skipUnless(
    os.environ.get('SHUNT_RUN_OPENCODE_SMOKE') == '1',
    'set SHUNT_RUN_OPENCODE_SMOKE=1 to start the local OpenCode binary',
)
class OpenCodeWorkerSmokeScenarios(unittest.TestCase):
    def test_given_the_local_opencode_binary_when_managing_a_worker_then_reuse_authenticated_server_and_stop_it(self):
        binary = shutil.which('opencode')
        self.assertIsNotNone(binary, 'opencode must be on PATH')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(root / 'state')
            env['SHUNT_OPENCODE_BIN'] = binary

            def cli(command):
                return subprocess.run(
                    [sys.executable, str(WORKER), command],
                    env=env, capture_output=True, text=True, timeout=40,
                )

            try:
                first = cli('ensure')
                self.assertEqual(first.returncode, 0, first.stderr)
                state = json.loads((root / 'state/worker.json').read_text())
                self.assertEqual(first.stdout, state['url'] + '\n')
                unauthorized = urllib.request.Request(state['url'] + '/global/health')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(unauthorized, timeout=2)
                self.assertEqual(error.exception.code, 401)
                error.exception.close()
                credentials = base64.b64encode(f"opencode:{state['password']}".encode()).decode()
                authenticated = urllib.request.Request(
                    state['url'] + '/global/health',
                    headers={'Authorization': 'Basic ' + credentials},
                )
                with urllib.request.urlopen(authenticated, timeout=2) as response:
                    self.assertEqual(json.load(response).get('healthy'), True)
                second = cli('ensure')
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(second.stdout, first.stdout)
            finally:
                stopped = cli('stop')
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertFalse((root / 'state/worker.json').exists())

    def test_given_an_installed_shunt_when_running_end_to_end_then_isolation_plugin_and_transport_work(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            install = subprocess.run(
                ['/bin/bash', str(INSTALL)],
                env={**os.environ, 'HOME': str(home)},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            shunt = home / '.local/share/shunt-opencode'
            plugin_path = shunt / 'plugin/index.ts'
            big = home / 'big.txt'
            big.write_text('\n'.join(str(n) for n in range(1, 401)) + '\n')
            driver = home / 'driver.mjs'
            driver.write_text(INSTALLED_PLUGIN_DRIVER)

            blocked = subprocess.run(
                ['bun', str(driver), 'read', json.dumps({'filePath': str(big)})],
                env={**os.environ, 'SHUNT_PLUGIN_PATH': str(plugin_path)},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(blocked.returncode, 0, blocked.stderr)
            self.assertEqual(json.loads(blocked.stdout.strip().splitlines()[-1])['blocked'], True)

            env = os.environ.copy()
            env.pop('SHUNT_OPENCODE_SERVER', None)
            env['SHUNT_STATE_DIR'] = str(home / 'state')
            env['SHUNT_OPENCODE_BIN'] = shutil.which('opencode')
            try:
                ensured = subprocess.run(
                    [sys.executable, str(shunt / 'scripts/lib/worker.py'), 'ensure'],
                    env=env, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(ensured.returncode, 0, ensured.stderr)
                state = json.loads((home / 'state/worker.json').read_text())
                credentials = base64.b64encode(f"opencode:{state['password']}".encode()).decode()
                headers = {'Authorization': 'Basic ' + credentials}
                with urllib.request.urlopen(
                    urllib.request.Request(state['url'] + '/config', headers=headers), timeout=5,
                ) as response:
                    config = json.load(response)
                self.assertEqual(config.get('permission', {}).get('*'), 'deny')
                company_dir = os.environ.get('OPENCODE_CONFIG_DIR')
                if company_dir:
                    company_prefix = 'file://' + company_dir.rstrip('/') + '/'
                    for spec in config.get('plugin', []):
                        self.assertTrue(str(spec).startswith(company_prefix), f'unexpected plugin: {spec}')
                skills = config.get('skills', {})
                company_roots = [root for root in (
                    company_dir,
                    str(Path.home() / '.local/share/ken'),
                ) if root]
                for path in (skills.get('paths') or []) if isinstance(skills, dict) else []:
                    self.assertTrue(
                        any(path.startswith(root) for root in company_roots),
                        f'unexpected skill path: {path}',
                    )
                with urllib.request.urlopen(
                    urllib.request.Request(state['url'] + '/agent', headers=headers), timeout=5,
                ) as response:
                    agents = json.load(response)
                names = [agent.get('name') for agent in (agents if isinstance(agents, list) else [])]
                self.assertIn('shunt-worker', names)

                message = home / 'msg.txt'
                message.write_text('smoke payload\n')
                invoke_env = {**env, 'SHUNT_OPENCODE_MODEL': 'nonexistent/nope', 'SHUNT_TIMEOUT_SECONDS': '60'}
                invoke = subprocess.run(
                    [sys.executable, str(shunt / 'scripts/lib/worker.py'), 'invoke', 'bulk-reader', str(message)],
                    env=invoke_env, capture_output=True, text=True, timeout=90,
                )
                self.assertEqual(invoke.returncode, 1)
                self.assertEqual(invoke.stdout, '')
                self.assertIn('shunt:', invoke.stderr)
            finally:
                stopped = subprocess.run(
                    [sys.executable, str(shunt / 'scripts/lib/worker.py'), 'stop'],
                    env=env, capture_output=True, text=True, timeout=60,
                )
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertFalse((home / 'state/worker.json').exists())

    def test_given_the_default_port_is_occupied_when_starting_then_discover_the_fallback_listener(self):
        binary = shutil.which('opencode')
        self.assertIsNotNone(binary, 'opencode must be on PATH')
        with socket.socket() as listener:
            try:
                listener.bind(('127.0.0.1', 4096))
            except OSError as error:
                self.skipTest(f'could not reserve 127.0.0.1:4096: {error}')
            listener.listen()
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = os.environ.copy()
                env.pop('SHUNT_OPENCODE_SERVER', None)
                env['SHUNT_STATE_DIR'] = str(root / 'state')
                env['SHUNT_OPENCODE_BIN'] = binary
                try:
                    result = subprocess.run(
                        [sys.executable, str(WORKER), 'ensure'],
                        env=env, capture_output=True, text=True, timeout=40,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    state = json.loads((root / 'state/worker.json').read_text())
                    self.assertNotEqual(urllib.parse.urlsplit(state['url']).port, 4096)
                finally:
                    stopped = subprocess.run(
                        [sys.executable, str(WORKER), 'stop'],
                        env=env, capture_output=True, text=True, timeout=40,
                    )
                self.assertEqual(stopped.returncode, 0, stopped.stderr)


if __name__ == '__main__':
    unittest.main()
