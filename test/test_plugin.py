import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name('plugin_driver.mjs')


class PluginScenarios(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.big = self.root / 'big.txt'
        self.big.write_text('\n'.join(str(n) for n in range(1, 401)) + '\n')
        self.small = self.root / 'small.txt'
        self.small.write_text('\n'.join(str(n) for n in range(1, 11)))
        self.shunt_root = REPO
        hook = REPO / 'hooks/check-file-size'
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        hook = REPO / 'hooks/check-bash-read'
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def tearDown(self):
        self._tmp.cleanup()

    def drive(self, tool, args, env_extra=None):
        env = os.environ.copy()
        env.pop('SHUNT_MIN_LINES', None)
        env['SHUNT_ROOT'] = str(self.shunt_root)
        env.update(env_extra or {})
        result = subprocess.run(
            ['bun', str(DRIVER), tool, json.dumps(args)],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_given_a_large_file_when_reading_it_fully_then_the_read_is_blocked_with_the_skill_hint(self):
        outcome = self.drive('read', {'filePath': str(self.big)})
        self.assertTrue(outcome['blocked'])
        self.assertIn('/bulk-reader skill', outcome['message'])
        self.assertIn('400 lines', outcome['message'])

    def test_given_a_large_file_when_reading_with_a_limit_then_the_read_is_allowed(self):
        outcome = self.drive('read', {'filePath': str(self.big), 'limit': 50})
        self.assertFalse(outcome['blocked'])

    def test_given_a_large_file_when_reading_with_an_offset_then_the_read_is_allowed(self):
        outcome = self.drive('read', {'filePath': str(self.big), 'offset': 10})
        self.assertFalse(outcome['blocked'])

    def test_given_a_small_file_when_reading_it_fully_then_the_read_is_allowed(self):
        outcome = self.drive('read', {'filePath': str(self.small)})
        self.assertFalse(outcome['blocked'])

    def test_given_a_high_min_lines_threshold_when_reading_a_medium_file_then_the_read_is_allowed(self):
        outcome = self.drive('read', {'filePath': str(self.big)}, env_extra={'SHUNT_MIN_LINES': '1000'})
        self.assertFalse(outcome['blocked'])

    def test_given_a_cat_of_a_large_file_when_running_bash_then_the_command_is_blocked(self):
        outcome = self.drive('bash', {'command': f'cat {self.big}'})
        self.assertTrue(outcome['blocked'])
        self.assertIn('/bulk-reader skill', outcome['message'])

    def test_given_an_argv_array_cat_of_a_large_file_when_running_bash_then_the_command_is_blocked(self):
        outcome = self.drive('bash', {'command': ['cat', str(self.big)]})
        self.assertTrue(outcome['blocked'])

    def test_given_a_piped_cat_when_running_bash_then_the_command_is_allowed(self):
        outcome = self.drive('bash', {'command': f'cat {self.big} | grep 7'})
        self.assertFalse(outcome['blocked'])

    def test_given_an_unrelated_command_when_running_bash_then_the_command_is_allowed(self):
        outcome = self.drive('bash', {'command': 'git status'})
        self.assertFalse(outcome['blocked'])

    def test_given_an_unrelated_tool_then_no_hook_runs_and_the_call_is_allowed(self):
        outcome = self.drive('grep', {'pattern': 'x', 'path': str(self.big)})
        self.assertFalse(outcome['blocked'])

    def test_given_a_missing_hook_installation_when_reading_then_the_call_fails_closed_with_direction(self):
        empty = self.root / 'broken'
        empty.mkdir()
        outcome = self.drive('read', {'filePath': str(self.big)}, env_extra={'SHUNT_ROOT': str(empty)})
        self.assertTrue(outcome['blocked'])
        self.assertIn('shunt hook', outcome['message'])

    def test_given_a_hook_that_fails_unexpectedly_when_reading_then_the_call_fails_closed(self):
        broken = self.root / 'broken-hook'
        (broken / 'hooks').mkdir(parents=True)
        hook = broken / 'hooks/check-file-size'
        hook.write_text('#!/bin/sh\necho boom >&2\nexit 1\n')
        hook.chmod(0o755)
        outcome = self.drive('read', {'filePath': str(self.big)}, env_extra={'SHUNT_ROOT': str(broken)})
        self.assertTrue(outcome['blocked'])
        self.assertIn('boom', outcome['message'])


if __name__ == '__main__':
    unittest.main()
