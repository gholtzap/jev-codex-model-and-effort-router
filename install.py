"""Install Jev Codex for the current user. No package manager is required."""
import argparse
import fcntl
import getpass
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from appserver import AppServer
from jev_client import JevError, ask, load_key, validate_choice
from settings import atomic_write, config_path, data_dir, load_settings, private_write
from usage import read_budget, show_budget

FILES = ('cli.py', 'router.py', 'appserver.py', 'jev_client.py', 'usage.py',
         'settings.py', 'install.py', 'README.md', 'AGENTS.md')
MARKER = '# Jev Codex managed launcher'


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def find_codex(preferred=None):
    candidates = [preferred] if preferred else [str(Path(p) / 'codex') for p in os.get_exec_path()]
    for value in candidates:
        path = Path(value).expanduser().absolute()
        if path.is_file() and os.access(path, os.X_OK):
            with path.open('rb') as source:
                if MARKER.encode() in source.read(512):
                    continue
            return str(path)
    raise RuntimeError('Codex CLI was not found. Install Codex and run codex login, then repeat setup.')


def installed_codex():
    manifest = data_dir() / 'install.json'
    saved = json.loads(manifest.read_text()) if manifest.exists() else {}
    return find_codex(saved.get('codex_path'))


def verify(codex, key, check_jev=True):
    server = AppServer(lambda m, p: (_ for _ in ()).throw(RuntimeError('Unexpected setup request.')),
                       [codex, 'app-server', '--stdio'])
    try:
        server.initialize()
        account = server.call('account/read', {})
        if not account.get('account'):
            raise RuntimeError(f'Codex is not signed in. Run {shlex.quote(codex)} login, then repeat setup.')
        models = server.models()
        if not models:
            raise RuntimeError('Codex returned no available models.')
        print(f'Codex connection and login passed; {len(models)} models available.')
        settings = load_settings()
        show_budget(read_budget(server, settings['usage_policy'], settings['reserve_percent'],
                                settings['usage_limit'], required=settings['usage_policy'] != 'quality'))
    finally:
        server.close()
    if check_jev:
        result = ask(key, 'Installation check.', {'check': {'type': 'choice',
                     'instructions': 'Select ready.', 'criteria': {'ready': 'The connection is ready.'}}})
        validate_choice(result['answers'].get('check'), {'ready'})
        print('TypeSafe connection passed.')


def launcher(entry, mode):
    # Quote executable paths so spaces and shell characters remain literal.
    return (f'#!/bin/sh\n{MARKER}\nexec {shlex.quote(sys.executable)} '
            f'{shlex.quote(str(entry))} {mode} "$@"\n')


def shell_files():
    name = Path(os.environ.get('SHELL', '')).name
    if name == 'zsh':
        return [Path(os.environ.get('ZDOTDIR', Path.home())) / '.zshrc']
    if name == 'bash':
        login = next((Path.home() / p for p in ('.bash_profile', '.bash_login', '.profile')
                      if (Path.home() / p).exists()), Path.home() / '.profile')
        return [Path.home() / '.bashrc', login]
    raise RuntimeError('Automatic PATH setup supports zsh and bash. Use --no-shell for other shells.')


def install(codex, key, wrap=False, change_shell=True):
    root = data_dir()
    bindir = Path.home() / '.local/bin'
    if root.exists() and not (root / '.managed').exists():
        raise RuntimeError(f'Refusing to replace an unmanaged directory: {root}')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(root / '.managed', 'jev-codex\n')
    with open(root / '.install.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest_path = root / 'install.json'
        old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        wrap = wrap or old.get('wrap_codex', False)
        commands = {'jev-codex': '', 'codex-original': '--original'}
        if wrap:
            commands['codex'] = '--codex-wrapper'
        launchers = {str(bindir / name): launcher(root / 'current/cli.py', mode)
                     for name, mode in commands.items()}
        for filename in launchers:
            path = Path(filename)
            if path.is_symlink() or (path.exists() and digest(path.read_text()) != old.get('commands', {}).get(filename)):
                raise RuntimeError(f'Refusing to replace an existing command: {path}')
        shell_records = old.get('shell', {})
        block = '\n# Begin Jev Codex PATH\nexport PATH=' + shlex.quote(str(bindir)) + ':"$PATH"\n# End Jev Codex PATH\n'
        writes = dict(launchers)
        if change_shell:
            for file in shell_files():
                text = file.read_text() if file.exists() else ''
                if '# Begin Jev Codex PATH' in text and block not in text:
                    raise RuntimeError(f'The Jev Codex PATH block was changed in {file}. Restore or remove it first.')
                if block not in text:
                    writes[str(file)] = text + block
                    shell_records[str(file)] = {'block': block, 'created': not file.exists()}
        sources = {name: Path(__file__).with_name(name).read_text() for name in FILES}
        version = digest(json.dumps(sources, sort_keys=True))[:20]
        release = root / 'releases' / version
        if not release.exists():
            release.parent.mkdir(exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=release.parent))
            try:
                for name, text in sources.items():
                    atomic_write(staging / name, text)
                staging.rename(release)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        settings_file = config_path()
        if not settings_file.exists():
            writes[str(settings_file)] = json.dumps(load_settings(), indent=2) + '\n'
        credential = settings_file.with_name('credentials.env')
        writes[str(credential)] = 'JEV_API_KEY=' + shlex.quote(key) + '\n'
        record = {'codex_path': codex, 'wrap_codex': wrap, 'release': version,
                  'commands': {p: digest(t) for p, t in launchers.items()}, 'shell': shell_records}
        writes[str(manifest_path)] = json.dumps(record, indent=2) + '\n'
        backup = {}
        current = root / 'current'
        if current.exists() and not current.is_symlink():
            raise RuntimeError('The installed current path must be a managed symbolic link.')
        old_target = os.readlink(current) if current.is_symlink() else None
        try:
            for filename, text in writes.items():
                path = Path(filename)
                backup[filename] = (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
                mode = 0o755 if filename in launchers else (backup[filename][1] if filename in shell_records and backup[filename] else 0o600)
                # Resolve shell symlinks so a user's dotfile link is preserved.
                atomic_write(path.resolve() if filename in shell_records else path, text, mode)
            link = root / 'current.next'
            link.unlink(missing_ok=True)
            link.symlink_to(release)
            link.replace(current)
        except BaseException:
            for filename, previous in reversed(list(backup.items())):
                path = Path(filename)
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path.resolve() if filename in shell_records else path, *previous)
            if old_target is None:
                current.unlink(missing_ok=True)
            raise
    print(f'Installed: {bindir / "jev-codex"}')
    print('Defaults: balanced routing, 10% reserve, usage display on. Existing settings were kept.')
    if wrap:
        print('The optional codex command starts the router. Use codex-original for the standard Codex interface.')
    print('Open a new terminal, enter your project directory, and run ' + ('codex.' if wrap else 'jev-codex.'))


def uninstall(purge=False):
    root = data_dir()
    if not (root / '.managed').is_file() or not (root / 'install.json').is_file():
        raise RuntimeError('No managed installation was found.')
    with open(root / '.install.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads((root / 'install.json').read_text())
        for filename, expected in saved['commands'].items():
            path = Path(filename)
            if path.is_symlink() or (path.exists() and digest(path.read_text()) != expected):
                raise RuntimeError(f'Command was changed; preserve or move it before uninstall: {path}')
        for filename, record in saved['shell'].items():
            path = Path(filename)
            if not path.exists():
                continue
            text = path.read_text()
            if '# Begin Jev Codex PATH' in text and record['block'] not in text:
                raise RuntimeError(f'PATH block was changed; remove it before uninstall: {path}')
        for filename, record in saved['shell'].items():
            path = Path(filename)
            if path.exists() and record['block'] in path.read_text():
                text = path.read_text().replace(record['block'], '', 1)
                if not text and record['created'] and not path.is_symlink():
                    path.unlink()
                else:
                    atomic_write(path.resolve(), text, path.stat().st_mode & 0o777)
        for filename in saved['commands']:
            Path(filename).unlink(missing_ok=True)
        shutil.rmtree(root)
    if purge:
        for name in ('config.json', 'config.lock', 'credentials.env'):
            config_path().with_name(name).unlink(missing_ok=True)
    print('Removed the installed commands and managed PATH entries. Codex conversations and audit records were kept.')
    if not purge:
        print('Saved settings and the TypeSafe key were kept. Use --purge during uninstall to remove them.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wrap-codex', action='store_true', help='Let the codex command start this terminal client')
    parser.add_argument('--no-shell', action='store_true', help='Do not add ~/.local/bin to shell startup files')
    parser.add_argument('--codex-path', help='Path to the original Codex executable')
    parser.add_argument('--env-file', type=Path, help='Read an existing TypeSafe key file without displaying it')
    args = parser.parse_args()
    if sys.version_info < (3, 10) or os.name != 'posix':
        parser.error('Python 3.10 or later on macOS or Linux is required.')
    try:
        codex = find_codex(args.codex_path) if args.codex_path else installed_codex()
        key_file = args.env_file or config_path().with_name('credentials.env')
        if not key_file.exists() and not args.env_file:
            key_file = Path(__file__).with_name('.env')
        try:
            key = load_key(key_file)
        except (FileNotFoundError, JevError):
            if args.env_file or not sys.stdin.isatty():
                raise RuntimeError('Provide JEV_API_KEY in the environment or use --env-file PATH. Do not put keys in command arguments.') from None
            key = getpass.getpass('TypeSafe API key (input hidden): ').strip()
            if not key:
                raise RuntimeError('A TypeSafe API key is required.')
        verify(codex, key)
        install(codex, key, args.wrap_codex, not args.no_shell)
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f'Setup failed: {error}', file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('Setup cancelled.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
