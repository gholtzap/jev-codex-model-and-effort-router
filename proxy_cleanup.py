"""Inspect abandoned managed proxies. Stop only disconnected, idle backends."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess

from native_proxy import backend_idle


def process_rows():
    result = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,args='], text=True, timeout=10)
    rows = []
    for line in result.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            try:
                rows.append((int(parts[0]), int(parts[1]), shlex.split(parts[2])))
            except ValueError:
                pass
    return rows


def candidates(rows):
    managed = Path.home() / '.local/share/jev-codex/releases'
    clients = {arg.removeprefix('unix://') for _, _, args in rows for arg in args
               if arg.startswith('unix://') and arg.endswith('/route.sock')}
    result = []
    for pid, parent, args in rows:
        if parent != 1 or len(args) < 4 or not Path(args[0]).name.lower().startswith('python'):
            continue
        source = Path(args[1])
        if source.name != 'native_proxy.py' or not source.is_relative_to(managed) or '--socket' not in args:
            continue
        index = args.index('--socket') + 1
        if index >= len(args) or args[index] in clients:
            continue
        result.append({'pid': pid, 'socket': args[index]})
    return result


def identity(pid):
    return subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'lstart='], text=True, timeout=10).strip()


async def inspect(proxy, apply):
    result = {**proxy, 'action': 'preserved'}
    try:
        started = identity(proxy['pid'])
        socket = Path(proxy['socket']).with_name('backend.sock')
        if not await asyncio.wait_for(backend_idle(socket), 10):
            return {**result, 'reason': 'Backend is active or its status is unknown.'}
        if not apply:
            return {**result, 'action': 'eligible', 'reason': 'Owner exited; no terminal client; backend idle.'}
        await asyncio.sleep(1)
        if proxy not in candidates(process_rows()) or identity(proxy['pid']) != started:
            return {**result, 'reason': 'Process ownership or client state changed.'}
        if not await asyncio.wait_for(backend_idle(socket), 10):
            return {**result, 'reason': 'Backend is active or its status is unknown.'}
        if proxy not in candidates(process_rows()) or identity(proxy['pid']) != started:
            return {**result, 'reason': 'Process ownership or client state changed.'}
        os.kill(proxy['pid'], signal.SIGTERM)
        for _ in range(100):
            await asyncio.sleep(.1)
            try:
                os.kill(proxy['pid'], 0)
            except ProcessLookupError:
                return {**result, 'action': 'stopped'}
        return {**result, 'action': 'stop-requested', 'reason': 'No forced termination was used.'}
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        return {**result, 'reason': str(error)}


async def cleanup(apply=False):
    results = []
    for proxy in candidates(process_rows()):
        result = await inspect(proxy, apply)
        results.append(result)
        print(json.dumps(result), flush=True)
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args(args)
    asyncio.run(cleanup(options.apply))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
