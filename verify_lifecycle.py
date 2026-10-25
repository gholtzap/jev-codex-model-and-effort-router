#!/usr/bin/env python3
"""Verify an installed proxy with a real Codex server and a local MCP worker."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def alive(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='],
                            capture_output=True, text=True, timeout=5)
    return result.returncode == 0 and not result.stdout.strip().startswith('Z')


async def wait_for(check, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        await asyncio.sleep(.2)
    raise AssertionError('The lifecycle check did not finish within its time limit.')


MCP = '''import json,os,sys
from pathlib import Path
Path(sys.argv[1]).write_text(str(os.getpid()))
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r: continue
 method=r['method']
 if method=='initialize':
  result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'lifecycle-probe','version':'1'}}
 elif method=='tools/list':
  result={'tools':[{'name':'ping','description':'Return pong','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='resources/list': result={'resources':[]}
 elif method=='prompts/list': result={'prompts':[]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
'''

OWNER = '''import os,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1])
p=subprocess.Popen([sys.executable,sys.argv[2],'--codex',sys.argv[3],
 '--socket',str(root/'route.sock'),'--state-dir',str(root/'state'),
 '--owner-pid',str(os.getpid())],start_new_session=True)
(root/'proxy.pid').write_text(str(p.pid))
time.sleep(300)
'''


async def scenario(release, codex, orphan):
    from native_proxy import Backend
    with tempfile.TemporaryDirectory(prefix='jev-live-') as directory:
        root = Path(directory)
        home = root / 'codex-home'
        home.mkdir()
        worker = root / 'mcp.py'
        worker.write_text(MCP)
        (home / 'config.toml').write_text(
            '[mcp_servers.lifecycle_probe]\ncommand = ' + json.dumps(sys.executable) +
            '\nargs = ' + json.dumps([str(worker), str(root/'worker.pid')]) +
            '\nstartup_timeout_sec = 10\n')
        environment = {**os.environ, 'CODEX_HOME': str(home)}
        stderr = (root/'proxy.log').open('w')
        owner = subprocess.Popen([sys.executable, '-c', OWNER, str(root),
            str(release/'native_proxy.py'), codex], env=environment,
            stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        proxy_pid = backend_pid = worker_pid = None
        try:
            await wait_for(lambda: (root/'route.sock').exists())
            proxy_pid = int((root/'proxy.pid').read_text())
            rows = subprocess.check_output(['/bin/ps','-axo','pid=,ppid=,comm='],text=True).splitlines()
            backend_pid = next(int(row.split(None,2)[0]) for row in rows
                               if row.split(None,2)[1] == str(proxy_pid))
            async with Backend(root/'route.sock') as client:
                result = await client.call('thread/start', {'cwd':str(root), 'ephemeral':True,
                    'approvalPolicy':'never', 'sandbox':'read-only'})
                thread_id = result['thread']['id']
                await wait_for(lambda: (root/'worker.pid').exists())
                worker_pid = int((root/'worker.pid').read_text())
                assert alive(backend_pid) and alive(worker_pid)
                print(json.dumps({'case':'owner-loss' if orphan else 'normal-stop',
                    'stage':'real-worker-started','proxy':proxy_pid,'backend':backend_pid,
                    'worker':worker_pid}),flush=True)
                if orphan:
                    owner.kill()
                    owner.wait(timeout=5)
                    await asyncio.sleep(2)
                    response = await client.call('thread/read', {'threadId':thread_id,'includeTurns':False})
                    assert response['thread']['status']['type'] == 'idle'
                    assert alive(proxy_pid) and alive(worker_pid)
                else:
                    os.kill(proxy_pid, signal.SIGTERM)
                    await wait_for(lambda: not alive(backend_pid) and not alive(worker_pid))
            disconnected = time.monotonic()
            if orphan:
                await asyncio.sleep(2)
                assert alive(proxy_pid) and alive(worker_pid), 'The reconnect grace period was skipped.'
                await wait_for(lambda: not alive(proxy_pid), seconds=80)
                assert time.monotonic()-disconnected >= 59
            else:
                await wait_for(lambda: not alive(proxy_pid))
            assert not alive(backend_pid) and not alive(worker_pid), 'A backend or worker remained.'
            return {'case':'owner-loss' if orphan else 'normal-stop','passed':True,
                    'proxyPid':proxy_pid,'backendPid':backend_pid,'workerPid':worker_pid,
                    'secondsAfterDisconnect':round(time.monotonic()-disconnected,2)}
        except BaseException:
            stderr.flush()
            pids = [pid for pid in (proxy_pid,backend_pid,worker_pid) if pid]
            if pids:
                print(subprocess.run(['/bin/ps','-p',','.join(map(str,pids)),
                    '-o','pid,ppid,pgid,stat,etime,args'],capture_output=True,text=True).stdout,file=sys.stderr)
            print((root/'proxy.log').read_text()[-3000:],file=sys.stderr)
            raise
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)
            for pid in (proxy_pid, backend_pid, worker_pid):
                if pid and alive(pid):
                    try:
                        os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            stderr.close()


async def verify(args):
    release = args.release.resolve()
    sys.path.insert(0,str(release))
    results = [await scenario(release,args.codex,False), await scenario(release,args.codex,True)]
    report = {'passed':True,'release':str(release),
        'proxySha256':hashlib.sha256((release/'native_proxy.py').read_bytes()).hexdigest(),
        'codex':args.codex,'cases':results}
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,default=Path.home()/'.local/share/jev-codex/current')
    parser.add_argument('--codex',required=True)
    parser.add_argument('--report',type=Path,required=True)
    asyncio.run(verify(parser.parse_args()))
