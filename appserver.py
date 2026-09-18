"""Codex App Server JSON-RPC transport over local standard IO."""
import collections
import json
import queue
import subprocess
import threading
import time


class RpcError(RuntimeError):
    pass


class AppServer:
    def __init__(self, request_handler, command=None):
        self.handler = request_handler
        self.events = collections.deque()
        self.items = {}
        self.inbox = queue.Queue()
        self.counter = 0
        self.process = subprocess.Popen(command or ['codex', 'app-server', '--stdio'],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.inbox.put(json.loads(line))
                except json.JSONDecodeError:
                    self.inbox.put(RpcError('Codex returned malformed JSON.'))
        finally:
            self.inbox.put(RpcError('Codex App Server disconnected.'))

    def send(self, message):
        try:
            self.process.stdin.write(json.dumps(message) + '\n')
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise RpcError('Codex App Server disconnected.') from None

    def receive(self, timeout):
        try:
            message = self.inbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError('Timed out waiting for Codex.') from None
        if isinstance(message, Exception):
            raise message
        if message.get('method') in ('item/started', 'item/completed'):
            item = message.get('params', {}).get('item', {})
            if 'id' in item:
                self.items[item['id']] = item
        if 'method' in message and 'id' in message:
            try:
                result = self.handler(message['method'], message.get('params', {}))
                self.send({'id': message['id'], 'result': result})
            except (EOFError, KeyboardInterrupt):
                self.send({'id': message['id'], 'error': {'code': -32000, 'message': 'User input cancelled'}})
                raise
            except Exception:
                self.send({'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported client request'}})
                raise
            return None
        return message

    def call(self, method, params, timeout=90):
        self.counter += 1
        request_id = self.counter
        self.send(dict(id=request_id, method=method, params=params))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'Timed out during {method}.')
            message = self.receive(remaining)
            if message is None:
                continue
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise RpcError(f"{method}: {message['error'].get('message', 'request failed')}")
                return message['result']
            if 'method' in message:
                self.events.append(message)

    def event(self, timeout=1):
        if self.events:
            return self.events.popleft()
        return self.receive(timeout)

    def initialize(self):
        self.call('initialize', {'clientInfo': {'name': 'jev_codex', 'title': 'Jev Codex router', 'version': '0.1.0'},
                                 'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized', 'params': {}})

    def models(self):
        result, cursor = [], None
        while True:
            page = self.call('model/list', {'limit': 100, 'cursor': cursor})
            result.extend(page['data'])
            cursor = page.get('nextCursor')
            if not cursor:
                return result

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.reader.join(timeout=2)
        self.process.stdout.close()
