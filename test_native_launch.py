"""Failure cases: host policy intercepts Jev launches, worker pass-through, or turns.
These checks exercise the installed launcher and relay, not a host memory policy.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve
import install
from native_proxy import relay

FAKE_CODEX = "import asyncio,json,os,sys\nfrom pathlib import Path\nfrom websockets.asyncio.server import unix_serve\nif sys.argv[1:2]!=['app-server'] or '--listen' not in sys.argv:\n Path(os.environ['START_MARKER']).write_text(json.dumps(sys.argv[1:]))\n sys.exit(0)\nasync def handle(c):\n async for raw in c:\n  r=json.loads(raw)\n  if 'id' in r:await c.send(json.dumps({'id':r['id'],'result':{}}))\nasync def main():\n async with unix_serve(handle,sys.argv[-1].removeprefix('unix://')):\n  await asyncio.Future()\nasyncio.run(main())\n"


def unrelated_host_command(root):
    binary=root/'bin'
    binary.mkdir()
    command=binary/'host-memory-check'
    command.write_text('#!'+sys.executable+'\nfrom pathlib import Path\nPath('+repr(str(root/'host-command-called'))+').touch()\nraise SystemExit(75)\n')
    command.chmod(0o755)
    return binary


class NativeLaunchTests(unittest.IsolatedAsyncioTestCase):
    def test_installed_launch_and_passthrough_ignore_host_policy(self):
        with tempfile.TemporaryDirectory(prefix='jl-') as directory:
            root=Path(directory)
            binary=unrelated_host_command(root)
            marker=root/'started.json'
            original=root/'original-codex'
            original.write_text('#!'+sys.executable+'\n'+FAKE_CODEX)
            original.chmod(0o755)
            env={**os.environ,'HOME':str(root),'XDG_CONFIG_HOME':str(root/'config'),
                 'XDG_DATA_HOME':str(root/'data'),'XDG_STATE_HOME':str(root/'state'),
                 'START_MARKER':str(marker),'PATH':str(binary)+os.pathsep+os.environ.get('PATH',os.defpath)}
            with patch.dict(os.environ,env), patch('install.install_vendor',side_effect=lambda p:p.mkdir()), \
                    patch('install.build_settings_app'):
                install.install(str(original),'fixture-key',change_shell=False)
            for args in [[],['resume','saved-thread'],['-m','gpt-6.1-sol'],['exec','work'],
                         ['review'],['app-server'],['mcp-server']]:
                with self.subTest(args=args):
                    marker.unlink(missing_ok=True)
                    result=subprocess.run([str(root/'.local/bin/codex'),*args],env=env,
                                          text=True,capture_output=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertTrue(marker.exists(),result.stdout)
                    forwarded=json.loads(marker.read_text())
                    self.assertEqual(forwarded[-len(args):] if args else [],args)
                    self.assertFalse((root/'host-command-called').exists())
            result=subprocess.run([str(root/'.local/bin/jev-codex'),'route','--help'],
                                  env=env,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'host-command-called').exists())

    async def test_turn_forwarding_does_not_consult_host_policy(self):
        with tempfile.TemporaryDirectory(prefix='jl-') as directory:
            root=Path(directory)
            binary=unrelated_host_command(root)
            received=[]
            async def backend(connection):
                async for raw in connection:
                    request=json.loads(raw)
                    received.append(request['method'])
                    if 'id' in request:
                        await connection.send(json.dumps({'id':request['id'],'result':{'accepted':True}}))
            with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ.get('PATH',os.defpath)}):
                async with unix_serve(backend,str(root/'backend.sock')), unix_serve(
                        lambda c:relay(c,root/'backend.sock',root),str(root/'front.sock')):
                    async with unix_connect(str(root/'front.sock')) as client:
                        await client.send(json.dumps({'id':1,'method':'turn/start',
                                                      'params':{'threadId':'saved-thread','input':[]}}))
                        result=json.loads(await client.recv())
                        self.assertNotIn('error',result)
                        self.assertIn('turn/start',received)
                        self.assertFalse((root/'host-command-called').exists())


if __name__=='__main__':unittest.main()
