"""Keep Codex's terminal UI and route user turns through Jev."""
import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).with_name('vendor')))
from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve
from websockets.exceptions import ConnectionClosed

from jev_client import load_key
from router import audit, choose, context_for, routes_for
from settings import default_env_file, load_settings
from usage import usage_budget


class Backend:
    def __init__(self, socket):
        self.socket = socket
        self.next_id = 0
        self.connection = None

    async def __aenter__(self):
        self.connection = await unix_connect(str(self.socket), max_size=None, compression=None)
        await self.call('initialize', {'clientInfo': {
            'name': 'jev_codex_route', 'title': 'Jev Codex route', 'version': '0.3.0'},
            'capabilities': {'experimentalApi': True}})
        await self.connection.send(json.dumps({'method': 'initialized', 'params': {}}))
        return self

    async def __aexit__(self, *_):
        await self.connection.close()

    async def call(self, method, params):
        self.next_id += 1
        request_id = f'jev-{self.next_id}'
        await self.connection.send(json.dumps({'id': request_id, 'method': method, 'params': params}))
        while True:
            message = json.loads(await self.connection.recv())
            if message.get('id') != request_id:
                continue
            if 'error' in message:
                raise RuntimeError(f"{method}: {message['error'].get('message', 'request failed')}")
            return message['result']


async def catalog(backend):
    result, cursor = [], None
    while True:
        page = await backend.call('model/list', {'limit': 100, 'cursor': cursor})
        result.extend(page['data'])
        cursor = page.get('nextCursor')
        if not cursor:
            return result


def user_request(params):
    if params.get('toolOutput') or not isinstance(params.get('input'), list):
        return None
    parts = [item['text'] for item in params['input']
             if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str)]
    if parts:
        return '\n'.join(parts)
    return '[Attachment without text]' if params['input'] else None


async def select(backend, params, state_dir):
    settings = load_settings()
    key = load_key(default_env_file())
    models = await catalog(backend)
    routes = routes_for(models, settings['allow_model'])
    thread_id = params['threadId']
    turns, cursor = [], None
    while len(json.dumps(turns)) < 32000 and len(turns) < 100:
        try:
            page = await backend.call('thread/turns/list', {
                'threadId': thread_id, 'limit': 20, 'cursor': cursor, 'itemsView': 'full'})
        except RuntimeError as error:
            if 'not materialized yet' in str(error) and not turns:
                break
            raise
        turns.extend(page['data'])
        cursor = page.get('nextCursor')
        if not cursor:
            break
    context = context_for({'turns': reversed(turns)})
    context['history_truncated'] |= bool(cursor)
    budget = None
    if settings['usage_policy'] != 'quality':
        snapshot = await backend.call('account/rateLimits/read', {})
        budget = usage_budget(snapshot, settings['usage_policy'], settings['reserve_percent'],
                              settings['usage_limit'])
        if budget['usage_blocked']:
            raise RuntimeError('Codex reports a usage limit. No turn was started.')
    route = await asyncio.to_thread(choose, key, routes, user_request(params), context,
                                    settings['jev_model'], budget)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    audit(state_dir / f'{thread_id}.jsonl', {'event': 'route', 'thread_id': thread_id,
          'model': route['model'], 'effort': route['effort'], 'confidence': route['confidence'],
          'usage_preference': budget['preference'] if budget else 'normal'})
    return route


async def relay(client, backend_socket, state_dir):
    async with unix_connect(str(backend_socket), max_size=None, compression=None) as upstream, Backend(backend_socket) as side:
        pending_routes = {}

        async def to_backend():
            async for raw in client:
                message = json.loads(raw)
                params = message.get('params', {})
                if message.get('method') == 'turn/start' and user_request(params) is not None:
                    try:
                        route = await asyncio.wait_for(select(side, params, state_dir), 120)
                    except Exception as error:
                        await client.send(json.dumps({'id': message['id'], 'error': {
                            'code': -32000, 'message': f'Jev routing failed: {error}'}}))
                        continue
                    params['model'] = route['model']
                    params['effort'] = route['effort']
                    mode = params.get('collaborationMode')
                    if isinstance(mode, dict) and isinstance(mode.get('settings'), dict):
                        mode['settings']['model'] = route['model']
                        mode['settings']['reasoning_effort'] = route['effort']
                    pending_routes[message['id']] = (params['threadId'], route)
                await upstream.send(json.dumps(message))

        async def to_client():
            async for raw in upstream:
                message = json.loads(raw)
                pending = pending_routes.pop(message.get('id'), None)
                if pending and 'result' in message:
                    thread_id, route = pending
                    try:
                        thread = (await side.call('thread/read', {
                            'threadId': thread_id, 'includeTurns': False}))['thread']
                        actual = (thread.get('model'), thread.get('reasoningEffort'))
                    except Exception as error:
                        actual = ('unverified', type(error).__name__)
                    if actual != (route['model'], route['effort']):
                        audit(state_dir / f'{thread_id}.jsonl', {'event': 'route_mismatch',
                              'thread_id': thread_id, 'expected': [route['model'], route['effort']],
                              'actual': actual})
                        turn_id = message['result'].get('turn', {}).get('id')
                        if turn_id:
                            try:
                                await side.call('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
                            except Exception:
                                pass
                await client.send(raw)

        tasks = [asyncio.create_task(to_backend()), asyncio.create_task(to_client())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except ConnectionClosed:
                pass


async def serve(codex, socket, state_dir):
    backend_socket = socket.with_name('backend.sock')
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    process = await asyncio.create_subprocess_exec(
        codex, 'app-server', '--listen', f'unix://{backend_socket}',
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    try:
        for _ in range(100):
            if backend_socket.exists():
                break
            if process.returncode is not None:
                raise RuntimeError('Codex app server exited during startup.')
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError('Codex app server did not start.')
        async with unix_serve(lambda client: relay(client, backend_socket, state_dir),
                              str(socket), max_size=None, compression=None):
            os.chmod(socket, 0o600)
            stopped = asyncio.create_task(stop.wait())
            exited = asyncio.create_task(process.wait())
            done, pending = await asyncio.wait((stopped, exited), return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if exited in done and not stop.is_set():
                raise RuntimeError('Codex app server stopped.')
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex', required=True)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.codex, args.socket, args.state_dir))


if __name__ == '__main__':
    main()
