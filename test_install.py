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
import cli
from cli import first_argument
from native import enable_auto, key_action, run as native_run
from router import main
from jev_client import JevError
from settings import (config_path, data_dir, load_route_pin, load_settings, save_route_pin,
                      set_setting, state_dir, validate)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jev install ')
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'SHELL': '/bin/zsh',
                             'XDG_CONFIG_HOME': str(self.home / '.config'),
                             'XDG_DATA_HOME': str(self.home / '.local/share'),
                             'XDG_STATE_HOME': str(self.home / '.local/state'),
                             'ZDOTDIR': str(self.home)}, clear=False)
        self.env.start()
        self.real_install_vendor = install.install_vendor
        self.vendor = patch('install.install_vendor', side_effect=lambda path: path.mkdir())
        self.vendor.start()
        self.binary = self.home / 'original-codex'
        self.binary.write_text('#!/bin/sh\nprintf "original:%s\\n" "$*"\n')
        self.binary.chmod(0o755)
        self.bindir = self.home / '.local/bin'

    def tearDown(self):
        self.vendor.stop()
        self.env.stop()
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([str(self.bindir / 'jev-codex'), *args], cwd=self.home,
                              text=True, capture_output=True, timeout=10)

    def test_install_update_commands_credentials_and_uninstall(self):
        rc = self.home / '.zshrc'
        rc.write_text('# existing shell settings\n')
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), 'test-key')
        self.assertEqual(self.run_cli('doctor', '--offline').returncode, 0)
        if sys.platform == 'darwin':
            self.assertTrue((data_dir() / 'current/Jev Codex Settings.app/Contents/MacOS/JevCodexSettings').is_file())
        self.assertEqual(self.run_cli('config', 'set', 'reserve-percent', '15').returncode, 0)
        self.assertEqual(json.loads(self.run_cli('config', 'show').stdout)['reserve_percent'], 15)
        self.assertEqual(self.run_cli('config', 'set', 'reserve_percent', '-1').returncode, 1)
        self.assertEqual(self.run_cli('config', 'set', 'unknown', 'value').returncode, 1)
        credential = config_path().with_name('credentials.env')
        catalog = config_path().with_name('catalog.json')
        catalog.write_text('{}')
        self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config_path().stat().st_mode & 0o777, 0o600)
        self.assertNotIn('test-key', self.run_cli('doctor', '--offline').stdout)
        wrapped = subprocess.run([str(self.bindir / 'codex'), 'app-server', '--help'], text=True, capture_output=True, timeout=10)
        self.assertEqual(wrapped.stdout.strip(), 'original:app-server --help')
        self.assertEqual(first_argument(['-C', '/tmp', 'exec', 'echo']), 'exec')
        self.assertEqual(first_argument(['-C', '/tmp', 'resume', 'thread-id']), 'resume')
        wrapped = subprocess.run([str(self.bindir / 'codex'), '-C', str(self.home), 'exec', '--help'],
                                 text=True, capture_output=True, timeout=10)
        self.assertEqual(wrapped.stdout.strip(), f'original:-C {self.home} exec --help')
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
            install.install(str(self.binary), 'updated-key', wrap=False)
        self.assertFalse((self.bindir / 'codex').exists())
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), 'updated-key', wrap=True)
        self.assertTrue((self.bindir / 'codex').exists())
        with patch('sys.stdout', new_callable=io.StringIO):
            install.uninstall(purge=True)
        self.assertEqual(rc.read_text(), '# existing shell settings\n')
        self.assertFalse((self.bindir / 'codex').exists())
        self.assertFalse(data_dir().exists())
        self.assertFalse(credential.exists())
        self.assertFalse(catalog.exists())
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

    def test_key_can_be_deferred_and_added_interactively(self):
        credential = config_path().with_name('credentials.env')
        with patch('sys.stdin.isatty', return_value=False):
            self.assertIsNone(install.prompt_key(required=False))
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), None, change_shell=False)
        self.assertFalse(credential.exists())
        with patch('sys.stdin.isatty', return_value=True), \
             patch('getpass.getpass', return_value='new-key'), \
             patch('install.verify_jev') as check, \
             patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(install.prompt_key(), 'new-key')
        check.assert_called_once_with('new-key')
        self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
        self.assertEqual(install.prompt_key(), 'new-key')

    def test_first_run_key_choices(self):
        with patch('native.load_key', side_effect=JevError('missing')), \
             patch('builtins.input', return_value=''):
            self.assertEqual(key_action(), 'original')
        with patch('native.load_key', side_effect=FileNotFoundError), \
             patch('builtins.input', return_value='u'):
            self.assertEqual(key_action(), 'uninstall')
        with patch('native.load_key', side_effect=JevError('missing')), \
             patch('builtins.input', return_value='a'), \
             patch('native.prompt_key', return_value='key') as prompt:
            self.assertEqual(key_action(), 'route')
        prompt.assert_called_once_with(force=True, required=False)
        with patch('native.load_key', side_effect=JevError('missing')), \
             patch('builtins.input', side_effect=['wrong', '']), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(key_action(), 'original')
        self.assertIn('Enter a, u, or press Enter.', output.getvalue())
        with patch('native.load_key', side_effect=JevError('missing')), \
             patch('builtins.input', return_value='a'), \
             patch('native.prompt_key', return_value=None):
            self.assertEqual(key_action(), 'original')
        for action in ('original', 'uninstall'):
            with patch('native.installed_codex', return_value=str(self.binary)), \
                 patch('native.key_action', return_value=action), \
                 patch('native.uninstall') as remove, \
                 patch('native.subprocess.call', return_value=0) as call, \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(native_run(['--help']), 0)
            call.assert_called_once_with([str(self.binary), '--help'])
            self.assertEqual(remove.call_count, action == 'uninstall')
            if action == 'uninstall':
                remove.assert_called_once_with(purge=True)

    def test_auth_cancellation_is_clean(self):
        with patch('sys.argv', ['jev-codex', 'auth', 'login']), \
             patch('cli.prompt_key', side_effect=KeyboardInterrupt), \
             patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(cli.main(), 130)
        self.assertEqual(error.getvalue(), '\nCancelled.\n')

    def test_installer_entry_point_matrix(self):
        key_file = self.home / 'provided.env'
        key_file.write_text('JEV_API_KEY=file-key\n')
        cases = [
            (['install.py', '--codex-path', str(self.binary)], None, None, True),
            (['install.py', '--codex-path', str(self.binary), '--no-wrap-codex', '--no-shell'], None, False, False),
            (['install.py', '--codex-path', str(self.binary), '--wrap-codex'], None, True, True),
            (['install.py', '--codex-path', str(self.binary), '--env-file', str(key_file)], 'file-key', None, True),
        ]
        for argv, expected_key, wrap, shell in cases:
            with self.subTest(argv=argv), patch('sys.argv', argv), \
                 patch('install.prompt_key', return_value=expected_key) as prompt, \
                 patch('install.verify') as check, patch('install.install') as apply, \
                 patch.dict(os.environ, {'JEV_API_KEY': 'environment-key'}), \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(install.main(), 0)
            check.assert_called_once_with(str(self.binary), expected_key, check_jev=expected_key is not None)
            apply.assert_called_once_with(str(self.binary), expected_key, wrap, shell)
            self.assertEqual(prompt.call_count, '--env-file' not in argv)

        def partial_failure(*_args, **_kwargs):
            root = data_dir()
            root.mkdir(parents=True)
            (root / '.managed').write_text('jev-codex\n')
            raise RuntimeError('failed install')

        with patch('sys.argv', ['install.py', '--codex-path', str(self.binary)]), \
             patch('install.prompt_key', return_value=None), patch('install.verify'), \
             patch('install.install', side_effect=partial_failure), \
             patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(install.main(), 1)
        self.assertFalse(data_dir().exists())
        self.assertNotIn('environment-key', error.getvalue())

    def test_shell_and_output_matrix(self):
        with patch.dict(os.environ, {'SHELL': '/bin/bash'}):
            profile = self.home / '.bash_profile'
            profile.write_text('# login\n')
            self.assertEqual(install.shell_files(), [self.home / '.bashrc', profile])
        with patch.dict(os.environ, {'SHELL': '/bin/fish'}), self.assertRaisesRegex(RuntimeError, '--no-shell'):
            install.shell_files()
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            install.install(str(self.binary), None, change_shell=False)
        self.assertIn(str(self.bindir / 'codex'), output.getvalue())
        self.assertNotIn('Existing settings were kept', output.getvalue())
        with patch('sys.stdout', new_callable=io.StringIO):
            install.uninstall(purge=True)

    def test_connection_verification_matrix(self):
        server = Mock()
        with patch('install.AppServer', return_value=server), patch('install.read_budget', return_value={}), \
             patch('install.show_budget'), patch('install.verify_jev') as check, \
             patch('sys.stdout', new_callable=io.StringIO):
            server.call.return_value = {'account': None}
            with self.assertRaisesRegex(RuntimeError, 'not signed in'):
                install.verify(str(self.binary), None, check_jev=False)
            server.call.return_value = {'account': {'email': 'user@example.com'}}
            server.models.return_value = []
            with self.assertRaisesRegex(RuntimeError, 'no available models'):
                install.verify(str(self.binary), None, check_jev=False)
            server.models.return_value = [{'model': 'test'}]
            install.verify(str(self.binary), 'key')
        self.assertEqual(server.close.call_count, 3)
        check.assert_called_once_with('key')

        with patch('install.ask', return_value={'answers': {'check': {
                'type': 'choice', 'choice': 'ready', 'probabilities': {'ready': 1}, 'confidence': 1}}}), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            install.verify_jev('private-key')
        self.assertNotIn('private-key', output.getvalue())

    def test_install_rejects_unsafe_and_changed_paths(self):
        root = data_dir()
        root.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, 'unmanaged directory'):
            install.install(str(self.binary), 'key', change_shell=False)
        self.assertFalse((root / '.managed').exists())
        root.rmdir()

        self.bindir.mkdir(parents=True)
        local_codex = self.bindir / 'codex'
        local_codex.write_text('#!/bin/sh\n')
        local_codex.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, 'where the Jev wrapper must be installed'):
            install.install(str(local_codex), 'key', change_shell=False)
        local_codex.unlink()

        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), 'key', change_shell=False)
        local_codex.write_text('changed wrapper')
        with self.assertRaisesRegex(RuntimeError, 'changed command'):
            install.install(str(self.binary), 'key', wrap=False, change_shell=False)

    def test_fresh_write_failure_removes_written_commands(self):
        original_write = install.atomic_write
        def fail_manifest(path, *args):
            if Path(path).name == 'install.json':
                raise OSError('write failed')
            return original_write(path, *args)
        with patch('install.atomic_write', side_effect=fail_manifest), self.assertRaises(OSError):
            install.install(str(self.binary), 'private-key', change_shell=False)
        for command in ('codex', 'codex-original', 'jev-codex'):
            self.assertFalse((self.bindir / command).exists())
        self.assertFalse((data_dir() / 'current').exists())

    def test_required_key_and_installer_cancellation(self):
        with patch('sys.stdin.isatty', return_value=False), self.assertRaisesRegex(RuntimeError, 'auth login'):
            install.prompt_key()
        with patch('sys.stdin.isatty', return_value=True), patch('getpass.getpass', return_value=''), \
             patch('sys.stdout', new_callable=io.StringIO), self.assertRaisesRegex(RuntimeError, 'required'):
            install.prompt_key()
        with patch('sys.argv', ['install.py', '--codex-path', str(self.binary)]), \
             patch('install.prompt_key', side_effect=KeyboardInterrupt), \
             patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(install.main(), 130)
        self.assertEqual(error.getvalue(), 'Setup cancelled.\n')

    def test_packaging_failure_messages(self):
        success = Mock(returncode=0, stderr='')
        with patch('install.subprocess.run', return_value=success) as run:
            self.real_install_vendor(self.home / 'vendor')
        self.assertIn('websockets==16.1.1', run.call_args.args[0])
        failure = Mock(returncode=1, stderr='package install failed')
        with patch('install.subprocess.run', return_value=failure), \
             self.assertRaisesRegex(RuntimeError, 'package install failed'):
            self.real_install_vendor(self.home / 'vendor')

        app_root = self.home / 'app-release'
        (app_root / 'menu_bar.swift').parent.mkdir(parents=True)
        (app_root / 'menu_bar.swift').write_text('')
        compiler = Mock(returncode=1, stderr='compiler failed')
        with patch('install.sys.platform', 'darwin'), patch('install.subprocess.run', return_value=compiler), \
             self.assertRaisesRegex(RuntimeError, 'compiler failed'):
            install.build_settings_app(app_root)
        with patch('install.sys.platform', 'linux'):
            self.assertIsNone(install.build_settings_app(app_root))

    def test_shell_block_and_uninstall_failures(self):
        with self.assertRaisesRegex(RuntimeError, 'No managed installation'):
            install.uninstall()
        rc = self.home / '.zshrc'
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), None)
        self.assertTrue(rc.exists())
        rc.write_text(rc.read_text().replace('export PATH=', 'export CHANGED_PATH='))
        with self.assertRaisesRegex(RuntimeError, 'PATH block was changed'):
            install.uninstall()

    def test_created_shell_file_is_removed_on_uninstall(self):
        rc = self.home / '.zshrc'
        with patch('sys.stdout', new_callable=io.StringIO):
            install.install(str(self.binary), None)
            install.uninstall(purge=True)
        self.assertFalse(rc.exists())

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
        self.assertEqual(validate({'economy': 'medium'})['routing_preference'], 'balanced')
        for preference in ('lowest_usage', 'lower_usage', 'balanced', 'higher_quality', 'highest_quality'):
            self.assertEqual(validate({'routing_preference': preference})['routing_preference'], preference)
        for mode in ('thread', 'turn'):
            self.assertEqual(validate({'routing_mode': mode})['routing_mode'], mode)
        for effort in ('automatic', 'high', 'xhigh', 'max'):
            self.assertEqual(validate({'maximum_effort': effort})['maximum_effort'], effort)
        self.assertEqual(validate({'allow_effort': ['low', 'max']})['allow_effort'], ['low', 'max'])
        for values in ({'show_usage': 'false'}, {'reserve_percent': True}, {'reserve_percent': float('nan')},
                       {'allow_model': []}, {'unknown': 1}, {'usage_policy': []},
                       {'economy': 'maximum'}, {'routing_preference': 'maximum'},
                       {'routing_mode': 'message'},
                       {'maximum_effort': 'ultra'}, {'allow_effort': []},
                       {'allow_effort': ['high', 'high']}, {'allow_effort': ['unknown']}):
            with self.assertRaises(ValueError):
                validate(values)

    def test_route_pin_can_be_reset_for_current_thread_ids(self):
        save_route_pin(state_dir(), 'thr_123-test', 'gpt-5.6-sol', 'high', 'manual')
        self.assertEqual(load_route_pin(state_dir(), 'thr_123-test')['source'], 'manual')
        enable_auto('thr_123-test')
        self.assertIsNone(load_route_pin(state_dir(), 'thr_123-test'))
        with self.assertRaises(ValueError):
            enable_auto('../thread')


if __name__ == '__main__':
    unittest.main()
