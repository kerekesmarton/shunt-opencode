import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / 'install.sh'


class InstallerScenarios(unittest.TestCase):
    def install(self, home):
        env = os.environ.copy()
        env['HOME'] = str(home)
        return subprocess.run(
            ['/bin/bash', str(INSTALL)],
            env=env, capture_output=True, text=True, timeout=30,
        )

    def test_given_a_clean_home_when_installing_then_the_runtime_is_laid_out_and_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            result = self.install(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            shunt = home / '.local/share/shunt-opencode'
            for relative in (
                'hooks/check-file-size',
                'hooks/check-bash-read',
                'scripts/bulk-read',
                'scripts/code-write',
                'scripts/lib/opencode.sh',
                'scripts/lib/worker.py',
                'plugin/index.ts',
            ):
                self.assertTrue((shunt / relative).is_file(), f'missing {relative}')
                if relative.startswith(('hooks/', 'scripts/')) and 'lib/' not in relative:
                    self.assertTrue(
                        os.access(shunt / relative, os.X_OK),
                        f'not executable: {relative}',
                    )
            self.assertTrue((shunt / 'scripts/lib/worker.py').is_file())

    def test_given_a_clean_home_when_installing_then_skills_reference_the_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            result = self.install(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            skills = home / '.config/opencode/skills'
            shunt = home / '.local/share/shunt-opencode'
            for skill in ('bulk-reader', 'code-writer', 'shunt-worker'):
                text = (skills / skill / 'SKILL.md').read_text()
                self.assertIn(str(shunt), text)
                self.assertNotIn('${SHUNT_ROOT}', text)

    def test_given_a_clean_home_when_installing_then_the_plugin_snippet_is_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            result = self.install(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            shunt = home / '.local/share/shunt-opencode'
            self.assertIn(str(shunt / 'plugin/index.ts'), result.stdout)
            self.assertIn('"plugin"', result.stdout)

    def test_given_an_existing_installation_when_installing_again_then_the_result_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            first = self.install(home)
            stale = home / '.local/share/shunt-opencode/scripts/stale.sh'
            stale.write_text('#!/bin/sh\n')
            stale.chmod(0o755)
            second = self.install(home)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertFalse(stale.exists())
            self.assertEqual(first.returncode, 0, first.stderr)


if __name__ == '__main__':
    unittest.main()
