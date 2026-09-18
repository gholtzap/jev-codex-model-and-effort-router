import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve

from native_proxy import relay, user_request


class NativeProxyTests(unittest.IsolatedAsyncioTestCase):
    def test_user_request_keeps_attachments_and_skips_tool_output(self):
        self.assertEqual(user_request({'input': [{'type': 'localImage', 'path': '/tmp/a'}]}),
                         '[Attachment without text]')
        self.assertIsNone(user_request({'input': [], 'toolOutput': {'name': 'check', 'output': 'ok'}}))

    async def test_turn_route_and_other_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend_socket, front_socket = root / 'backend.sock', root / 'front.sock'
            received = []

            async def backend(connection):
                async for raw in connection:
                    request = json.loads(raw)
                    received.append(request)
                    if 'id' not in request or 'method' not in request:
                        continue
                    if request['method'] == 'thread/read':
                        result = {'thread': {'model': 'gpt-5.6-luna', 'reasoningEffort': 'low'}}
                    elif request['method'] == 'turn/start':
                        result = {'turn': {'id': 'turn-1'}}
                    else:
                        result = {}
                    await connection.send(json.dumps({'id': request['id'], 'result': result}))
                    if request['method'] == 'turn/start':
                        await connection.send(json.dumps({'id': 'approval-1',
                            'method': 'item/commandExecution/requestApproval',
                            'params': {'command': 'true'}}))

            async def choice(*_):
                return {'model': 'gpt-5.6-luna', 'effort': 'low'}

            with patch('native_proxy.select', side_effect=choice):
              async with unix_serve(backend, str(backend_socket), compression=None), \
                         unix_serve(lambda c: relay(c, backend_socket, root), str(front_socket), compression=None):
                async with unix_connect(str(front_socket), compression=None) as client:
                    await client.send(json.dumps({'id': 1, 'method': 'initialize', 'params': {}}))
                    self.assertEqual(json.loads(await client.recv())['id'], 1)
                    content = [{'type': 'text', 'text': 'Check this image'},
                               {'type': 'localImage', 'path': '/tmp/a'}]
                    await client.send(json.dumps({'id': 2, 'method': 'turn/start', 'params': {
                        'threadId': 'thread-1', 'input': content, 'model': 'gpt-5.6-sol',
                        'effort': 'medium', 'collaborationMode': {'mode': 'default', 'settings': {
                            'model': 'gpt-5.6-sol', 'reasoning_effort': 'medium',
                            'developer_instructions': 'Keep me.'}}}}))
                    self.assertEqual(json.loads(await client.recv())['result']['turn']['id'], 'turn-1')
                    approval = json.loads(await client.recv())
                    self.assertEqual(approval['method'], 'item/commandExecution/requestApproval')
                    await client.send(json.dumps({'id': approval['id'], 'result': {'decision': 'accept'}}))
                    for _ in range(20):
                        if any(x.get('id') == 'approval-1' and 'result' in x for x in received):
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(any(x.get('id') == 'approval-1' and 'result' in x for x in received))
                    sent = next(x['params'] for x in received if x.get('method') == 'turn/start')
                    self.assertEqual(sent['input'], content)
                    self.assertEqual((sent['model'], sent['effort']), ('gpt-5.6-luna', 'low'))
                    self.assertEqual(sent['collaborationMode']['settings'], {
                        'model': 'gpt-5.6-luna', 'reasoning_effort': 'low',
                        'developer_instructions': 'Keep me.'})

    async def test_route_failure_does_not_start_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend_socket, front_socket = root / 'backend.sock', root / 'front.sock'
            received = []

            async def backend(connection):
                async for raw in connection:
                    request = json.loads(raw)
                    received.append(request)
                    if 'id' in request:
                        await connection.send(json.dumps({'id': request['id'], 'result': {}}))

            async def fail(*_):
                raise RuntimeError('route unavailable')

            with patch('native_proxy.select', side_effect=fail):
              async with unix_serve(backend, str(backend_socket), compression=None), \
                         unix_serve(lambda c: relay(c, backend_socket, root), str(front_socket), compression=None):
                async with unix_connect(str(front_socket), compression=None) as client:
                    await client.send(json.dumps({'id': 1, 'method': 'turn/start', 'params': {
                        'threadId': 'thread-1', 'input': [{'type': 'text', 'text': 'do work'}]}}))
                    reply = json.loads(await client.recv())
                    self.assertIn('Jev routing failed', reply['error']['message'])
                    self.assertFalse(any(x.get('method') == 'turn/start' for x in received))

    async def test_native_model_selection_pins_the_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend_socket, front_socket = root / 'backend.sock', root / 'front.sock'
            received = []

            async def backend(connection):
                async for raw in connection:
                    request = json.loads(raw)
                    received.append(request)
                    if 'method' in request and 'id' in request:
                        result = {'thread': {'model': 'gpt-5.6-luna', 'reasoningEffort': 'low'}} \
                            if request['method'] == 'thread/read' else {}
                        await connection.send(json.dumps({'id': request['id'], 'result': result}))

            with patch('native_proxy.select') as select:
              async with unix_serve(backend, str(backend_socket), compression=None), \
                         unix_serve(lambda c: relay(c, backend_socket, root), str(front_socket), compression=None):
                async with unix_connect(str(front_socket), compression=None) as client:
                    await client.send(json.dumps({'id': 1, 'method': 'thread/settings/update',
                        'params': {'threadId': 'thread-1', 'model': 'gpt-5.6-luna', 'effort': 'low'}}))
                    self.assertEqual(json.loads(await client.recv())['id'], 1)
                    self.assertFalse((root / 'thread-1.pin').exists())
                    await client.send(json.dumps({'id': 2, 'method': 'thread/settings/update',
                        'params': {'threadId': 'thread-1', 'model': 'gpt-5.6-terra', 'effort': 'medium'}}))
                    self.assertEqual(json.loads(await client.recv())['id'], 2)
                    self.assertTrue((root / 'thread-1.pin').exists())
                    await client.send(json.dumps({'id': 3, 'method': 'turn/start', 'params': {
                        'threadId': 'thread-1', 'input': [{'type': 'text', 'text': 'Use this model'}],
                        'model': 'gpt-5.6-terra', 'effort': 'medium'}}))
                    self.assertEqual(json.loads(await client.recv())['id'], 3)
                    sent = next(x['params'] for x in received if x.get('method') == 'turn/start')
                    self.assertEqual((sent['model'], sent['effort']), ('gpt-5.6-terra', 'medium'))
                    select.assert_not_called()


if __name__ == '__main__':
    unittest.main()
