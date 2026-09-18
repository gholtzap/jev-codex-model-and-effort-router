"""Launch the standard Codex terminal UI through the Jev route relay."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from install import installed_codex


def enable_auto(thread_id):
    if len(thread_id) != 36 or any(c not in '0123456789abcdef-' for c in thread_id):
        raise ValueError('Provide a Codex thread ID.')
    (Path.home() / '.local/state/jev-codex' / f'{thread_id}.pin').unlink(missing_ok=True)


def run(args):
    codex = installed_codex()
    with tempfile.TemporaryDirectory(prefix='jev-codex-') as directory:
        root = Path(directory)
        socket = root / 'route.sock'
        log = root / 'relay.log'
        with log.open('w') as output:
            relay = subprocess.Popen([
                sys.executable, str(Path(__file__).with_name('native_proxy.py')),
                '--codex', codex, '--socket', str(socket),
                '--state-dir', str(Path.home() / '.local/state/jev-codex')],
                stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
            try:
                for _ in range(100):
                    if socket.exists():
                        break
                    if relay.poll() is not None:
                        raise RuntimeError('Jev route relay failed to start: ' + log.read_text()[-1000:])
                    time.sleep(0.1)
                else:
                    raise TimeoutError('Jev route relay did not start.')
                return subprocess.call([codex, '--remote', f'unix://{socket}', *args])
            finally:
                if relay.poll() is None:
                    relay.terminate()
                    try:
                        relay.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        relay.kill()
                        relay.wait()
