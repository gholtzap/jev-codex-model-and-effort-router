"""Failure cases: warning blocks a UI, critical admits work, invalid probes admit,
worker admission weakens, or a new interactive turn uses the worker policy.
"""
import asyncio
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


FAKE_CODEX = '''import asyncio,json,os,sys
from pathlib import Path
from websockets.asyncio.server import unix_serve
if sys.argv[1:2]!=['app-server']:
 Path(os.environ['START_MARKER']).write_text(json.dumps(sys.argv[1:]))
 sys.exit(0)
async def handle(c):
 async for raw in c:
  r=json.loads(raw)
  if 'id' in r:await c.send(json.dumps({'id':r['id'],'result':{}}))
async def main():
 async with unix_serve(handle,sys.argv[-1].removeprefix('unix://')):
  await asyncio.Future()
asyncio.run(main())
'''
GUARD = '''import json,os,sys
from pathlib import Path
r=json.loads(Path(os.environ['GUARD_FIXTURE']).read_text())
if sys.argv[1:]==['--sample']:
 print(r.get('raw',json.dumps(r['sample'])))
 sys.exit(r.get('probeExit',0))
sys.exit(r['strictExit'])
'''


def make_guard(root):
    binary = root/'bin'
    binary.mkdir()
    path = binary/'host-memory-check'
    path.write_text('#!'+sys.executable+'\n'+GUARD)
    path.chmod(0o755)
    return binary


class HostMemoryTests(unittest.IsolatedAsyncioTestCase):
    def test_installed_cli_admits_interactive_work_by_pressure(self):
        with tempfile.TemporaryDirectory(prefix='jm-') as directory:
            root = Path(directory)
            guard = make_guard(root)
            marker, fixture = root/'started.json', root/'memory.json'
            original = root/'original-codex'
            original.write_text('#!'+sys.executable+'\n'+FAKE_CODEX)
            original.chmod(0o755)
            env = {**os.environ,'HOME':str(root),'XDG_CONFIG_HOME':str(root/'config'),
                   'XDG_DATA_HOME':str(root/'data'),'XDG_STATE_HOME':str(root/'state'),
                   'START_MARKER':str(marker),'GUARD_FIXTURE':str(fixture),
                   'PATH':str(guard)+os.pathsep+os.environ.get('PATH',os.defpath)}
            with patch.dict(os.environ,env), patch('install.install_vendor',side_effect=lambda p:p.mkdir()), \
                    patch('install.build_settings_app'):
                install.install(str(original),'fixture-key',change_shell=False)
            sample = {'supported':True,'pressure':2,'ramBytes':24*2**30,
                      'compressedBytes':11*2**30,'swapUsedBytes':6*2**30}
            cases = [
                ('warning-resume',sample,['resume','saved-thread'],0),
                ('warning-launch',sample,[],0),
                ('warning-original',sample,['-m','gpt-6.1-sol'],0),
                ('compression-only',{**sample,'pressure':1},['resume','saved-thread'],0),
                ('critical-resume',{**sample,'pressure':4},['resume','saved-thread'],75),
                ('critical-original',{**sample,'pressure':4},['-m','gpt-6.1-sol'],75),
                ('unknown-pressure',{**sample,'pressure':3},['resume','saved-thread'],75),
                ('failed-probe',{'supported':True,'error':'probe failed'},['resume','saved-thread'],75),
                ('invalid-probe',{},['resume','saved-thread'],75),
                ('strict-exec',sample,['exec','work'],75),
                ('strict-review',sample,['review'],75),
                ('strict-server',sample,['app-server'],75),
            ]
            for name,metrics,args,expected in cases:
                with self.subTest(name=name):
                    fixture.write_text(json.dumps({'sample':metrics,'strictExit':75}))
                    marker.unlink(missing_ok=True)
                    result = subprocess.run([str(root/'.local/bin/codex'),*args],env=env,
                                            text=True,capture_output=True,timeout=15)
                    self.assertEqual(result.returncode,expected,result.stderr)
                    self.assertEqual(marker.exists(),expected==0,result.stdout)
                    if marker.exists():
                        forwarded=json.loads(marker.read_text())
                        self.assertEqual(forwarded[-len(args):] if args else [],args)

    async def test_interactive_turns_use_pressure_policy(self):
        with tempfile.TemporaryDirectory(prefix='jm-') as directory:
            root = Path(directory)
            guard=make_guard(root)
            fixture=root/'memory.json'
            received=[]
            async def backend(connection):
                async for raw in connection:
                    request=json.loads(raw)
                    received.append(request['method'])
                    if 'id' in request:
                        await connection.send(json.dumps({'id':request['id'],'result':{'accepted':True}}))
            with patch.dict(os.environ,{'PATH':str(guard)+os.pathsep+os.environ.get('PATH',os.defpath),
                                        'GUARD_FIXTURE':str(fixture)}):
                async with unix_serve(backend,str(root/'backend.sock')), unix_serve(
                        lambda c:relay(c,root/'backend.sock',root),str(root/'front.sock')):
                    async with unix_connect(str(root/'front.sock')) as client:
                        for pressure,allowed in [(2,True),(4,False)]:
                            fixture.write_text(json.dumps({'sample':{'supported':True,'pressure':pressure},'strictExit':75}))
                            received.clear()
                            await client.send(json.dumps({'id':pressure,'method':'turn/start',
                                                          'params':{'threadId':'saved-thread','input':[]}}))
                            result=json.loads(await client.recv())
                            self.assertEqual('error' not in result,allowed)
                            self.assertEqual('turn/start' in received,allowed)


if __name__=='__main__':unittest.main()
