import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appserver import AppServer, RpcError
from jev_client import JevError, ask, load_key, validate_choice
from urllib.error import HTTPError
from router import choose, context_for, main, routes_for, run_check, run_turn, server_request
from usage import read_budget, usage_budget

MODEL = {'model': 'test-model', 'description': 'Test model',
         'supportedReasoningEfforts': [{'reasoningEffort': 'low', 'description': 'Fast'}]}


class RouterTests(unittest.TestCase):
    def test_usage_pressure_tracks_balance_reset_and_all_windows(self):
        def snapshot(used=70, reset=86400 * 5):
            return {'rateLimitsByLimitId': {'codex': {'primary': {
                'usedPercent': used, 'windowDurationMins': 10080, 'resetsAt': reset}}}}
        base = usage_budget(snapshot(), 'balanced', 10, now=0)
        low = usage_budget(snapshot(90), 'balanced', 10, now=0)
        soon = usage_budget(snapshot(reset=3600), 'balanced', 10, now=0)
        self.assertGreater(low['pressure'], base['pressure'])
        self.assertLess(soon['pressure'], base['pressure'])
        self.assertEqual(base['windows'][0]['daily_allowance_percent'], 4)
        self.assertEqual(usage_budget(snapshot(), 'quality', 10, now=0)['pressure'], 0)
        self.assertGreaterEqual(usage_budget(snapshot(reset=3600), 'conserve', 10, now=0)['pressure'], .5)
        both = snapshot(reset=3600)
        both['rateLimitsByLimitId']['codex']['secondary'] = {
            'usedPercent': 100, 'windowDurationMins': 300, 'resetsAt': 3600}
        self.assertEqual(usage_budget(both, 'balanced', 10, now=0)['pressure'], 1)
        for bad in (snapshot(reset=0), snapshot(float('nan')), snapshot(True), {},
                    {'rateLimitsByLimitId': {'codex': {'primary': {}}}}):
            with self.assertRaises(ValueError):
                usage_budget(bad, 'balanced', 10, now=0)
        self.assertTrue(usage_budget({**snapshot(), 'ordinaryUsageAllowed': False}, 'balanced', 10, now=0)['usage_blocked'])

    def test_usage_failure_is_explicit_and_routing_preference_is_authoritative(self):
        from unittest.mock import Mock
        server = Mock()
        server.call.side_effect = RpcError('unavailable')
        self.assertIn('unavailable', read_budget(server, 'quality', 10, 'codex'))
        with self.assertRaises(RuntimeError):
            read_budget(server, 'balanced', 10, 'codex', required=True)
        answer = {'answers': {
            'request_kind': {'type': 'choice', 'choice': 'task', 'confidence': 1,
                             'probabilities': {'conversation': 0, 'task': 1}},
            'task_class': {'type': 'choice', 'choice': 'routine', 'confidence': 1,
                           'probabilities': {'routine': 1, 'standard': 0, 'demanding': 0}},
            'route': {'type': 'choice', 'choice': 'test-model/low',
                      'confidence': 1, 'probabilities': {'test-model/low': 1}}}}
        for mode in ('quality', 'balanced'):
            budget = {'mode': mode, 'pressure': .8}
            with patch('router.ask', return_value=answer) as request:
                choose('key', routes_for([MODEL]), 'task', {}, 'jev', budget)
                self.assertEqual('usage_policy' in request.call_args.args[1], mode == 'balanced')
                self.assertEqual(request.call_args.args[1]['routing_preference']['level'], 'balanced')

    def test_demanding_work_enforces_model_and_effort_floor(self):
        models = [
            {'model': 'luna', 'description': 'Fast and affordable agentic coding model.',
             'supportedReasoningEfforts': [{'reasoningEffort': 'high', 'description': 'Deep'}]},
            {'model': 'sol', 'description': 'Reliable agentic workhorse for everyday tasks.',
             'supportedReasoningEfforts': [{'reasoningEffort': 'high', 'description': 'Deep'}]},
            {'model': 'astra', 'description': 'Our most capable model for complex, demanding work.',
             'supportedReasoningEfforts': [{'reasoningEffort': 'high', 'description': 'Deep'}]},
        ]
        probabilities = {'luna/high': .6, 'sol/high': .3, 'astra/high': .1}
        answer = {'answers': {
            'request_kind': {'type': 'choice', 'choice': 'task', 'confidence': 1,
                             'probabilities': {'conversation': 0, 'task': 1}},
            'task_class': {'type': 'choice', 'choice': 'demanding', 'confidence': 1,
                           'probabilities': {'routine': 0, 'standard': 0, 'demanding': 1}},
            'route': {'type': 'choice', 'choice': 'luna/high', 'confidence': .6,
                      'probabilities': probabilities}}}
        with patch('router.ask', return_value=answer):
            balanced = choose('key', routes_for(models), 'audit and repair', {}, 'jev', routing_preference='balanced')
            quality = choose('key', routes_for(models), 'audit and repair', {}, 'jev', routing_preference='higher_quality')
        self.assertEqual((balanced['model'], balanced['effort']), ('sol', 'high'))
        self.assertEqual((quality['model'], quality['effort']), ('astra', 'high'))
        self.assertTrue(balanced['policy_adjusted'])

        answer['answers']['route']['choice'] = 'astra/high'
        answer['answers']['route']['confidence'] = .1
        seven_percent_left = usage_budget({'rateLimitsByLimitId': {'codex': {'primary': {
            'usedPercent': 93, 'windowDurationMins': 10080, 'resetsAt': 86400 * 5}}}},
            'balanced', 10, now=0)
        with patch('router.ask', return_value=answer):
            usage_aware = choose('key', routes_for(models), 'audit and repair', {}, 'jev',
                                 seven_percent_left, routing_preference='balanced')
            usage_off = choose('key', routes_for(models), 'audit and repair', {}, 'jev',
                               {'mode': 'quality', 'pressure': 0}, routing_preference='balanced')
        self.assertEqual((usage_aware['model'], usage_aware['effort']), ('sol', 'high'))
        self.assertTrue(usage_aware['usage_adjusted'])
        self.assertEqual((usage_off['model'], usage_off['effort']), ('astra', 'high'))

        answer['answers']['task_class']['choice'] = 'routine'
        answer['answers']['task_class']['probabilities'] = {'routine': 1, 'standard': 0, 'demanding': 0}
        answer['answers']['request_kind']['choice'] = 'conversation'
        answer['answers']['request_kind']['probabilities'] = {'conversation': 1, 'task': 0}
        with patch('router.ask', return_value=answer):
            highest = choose('key', routes_for(models), 'hello', {}, 'jev',
                             routing_preference='highest_quality')
        self.assertEqual(highest['model'], 'astra')
        self.assertEqual(highest['request_kind'], 'conversation')

    def test_maximum_effort_caps_the_selected_model(self):
        model = {'model': 'astra', 'description': 'Our most capable model for complex, demanding work.',
                 'supportedReasoningEfforts': [
                     {'reasoningEffort': effort, 'description': effort}
                     for effort in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')]}
        probabilities = {f'astra/{effort}': value for effort, value in
                         zip(('low', 'medium', 'high', 'xhigh', 'max', 'ultra'), (.05, .05, .1, .2, .2, .4))}
        answer = {'answers': {
            'request_kind': {'type': 'choice', 'choice': 'task', 'confidence': 1,
                             'probabilities': {'conversation': 0, 'task': 1}},
            'task_class': {'type': 'choice', 'choice': 'demanding', 'confidence': 1,
                           'probabilities': {'routine': 0, 'standard': 0, 'demanding': 1}},
            'route': {'type': 'choice', 'choice': 'astra/ultra', 'confidence': .4,
                      'probabilities': probabilities}}}
        with patch('router.ask', return_value=answer):
            route = choose('key', routes_for([model]), 'complex task', {}, 'jev',
                           routing_preference='highest_quality', maximum_effort='high')
        self.assertEqual((route['model'], route['effort']), ('astra', 'high'))
        self.assertTrue(route['policy_adjusted'])

    def test_explicit_check_exit_output_and_timeout(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory, patch('sys.stdout', new_callable=io.StringIO):
            result = run_check([sys.executable, '-c', 'print("x" * 9000); raise SystemExit(2)'], directory, 5)
            self.assertEqual(result['exit_code'], 2)
            self.assertTrue(result['output_truncated'])
            self.assertEqual(len(result['output_tail']), 8000)
            with self.assertRaises(subprocess.TimeoutExpired):
                run_check([sys.executable, '-c', 'import time; time.sleep(5)'], directory, .02)

    def test_repair_limit_and_stages_use_new_decisions_in_same_thread(self):
        from unittest.mock import Mock
        for failed_checks in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / 'stages.json').write_text(json.dumps(['First stage', 'Second stage']))
                server = Mock()
                server.models.return_value = [MODEL]
                server.call.return_value = {'thread': {'id': 'test-thread', 'turns': []}}
                route = {'model': 'test-model', 'effort': 'low', 'confidence': 1}
                outcome = {'status': 'completed', 'error': None, 'execution_seconds': 0}
                check = {'exit_code': int(failed_checks), 'output_tail': 'check result', 'output_truncated': False}
                argv = ['router', '-C', directory, '--config', str(path / 'config.json'), '--state-dir', str(path / 'state'),
                        '--stages', str(path / 'stages.json'), '--check-command', 'true', '--max-retries', '1',
                        '--usage-policy', 'balanced']
                with patch('sys.argv', argv), patch('router.AppServer', return_value=server), patch('router.installed_codex', return_value='/fake/codex'), \
                     patch('router.load_key', return_value='key'), patch('router.choose', side_effect=lambda *a: dict(route)) as select, \
                     patch('router.run_turn', return_value=outcome) as turn, patch('router.run_check', return_value=check), \
                     patch('router.read_budget', return_value={'mode': 'balanced'}) as read, \
                     patch('router.show_budget'), patch('sys.stdout', new_callable=io.StringIO), \
                     patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(main(), int(failed_checks))
                self.assertEqual(select.call_count, 2)
                self.assertEqual(read.call_count, 2)
                self.assertEqual([c.args[1] for c in turn.call_args_list], ['test-thread', 'test-thread'])
                last_prompt = select.call_args.args[2]
                self.assertIn('verification failed' if failed_checks else 'Second stage', last_prompt)
                self.assertEqual(sum(c.args[0] == 'thread/start' for c in server.call.call_args_list), 1)

    def test_key_quotes_comments_export_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('export JEV_API_KEY="test-key" # comment\n')
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(load_key(path), 'test-key')
            with patch.dict('os.environ', {'JEV_API_KEY': 'override'}):
                self.assertEqual(load_key(path), 'override')
                self.assertEqual(load_key(path, use_environment=False), 'test-key')

    def test_http_failure_does_not_expose_key_and_retries_are_bounded(self):
        error = HTTPError('https://api.typesafe.ai', 429, 'rate limit', {}, None)
        with patch('jev_client.urlopen', side_effect=error) as request, patch('jev_client.time.sleep'):
            with self.assertRaises(JevError) as caught:
                ask('private-test-key', 'hello', {})
            self.assertEqual(request.call_count, 3)
            self.assertNotIn('private-test-key', str(caught.exception))
        error = HTTPError('https://api.typesafe.ai', 401, 'unauthorized', {}, None)
        with patch('jev_client.urlopen', side_effect=error) as request:
            with self.assertRaises(JevError):
                ask('private-test-key', 'hello', {})
            self.assertEqual(request.call_count, 1)

    def test_route_contract_rejects_unknown_model_and_effort(self):
        self.assertEqual(routes_for([MODEL])['test-model/low']['effort'], 'low')
        self.assertEqual(set(routes_for([MODEL], allowed_efforts=['low'])), {'test-model/low'})
        with self.assertRaises(ValueError):
            routes_for([MODEL], ['unknown'])
        with self.assertRaises(ValueError):
            routes_for([MODEL], policy={'bad': {'model': 'test-model', 'effort': 'invalid', 'description': 'x'}})
        with self.assertRaises(ValueError):
            routes_for([MODEL], policy={'bad': {'model': 'test-model', 'effort': 'low', 'description': 'x'}},
                       allowed_efforts=['high'])

    def test_invalid_jev_decisions_cannot_execute(self):
        valid = {'type': 'choice', 'choice': 'a', 'probabilities': {'a': .6, 'b': .4}, 'confidence': .2}
        self.assertEqual(validate_choice(valid, {'a', 'b'}), 'a')
        for changes in ({'choice': 'c'}, {'choice': []}, {'confidence': float('nan')},
                        {'probabilities': {'a': .6}}, {'probabilities': {'a': 1, 'b': 1}},
                        {'probabilities': {'a': True, 'b': False}}):
            with self.assertRaises(JevError):
                validate_choice({**valid, **changes}, {'a', 'b'})

    def test_history_keeps_latest_context_and_excludes_raw_tool_output(self):
        thread = {'turns': [{'items': [
            {'type': 'userMessage', 'content': [{'type': 'text', 'text': 'task'}]},
            {'type': 'commandExecution', 'status': 'failed', 'exitCode': 1, 'aggregatedOutput': 'PRIVATE_TOOL_TEXT'},
            {'type': 'agentMessage', 'text': 'continue here'}]}]}
        context = context_for(thread)
        self.assertIn('task', context['recent_history'])
        self.assertNotIn('PRIVATE_TOOL_TEXT', context['recent_history'])
        self.assertEqual(context_for(thread, 4), {'recent_history': 'here', 'history_truncated': True})

    def test_rpc_interleaved_events_server_requests_and_errors(self):
        script = '''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'method' not in m: continue
 if m['method']=='bad':
  print(json.dumps({'id':m['id'],'error':{'message':'failed'}}),flush=True);continue
 print(json.dumps({'method':'item/started','params':{'item':{'id':'early'}}}),flush=True)
 print(json.dumps({'id':'approval','method':'approval','params':{}}),flush=True)
 answer=json.loads(sys.stdin.readline())
 print(json.dumps({'id':m['id'],'result':answer['result']}),flush=True)
'''
        server = AppServer(lambda m,p: {'decision': 'decline'}, [sys.executable, '-u', '-c', script])
        try:
            self.assertNotEqual(os.getpgid(server.process.pid), os.getpgrp())
            self.assertEqual(server.call('test', {}), {'decision': 'decline'})
            self.assertIn('early', server.items)
            self.assertEqual(server.event()['params']['item']['id'], 'early')
            with self.assertRaises(RpcError):
                server.call('bad', {})
        finally:
            server.close()

    def test_disconnect_is_an_error(self):
        server = AppServer(lambda m,p: {}, [sys.executable, '-c', 'pass'])
        try:
            with self.assertRaises(RpcError):
                server.event(2)
        finally:
            server.close()

    def test_stream_and_final_are_not_printed_twice(self):
        class Fake:
            def call(self, method, params):
                self.params = params
                return {'turn': {'id': 'turn'}}
            def event(self, timeout):
                return self.events.pop(0)
        fake = Fake()
        fake.events = [
            {'method': 'item/agentMessage/delta', 'params': {'itemId': 'a', 'delta': 'hello'}},
            {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'id': 'a', 'text': 'hello'}}},
            {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'failed', 'error': {'message': 'bad'}}}}]
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            result = run_turn(fake, 'thread', 'prompt', {'model': 'm', 'effort': 'low'}, 10)
        self.assertEqual(output.getvalue().count('hello'), 1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(fake.params['threadId'], 'thread')
        self.assertEqual(fake.params['model'], 'm')

    def test_interrupt_requests_cancel_and_waits_for_completion(self):
        class Fake:
            def __init__(self): self.first = True; self.calls = []
            def call(self, method, params, **kwargs):
                self.calls.append(method)
                return {'turn': {'id': 'turn'}}
            def event(self, timeout):
                if self.first:
                    self.first = False
                    raise KeyboardInterrupt()
                return {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'interrupted'}}}
        fake = Fake()
        with patch('sys.stdout', new_callable=io.StringIO):
            result = run_turn(fake, 'thread', 'prompt', {'model': 'm', 'effort': 'low'}, 10)
        self.assertEqual(result['status'], 'interrupted')
        self.assertIn('turn/interrupt', fake.calls)

    def test_approval_requires_explicit_yes(self):
        with patch('router.prompt_user', return_value=''), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(server_request('item/commandExecution/requestApproval', {}), {'decision': 'decline'})
        with patch('router.prompt_user', return_value='y'), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(server_request('item/fileChange/requestApproval', {}), {'decision': 'accept'})
        with patch('router.prompt_user', return_value='n'), patch('sys.stdout', new_callable=io.StringIO) as output:
            server_request('item/fileChange/requestApproval', {'itemId': 'x'}, {'x': {'diff': 'proposed diff'}})
            self.assertIn('proposed diff', output.getvalue())
        with self.assertRaises(RpcError):
            server_request('unknown/request', {})


if __name__ == '__main__':
    unittest.main()
