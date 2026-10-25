"""Launch the standard Codex terminal UI through the Jev route relay."""
import subprocess
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

from install import installed_codex, prompt_key, uninstall
from jev_client import JevError, load_key
from settings import default_env_file, request_auto_route, state_dir


def enable_auto(thread_id):
    request_auto_route(state_dir(), thread_id)


def key_action():
    try:
        load_key(default_env_file())
        return 'route'
    except (FileNotFoundError, JevError):
        pass
    while True:
        choice = input('Jev needs a TypeSafe API key. Add one [a], continue without Jev [Enter], or uninstall Jev [u]: ').strip().lower()
        if not choice:
            return 'original'
        if choice == 'u':
            return 'uninstall'
        if choice == 'a':
            return 'route' if prompt_key(force=True, required=False) else 'original'
        print('Enter a, u, or press Enter.')


def run(args):
    codex = installed_codex()
    try:
        action = key_action()
    except (EOFError, KeyboardInterrupt):
        print()
        return 130
    if action == 'original':
        print('Starting Codex without Jev. Run jev-codex auth login when you want to enable routing.')
        return subprocess.call([codex, *args])
    if action == 'uninstall':
        uninstall(purge=True)
        return subprocess.call([codex, *args])
    with tempfile.TemporaryDirectory(prefix='jev-codex-') as directory:
        root = Path(directory)
        socket = root / 'route.sock'
        log = root / 'relay.log'
        with log.open('w') as output:
            relay = subprocess.Popen([
                sys.executable, str(Path(__file__).with_name('native_proxy.py')),
                '--codex', codex, '--socket', str(socket),
                '--state-dir', str(state_dir()), '--owner-pid', str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
            terminal = None
            previous_signals = {}
            def stop(signum, _):
                raise SystemExit(128 + signum)
            try:
                for signum in (signal.SIGTERM, signal.SIGHUP):
                    previous_signals[signum] = signal.signal(signum, stop)
                for _ in range(100):
                    if socket.exists():
                        break
                    if relay.poll() is not None:
                        raise RuntimeError('Jev route relay failed to start: ' + log.read_text()[-1000:])
                    time.sleep(0.1)
                else:
                    raise TimeoutError('Jev route relay did not start.')
                terminal = subprocess.Popen([codex, '--remote', f'unix://{socket}', *args])
                return terminal.wait()
            finally:
                for signum, handler in previous_signals.items():
                    signal.signal(signum, handler)
                if terminal and terminal.poll() is None:
                    terminal.terminate()
                    try:
                        terminal.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        terminal.kill()
                        terminal.wait()
                if relay.poll() is None:
                    relay.terminate()
                    try:
                        relay.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        relay.kill()
                        relay.wait()
