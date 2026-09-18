import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import install
from router import main
from settings import config_path, data_dir, load_settings, set_setting, validate


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jev install ')
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'SHELL': '/bin/zsh',
                             'XDG_CONFIG_HOME': str(self.home / '.config'),
                             'XDG_DATA_HOME': str(self.home / '.local/share'),
                             'ZDOTDIR': str(self.home)}, clear=False)
        self.env.start()
        self.binary = self.home / 'original-codex'
        self.binary.write_text('#!/bin/sh\nprintf "original:%s\\n" "$*"\n')
        self.binary.chmod(0o755)
        self.bindir = self.home / '.local/bin'

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([str(self.bindir / 'jev-codex'), *args], cwd=self.home,
                              text=True, capture_output=True, timeout=10)

    def test_install_update_commands_credentials_and_uninstall(self):
        rc = self.home / '.zshrc'
        rc.write_text('# existing shell settings\n')
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), 'test-key', wrap=True)
        self.assertEqual(self.run_cli('doctor', '--offline').returncode, 0)
        self.assertEqual(self.run_cli('config', 'set', 'reserve-percent', '15').returncode, 0)
        self.assertEqual(json.loads(self.run_cli('config', 'show').stdout)['reserve_percent'], 15)
        self.assertEqual(self.run_cli('config', 'set', 'reserve_percent', '-1').returncode, 1)
        self.assertEqual(self.run_cli('config', 'set', 'unknown', 'value').returncode, 1)
        credential = config_path().with_name('credentials.env')
        self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config_path().stat().st_mode & 0o777, 0o600)
        self.assertNotIn('test-key', self.run_cli('doctor', '--offline').stdout)
        wrapped = subprocess.run([str(self.bindir / 'codex'), 'app-server', '--help'], text=True, capture_output=True, timeout=10)
        self.assertEqual(wrapped.stdout.strip(), 'original:app-server --help')
        original = subprocess.run([str(self.bindir / 'codex-original'), '--version'], text=True, capture_output=True, timeout=10)
        self.assertEqual(original.stdout.strip(), 'original:--version')
        with patch.dict(os.environ, {'PATH': str(self.bindir) + os.pathsep + str(self.home)}):
            self.assertEqual(install.installed_codex(), str(self.binary))
            with self.assertRaises(RuntimeError):
                install.find_codex(str(self.bindir / 'codex'))
        # A repeated install keeps settings, one PATH block, and an enabled wrapper.
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), 'updated-key')
        self.assertEqual(load_settings()['reserve_percent'], 15)
        self.assertEqual(rc.read_text().count('# Begin Jev Codex PATH'), 1)
        shell = subprocess.run(['/bin/sh', '-c', f'. {shlex.quote(str(rc))}; command -v codex'],
                               text=True, capture_output=True, timeout=10)
        self.assertEqual(shell.stdout.strip(), str(self.bindir / 'codex'))
        with patch('sys.stdout', new_callable=io.StringIO):
            install.uninstall(purge=True)
        self.assertEqual(rc.read_text(), '# existing shell settings\n')
        self.assertFalse((self.bindir / 'codex').exists())
        self.assertFalse(data_dir().exists())
        self.assertFalse(credential.exists())
        self.assertTrue(self.binary.exists())

    def test_conflicts_and_modified_files_are_not_overwritten(self):
        self.bindir.mkdir(parents=True)
        command = self.bindir / 'jev-codex'
        command.write_text('user command')
        with self.assertRaises(RuntimeError):
            install.install(str(self.binary), 'key', change_shell=False)
        self.assertEqual(command.read_text(), 'user command')
        command.unlink()
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), 'key', change_shell=False)
        command.write_text('changed after installation')
        with self.assertRaises(RuntimeError):
            install.uninstall()
        self.assertEqual(command.read_text(), 'changed after installation')

    def test_failed_update_restores_previous_installation_and_shell_link(self):
        target = self.home / 'shell-config'
        target.write_text('# keep this\n')
        (self.home / '.zshrc').symlink_to(target)
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), 'old-key')
        self.assertTrue((self.home / '.zshrc').is_symlink())
        previous = config_path().with_name('credentials.env').read_text()
        original_write = install.atomic_write
        failed = False
        def fail_once(path, *args):
            nonlocal failed
            if Path(path).name == 'install.json' and not failed:
                failed = True
                raise OSError('Test write failure')
            return original_write(path, *args)
        with patch('install.atomic_write', side_effect=fail_once), self.assertRaises(OSError):
            install.install(str(self.binary), 'new-key')
        self.assertEqual(config_path().with_name('credentials.env').read_text(), previous)
        self.assertEqual(self.run_cli('doctor', '--offline').returncode, 0)
        with patch('sys.stdout', new_callable=io.StringIO):
            install.uninstall()
        self.assertEqual(target.read_text(), '# keep this\n')
        self.assertTrue((self.home / '.zshrc').is_symlink())
        self.assertTrue(config_path().exists())

    def test_settings_reload_between_turns_and_cli_override(self):
        for override in (False, True):
            set_setting('usage_policy', 'quality')
            server = Mock()
            server.models.return_value = [{'model': 'test', 'description': 'test',
                'supportedReasoningEfforts': [{'reasoningEffort': 'low', 'description': 'fast'}]}]
            server.call.return_value = {'thread': {'id': 'thread', 'turns': []}, 'config': {'developer_instructions': 'Keep this instruction.'}}
            calls = []
            def get_input(*unused):
                if not calls:
                    calls.append(1)
                    return 'first'
                if len(calls) == 1:
                    calls.append(2)
                    set_setting('usage_policy', 'conserve')
                    return 'second'
                return '/quit'
            args = ['router', '-C', str(self.home), '--state-dir', str(self.home / 'state')]
            if override:
                args += ['--usage-policy', 'quality']
            route = {'model': 'test', 'effort': 'low', 'confidence': 1}
            with patch('sys.argv', args), patch('sys.stdin.isatty', return_value=True), \
                 patch('builtins.input', side_effect=get_input), patch('router.AppServer', return_value=server), \
                 patch('router.installed_codex', return_value=str(self.binary)), patch('router.load_key', return_value='key'), \
                 patch('router.choose', side_effect=lambda *a: dict(route)), patch('router.show_budget'), \
                 patch('router.read_budget', side_effect=lambda s,m,*a,**kw: {'mode': m}) as budget, \
                 patch('router.run_turn', return_value={'status': 'completed', 'error': None, 'execution_seconds': 0}), \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(), 0)
            self.assertEqual([c.args[1] for c in budget.call_args_list], ['quality', 'quality' if override else 'conserve'])
            start = next(c for c in server.call.call_args_list if c.args[0] == 'thread/start')
            self.assertIn('Keep this instruction.', start.args[1]['developerInstructions'])
            self.assertIn('config', start.args[1]['developerInstructions'])
        for values in ({'show_usage': 'false'}, {'reserve_percent': True}, {'reserve_percent': float('nan')},
                       {'allow_model': []}, {'unknown': 1}, {'usage_policy': []}):
            with self.assertRaises(ValueError):
                validate(values)


if __name__ == '__main__':
    unittest.main()
