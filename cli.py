"""Command entry point for routing, settings, and installation checks."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from install import installed_codex, prompt_key, uninstall, verify
from jev_client import load_key
from settings import config_path, default_env_file, load_settings, private_write, set_setting

NATIVE_COMMANDS = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'mcp-server',
                   'app-server', 'completion', 'sandbox', 'debug', 'apply', 'a',
                   'cloud', 'features', '--version', '-V', '--help', '-h',
                   'agents', 'plugin', 'remote-control', 'app', 'update', 'doctor',
                   'queue', 'archive', 'delete', 'unarchive', 'migrate-rollouts', 'help'}
VALUE_OPTIONS = {'-c', '--config', '--remote', '--remote-auth-token-env', '-i', '--image',
                 '-m', '--model', '--local-provider', '-p', '--profile', '-s', '--sandbox',
                 '-C', '--cd', '--add-dir', '-a', '--ask-for-approval'}


def first_argument(args):
    index = 0
    while index < len(args):
        value = args[index]
        if value == '--':
            return None
        if value in VALUE_OPTIONS:
            index += 2
        elif value.startswith('-'):
            index += 1
        else:
            return value
    return None


def main():
    try:
        args = sys.argv[1:]
        if args[:1] == ['--codex-wrapper']:
            args = args[1:]
            command = first_argument(args)
            if command in NATIVE_COMMANDS or any(option in args for option in (
                '-m', '--model', '--remote', '--help', '-h', '--version', '-V')):
                args = ['--original', *args]
            else:
                from native import run
                return run(args)
        if args[:1] == ['--original']:
            binary = installed_codex()
            os.execv(binary, [binary, *args[1:]])
        if args[:1] == ['config']:
            parser = argparse.ArgumentParser(prog='jev-codex config', description='Change saved settings; open sessions reload them before the next turn.')
            parser.add_argument('--file', type=Path, default=config_path(), help='Settings file to read or change')
            sub = parser.add_subparsers(dest='action', required=True)
            sub.add_parser('show')
            sub.add_parser('path')
            edit = sub.add_parser('set')
            edit.add_argument('name')
            edit.add_argument('value', help='A JSON value, or a plain string')
            options = parser.parse_args(args[1:])
            if options.action == 'path':
                print(options.file)
            elif options.action == 'show':
                print(json.dumps(load_settings(options.file), indent=2))
            else:
                try:
                    value = json.loads(options.value)
                except ValueError:
                    value = options.value
                set_setting(options.name.replace('-', '_'), value, options.file)
                print('Saved. Applies before the next turn unless a command-line option overrides it.')
            return 0
        if args[:1] == ['doctor']:
            parser = argparse.ArgumentParser(prog='jev-codex doctor')
            parser.add_argument('--offline', action='store_true', help='Check local setup without network requests')
            options = parser.parse_args(args[1:])
            load_settings()
            binary = installed_codex()
            key = load_key(default_env_file())
            print(f'Original Codex: {binary}\nSettings: {config_path()}\nTypeSafe key: configured')
            if not options.offline:
                verify(binary, key)
            return 0
        if args == ['auth', 'login']:
            prompt_key(force=True)
            print('TypeSafe API key saved.')
            return 0
        if args[:1] == ['uninstall']:
            parser = argparse.ArgumentParser(prog='jev-codex uninstall')
            parser.add_argument('--purge', action='store_true', help='Also remove saved settings and the TypeSafe key')
            uninstall(parser.parse_args(args[1:]).purge)
            return 0
        if args[:1] == ['settings']:
            if sys.platform != 'darwin':
                raise RuntimeError('The settings menu is available on macOS only.')
            app = Path(__file__).with_name('Jev Codex Settings.app')
            if not app.exists():
                raise RuntimeError('The settings menu is not installed. Run the installer again.')
            from appserver import AppServer
            server = AppServer(lambda *_: (_ for _ in ()).throw(RuntimeError('Unexpected settings request.')),
                               [installed_codex(), 'app-server', '--stdio'])
            try:
                server.initialize()
                models = server.models()
            finally:
                server.close()
            order = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')
            supported = {effort['reasoningEffort'] for model in models
                         for effort in model['supportedReasoningEfforts']}
            private_write(config_path().with_name('catalog.json'), {
                'models': [model['model'] for model in models],
                'efforts': [effort for effort in order if effort in supported]})
            return subprocess.call(['open', str(app), '--args', '--config', str(config_path())])
        if args[:2] == ['auto', 'on']:
            parser = argparse.ArgumentParser(prog='jev-codex auto on')
            parser.add_argument('thread_id')
            from native import enable_auto
            enable_auto(parser.parse_args(args[2:]).thread_id)
            print('Automatic routing will resume on the next turn.')
            return 0
        if not args or args[:1] == ['tui']:
            from native import run
            return run(args[1:] if args else [])
        if args[:1] in (['resume'], ['fork']):
            from native import run
            return run(args)
        if args[:1] == ['route']:
            args = args[1:]
        if args == ['--version']:
            print('jev-codex 0.3.0')
            return 0
        from router import main as route
        sys.argv[1:] = args
        return route()
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
