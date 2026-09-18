"""Command entry point for routing, settings, and installation checks."""
import argparse
import json
import os
import sys
from pathlib import Path

from install import installed_codex, uninstall, verify
from jev_client import load_key
from settings import config_path, default_env_file, load_settings, set_setting

NATIVE_COMMANDS = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'mcp-server',
                   'app-server', 'completion', 'sandbox', 'debug', 'apply', 'a',
                   'resume', 'fork', 'cloud', 'features', '--version', '-V'}


def main():
    try:
        args = sys.argv[1:]
        if args[:1] == ['--codex-wrapper']:
            args = args[1:]
            if not args:
                print('Jev Codex routing is enabled. Use codex-original for the standard interface.', file=sys.stderr)
            if args and args[0] in NATIVE_COMMANDS:
                args = ['--original', *args]
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
        if args[:1] == ['uninstall']:
            parser = argparse.ArgumentParser(prog='jev-codex uninstall')
            parser.add_argument('--purge', action='store_true', help='Also remove saved settings and the TypeSafe key')
            uninstall(parser.parse_args(args[1:]).purge)
            return 0
        if args == ['--version']:
            print('jev-codex 0.2.0')
            return 0
        from router import main as route
        sys.argv[1:] = args
        return route()
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
