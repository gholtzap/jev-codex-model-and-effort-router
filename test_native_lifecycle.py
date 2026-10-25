"""Failure cases: lost owner, active client, active turn, bad status, and child cleanup."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve
from native_proxy import backend_idle


class NativeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_probe_preserves_active_and_unknown_work(self):
        with tempfile.TemporaryDirectory() as directory:
            socket = Path(directory) / 'backend.sock'
            state = {'type': 'idle'}
            async def backend(connection):
                async for raw in connection:
                    request = json.loads(raw)
                    if 'id' not in request:
                        continue
                    method = request['method']
                    result = ({'data': ['thread-1'], 'nextCursor': None} if method == 'thread/loaded/list'
                              else {'thread': {'status': state}} if method == 'thread/read' else {})
                    await connection.send(json.dumps({'id': request['id'], 'result': result}))
            async with unix_serve(backend, str(socket)):
                self.assertTrue(await backend_idle(socket))
                state['type'] = 'active'
                self.assertFalse(await backend_idle(socket))
                state.clear()
                self.assertFalse(await backend_idle(socket))

    async def test_owner_exit_waits_for_client_then_cleans_backend(self):
        with tempfile.TemporaryDirectory(prefix='jl-') as directory:
            root = Path(directory)
            backend = root / 'backend'
            backend.write_text('#!' + sys.executable + '\n' + '''import asyncio, json, os, sys
from pathlib import Path
from websockets.asyncio.server import unix_serve
async def handle(c):
 async for raw in c:
  r=json.loads(raw)
  if 'id' in r:
   result={'data': [], 'nextCursor': None} if r['method']=='thread/loaded/list' else {}
   await c.send(json.dumps({'id':r['id'],'result':result}))
async def main():
 Path(sys.argv[-1].removeprefix('unix://')).with_name('backend.pid').write_text(str(os.getpid()))
 async with unix_serve(handle, sys.argv[-1].removeprefix('unix://')):
  await asyncio.Future()
asyncio.run(main())
''')
            backend.chmod(0o755)
            owner_code = '''import os, subprocess, sys, time
from pathlib import Path
root=Path(sys.argv[1])
code="import asyncio,os; from pathlib import Path; from native_proxy import serve; asyncio.run(serve(%r,Path(%r),Path(%r),owner_pid=%r,orphan_grace=.2))" % (str(root/'backend'),str(root/'route.sock'),str(root),os.getpid())
p=subprocess.Popen([sys.executable,'-c',code])
(root/'proxy.pid').write_text(str(p.pid))
time.sleep(60)
'''
            owner = subprocess.Popen([sys.executable, '-c', owner_code, str(root)], start_new_session=True)
            try:
                for _ in range(100):
                    if (root / 'route.sock').exists():
                        break
                    await asyncio.sleep(.05)
                self.assertTrue((root / 'route.sock').exists())
                proxy_pid = int((root / 'proxy.pid').read_text())
                async with unix_connect(str(root / 'route.sock')):
                    owner.kill()
                    owner.wait(timeout=5)
                    await asyncio.sleep(1.3)
                    os.kill(proxy_pid, 0)
                    self.assertTrue((root / 'backend.sock').exists())
                for _ in range(100):
                    if not (root / 'backend.sock').exists():
                        break
                    await asyncio.sleep(.05)
                self.assertFalse((root / 'backend.sock').exists())
            finally:
                if owner.poll() is None:
                    owner.kill()
                    owner.wait(timeout=5)
                for name in ['proxy.pid', 'backend.pid']:
                    if (root / name).exists():
                        try:
                            os.kill(int((root / name).read_text()), signal.SIGTERM)
                        except ProcessLookupError:
                            pass


if __name__ == '__main__':
    unittest.main()
