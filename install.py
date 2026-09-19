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
         'settings.py', 'install.py', 'native.py', 'native_proxy.py', 'menu_bar.swift')
MARKER = '# Jev Codex managed launcher'
WEBSOCKETS_VERSION = '16.1.1'
TYPESAFE_CONSOLE = 'https://console.typesafe.ai'


def build_settings_app(root):
    if sys.platform != 'darwin':
        return
    executable = root / 'Jev Codex Settings.app/Contents/MacOS/JevCodexSettings'
    executable.parent.mkdir(parents=True)
    result = subprocess.run(['xcrun', 'swiftc', '-parse-as-library', str(root / 'menu_bar.swift'),
                             '-o', str(executable), '-framework', 'AppKit'],
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Could not build the macOS settings menu: ' + result.stderr[-1000:])
    atomic_write(executable.parent.parent / 'Info.plist', '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>JevCodexSettings</string>
<key>CFBundleIdentifier</key><string>com.gholtzap.jev-codex-settings</string>
<key>CFBundleName</key><string>Jev Codex</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>LSUIElement</key><true/>
</dict></plist>
''')


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
        verify_jev(key)


def verify_jev(key):
    result = ask(key, 'Installation check.', {'check': {'type': 'choice',
                 'instructions': 'Select ready.', 'criteria': {'ready': 'The connection is ready.'}}})
    validate_choice(result['answers'].get('check'), {'ready'})
    print('TypeSafe connection passed.')


def prompt_key(force=False, required=True, save=True):
    credential = config_path().with_name('credentials.env')
    if not force:
        try:
            return load_key(credential)
        except (FileNotFoundError, JevError):
            pass
    if not sys.stdin.isatty():
        if required:
            raise RuntimeError('Run jev-codex auth login in a terminal to add your TypeSafe API key.')
        return None
    print(f'Create or copy your TypeSafe API key at {TYPESAFE_CONSOLE}')
    suffix = '' if required else '; press Enter to set it up later'
    key = getpass.getpass(f'TypeSafe API key (input hidden{suffix}): ').strip()
    if not key:
        if required:
            raise RuntimeError('A TypeSafe API key is required.')
        return None
    verify_jev(key)
    if save:
        atomic_write(credential, 'JEV_API_KEY=' + shlex.quote(key) + '\n')
    return key


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


def install_vendor(path):
    result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-input',
                             '--disable-pip-version-check', '--target', str(path),
                             f'websockets=={WEBSOCKETS_VERSION}'],
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Could not install the WebSocket dependency: ' + result.stderr[-1000:])


def install(codex, key, wrap=None, change_shell=True):
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
        if wrap is None:
            wrap = old.get('wrap_codex', True)
        if wrap and Path(codex).absolute() == bindir / 'codex':
            raise RuntimeError('Codex already uses ~/.local/bin/codex, where the Jev wrapper must be installed. Move the original Codex command or use --no-wrap-codex.')
        commands = {'jev-codex': '', 'codex-original': '--original'}
        if wrap:
            commands['codex'] = '--codex-wrapper'
        launchers = {str(bindir / name): launcher(root / 'current/cli.py', mode)
                     for name, mode in commands.items()}
        old_commands = old.get('commands', {})
        for filename in launchers:
            path = Path(filename)
            if path.is_symlink() or (path.exists() and digest(path.read_text()) != old_commands.get(filename)):
                raise RuntimeError(f'Refusing to replace an existing command: {path}')
        obsolete = set(old_commands) - set(launchers)
        for filename in obsolete:
            path = Path(filename)
            if path.is_symlink() or (path.exists() and digest(path.read_text()) != old_commands[filename]):
                raise RuntimeError(f'Refusing to remove a changed command: {path}')
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
        version = digest(json.dumps({'sources': sources, 'websockets': WEBSOCKETS_VERSION}, sort_keys=True))[:20]
        release = root / 'releases' / version
        if not release.exists():
            release.parent.mkdir(exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=release.parent))
            try:
                for name, text in sources.items():
                    atomic_write(staging / name, text)
                install_vendor(staging / 'vendor')
                build_settings_app(staging)
                staging.rename(release)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        settings_file = config_path()
        settings_kept = settings_file.exists()
        if not settings_kept:
            writes[str(settings_file)] = json.dumps(load_settings(), indent=2) + '\n'
        credential = settings_file.with_name('credentials.env')
        if key is not None:
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
            for filename in set(writes) | obsolete:
                path = Path(filename)
                backup[filename] = (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
            for filename, text in writes.items():
                path = Path(filename)
                mode = 0o755 if filename in launchers else (backup[filename][1] if filename in shell_records and backup[filename] else 0o600)
                # Resolve shell symlinks so a user's dotfile link is preserved.
                atomic_write(path.resolve() if filename in shell_records else path, text, mode)
            for filename in obsolete:
                Path(filename).unlink(missing_ok=True)
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
    print('Defaults: balanced routing, automatic maximum effort, 10% reserve, usage display on.' +
          (' Existing settings were kept.' if settings_kept else ''))
    if wrap:
        print('The codex command keeps the standard terminal UI and routes user turns through Jev.')
    if key is None:
        print('Your TypeSafe API key will be requested when you first run codex.')
    if sys.platform == 'darwin':
        print('Run jev-codex settings to open the menu-bar settings app.')
    command = 'codex' if wrap else 'jev-codex'
    if change_shell:
        print(f'Open a new terminal, enter your project directory, and run {command}.')
    else:
        print(f'Run {bindir / command}, or add {bindir} to PATH.')


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
    settings_file = config_path()
    kept = [name for name, path in (
        ('settings', settings_file), ('TypeSafe key', settings_file.with_name('credentials.env')))
        if path.exists()]
    if purge:
        for name in ('config.json', 'config.lock', 'credentials.env', 'catalog.json'):
            config_path().with_name(name).unlink(missing_ok=True)
    print('Removed the installed commands and managed PATH entries. Codex conversations and audit records were kept.')
    if not purge and kept:
        print('Saved ' + ' and '.join(kept) + ' were kept. Use --purge during uninstall to remove them.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    wrapping = parser.add_mutually_exclusive_group()
    wrapping.add_argument('--wrap-codex', action='store_true', dest='wrap_codex', default=None,
                          help='Route the codex command through Jev (default for a new installation)')
    wrapping.add_argument('--no-wrap-codex', action='store_false', dest='wrap_codex',
                          help='Install only the separate jev-codex command')
    parser.add_argument('--no-shell', action='store_true', help='Do not add ~/.local/bin to shell startup files')
    parser.add_argument('--codex-path', help='Path to the original Codex executable')
    parser.add_argument('--env-file', type=Path, help='Read an existing TypeSafe key file without displaying it')
    args = parser.parse_args()
    if sys.version_info < (3, 10) or os.name != 'posix':
        parser.error('Python 3.10 or later on macOS or Linux is required.')
    root = data_dir()
    fresh_root = not root.exists()
    try:
        codex = find_codex(args.codex_path) if args.codex_path else installed_codex()
        if args.env_file:
            key = load_key(args.env_file, use_environment=False)
        else:
            key = prompt_key(required=False, save=False)
        verify(codex, key, check_jev=key is not None)
        install(codex, key, args.wrap_codex, not args.no_shell)
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        if fresh_root and (root / '.managed').is_file() and not (root / 'install.json').exists():
            shutil.rmtree(root, ignore_errors=True)
        print(f'Setup failed: {error}', file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        if fresh_root and (root / '.managed').is_file() and not (root / 'install.json').exists():
            shutil.rmtree(root, ignore_errors=True)
        print('Setup cancelled.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
