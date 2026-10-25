"""Failure cases: wrong provider/model/key, invalid routes, refusals, truncation,
redirected credentials, failed requests, and settings that do not reach the relay.
All HTTP and Codex endpoints in these tests are local fixtures.
"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve
import jev_client
import install
import settings
from native_proxy import relay
from router import choose, routes_for
from test_router import MODEL


@contextmanager
def endpoint(transform=None):
    receipts=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            receipts.append({'path':self.path,'body':body,'authorization':self.headers.get('Authorization')})
            if self.path == '/v1/decisions':
                if any(not 2 <= len(q['choices']) <= 255 for q in body['questions']):
                    self.send_response(400);self.end_headers();return
                questions={q['name']:[c['value'] for c in q['choices']] for q in body['questions']}
            else:
                schema=body['response_format']['json_schema']['schema']['properties']['answers']['properties']
                questions={name:value['properties']['choice']['enum'] for name,value in schema.items()}
            answers={}
            for name,choices in questions.items():
                selected='task' if name=='request_kind' else 'routine' if name=='task_class' else choices[0]
                answers[name]={'type':'choice','choice':selected,'confidence':1,
                               'probabilities':{c:int(c==selected) for c in choices}}
            if self.path == '/v1/decisions':
                result={'model':body['model'],'usage':{'input_tokens':10,'output_tokens':0,'total_tokens':10},
                        'answers':[{**a,'name':name,'probabilities':[
                            {'value':v,'probability':p} for v,p in a['probabilities'].items()]}
                            for name,a in reversed(list(answers.items()))]}
            else:
                result={'model':body['model'],'usage':{'total_tokens':10},'choices':[{
                    'finish_reason':'stop','message':{'content':json.dumps({'answers':answers})}}]}
            status,headers,result=(transform(result,receipts) if transform else (200,{},result))
            data=json.dumps(result).encode();self.send_response(status)
            for name,value in headers.items():self.send_header(name,value)
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}/v1',receipts
    finally:server.shutdown();server.server_close();thread.join()


class ProviderApiTests(unittest.IsolatedAsyncioTestCase):
    def test_multiple_routing_models_share_the_choice_contract(self):
        with endpoint() as (url,receipts):
            for model in ['router-model-a','router-model-b']:
                api=settings.validate({'routing_provider':'openai','routing_model':model,
                                       'routing_base_url':url,'routing_api_key_env':'TEST_ROUTER_KEY'})
                result=choose('fixture-key',routes_for([MODEL]),'Do a narrow edit',{},'jev-latest',
                              None,'balanced','automatic',api)
                self.assertEqual(result['model'],MODEL['model'])
                self.assertEqual(result['routing_provider'],'openai')
                self.assertEqual(result['routing_model'],model)
            self.assertEqual([r['body']['model'] for r in receipts],['router-model-a','router-model-b'])
            self.assertTrue(all(r['path']=='/v1/chat/completions' for r in receipts))
            self.assertTrue(all(r['authorization']=='Bearer fixture-key' for r in receipts))
            self.assertTrue(all(r['body']['response_format']['json_schema']['strict'] for r in receipts))

    def test_decisions_uses_native_questions_and_probabilities(self):
        with endpoint() as (url,receipts):
            api=settings.validate({'routing_provider':'openai_decisions','routing_base_url':url,
                                   'routing_api_key_env':'TEST_ROUTER_KEY'})
            self.assertEqual(settings.routing_api(api)['model'],'gpt-6-luna')
            result=choose('fixture-key',routes_for([MODEL]),'Do a narrow edit',{},'jev-latest',
                          None,'balanced','automatic',api)
            self.assertEqual(result['routing_provider'],'openai_decisions')
            self.assertEqual(result['routing_model'],'gpt-6-luna')
            self.assertEqual(result['routing_usage']['input_tokens'],10)
            self.assertEqual(result['model'],MODEL['model'])
            request=receipts[0];self.assertEqual(request['path'],'/v1/decisions')
            self.assertNotIn('response_format',request['body'])
            self.assertEqual(request['body']['input'][0]['content'][0]['type'],'input_json')
            self.assertEqual(request['body']['input'][0]['content'][0]['data']['request'],'Do a narrow edit')
            self.assertEqual({q['name'] for q in request['body']['questions']},
                             {'request_kind','task_class'})
            self.assertTrue(all(q['type']=='choice' and q['choices'] and q['instructions']
                                for q in request['body']['questions']))
            self.assertEqual(settings.routing_key_env(settings.validate({
                'routing_provider':'openai_decisions'})),'OPENAI_API_KEY')
            api['routing_model']='another-decision-model'
            self.assertEqual(settings.routing_api(api)['model'],'another-decision-model')

    def test_decisions_handles_single_option_and_connection_check(self):
        with endpoint() as (url,receipts):
            api=settings.validate({'routing_provider':'openai_decisions','routing_base_url':url,
                                   'routing_api_key_env':'TEST_ROUTER_KEY'})
            question={'route':{'type':'choice','instructions':'Select','criteria':{'one':'One'}}}
            result=jev_client.ask('fixture-key',{},question,'gpt-6-luna',
                                  provider='openai_decisions',base_url=url)
            self.assertEqual(result['answers']['route']['probabilities'],{'one':1})
            self.assertEqual(len(receipts),0)
            with patch('install.load_settings',return_value=api):install.verify_jev('fixture-key')
            self.assertEqual(len(receipts),1)
            self.assertTrue(all(2<=len(q['choices'])<=255 for q in receipts[0]['body']['questions']))

    def test_decisions_rejects_refusals_incomplete_and_duplicate_answers(self):
        # Failures: refusal, wrong type/name/choice, missing or duplicate answers or
        # probabilities, invalid numbers, HTTP failure, redirect, and provider fallback.
        def refusal(r,_):r['answers'][0]={'type':'refusal','name':'route'};return 200,{},r
        def wrong_type(r,_):r['answers'][0]['type']='predicate';return 200,{},r
        def missing(r,_):r['answers']=[];return 200,{},r
        def duplicate(r,_):r['answers']*=2;return 200,{},r
        def wrong_name(r,_):r['answers'][0]['name']='unknown';return 200,{},r
        def unknown(r,_):r['answers'][0]['choice']='unknown';return 200,{},r
        def no_probabilities(r,_):r['answers'][0]['probabilities']=[];return 200,{},r
        def duplicate_probability(r,_):r['answers'][0]['probabilities']*=2;return 200,{},r
        def nan(r,_):r['answers'][0]['confidence']=float('nan');return 200,{},r
        def redirect(r,_):return 302,{'Location':'/stolen'},r
        def unauthorized(r,_):return 401,{}, {'error':'Do not print fixture-key'}
        questions={'route':{'type':'choice','instructions':'Select','criteria':{'one':'One','two':'Two'}}}
        for response in [refusal,wrong_type,missing,duplicate,wrong_name,unknown,
                         no_probabilities,duplicate_probability,nan,redirect,unauthorized]:
            with self.subTest(response=response.__name__),endpoint(response) as (url,receipts), \
                    patch('jev_client.urlopen',side_effect=AssertionError('Unexpected Jev request')):
                with self.assertRaises(jev_client.JevError) as error:
                    jev_client.ask('fixture-key',{},questions,'gpt-6-luna',
                                   provider='openai_decisions',base_url=url)
                self.assertNotIn('fixture-key',str(error.exception))
                self.assertEqual(len(receipts),1)

    def test_bad_responses_and_redirects_fail_without_provider_fallback(self):
        def refusal(r,_):r['choices'][0]['message']={'refusal':'No'};return 200,{},r
        def truncated(r,_):r['choices'][0]['finish_reason']='length';return 200,{},r
        def invalid(r,_):r['choices'][0]['message']['content']='{}';return 200,{},r
        def unknown(r,_):
            body=json.loads(r['choices'][0]['message']['content']);body['answers']['route']['choice']='unknown'
            r['choices'][0]['message']['content']=json.dumps(body);return 200,{},r
        def redirect(r,_):return 302,{'Location':'/stolen'},r
        def unauthorized(r,_):return 401,{}, {'error':'Do not print fixture-key'}
        questions={'route':{'type':'choice','instructions':'Select','criteria':{'one':'One'}}}
        for response in [refusal,truncated,invalid,unknown,redirect,unauthorized]:
            with self.subTest(response=response.__name__),endpoint(response) as (url,receipts), \
                    patch('jev_client.urlopen',side_effect=AssertionError('Unexpected Jev request')):
                with self.assertRaises(jev_client.JevError) as error:
                    jev_client.ask('fixture-key',{},questions,'model',provider='openai',base_url=url)
                self.assertNotIn('fixture-key',str(error.exception))
                self.assertEqual(len(receipts),1)

    def test_retry_is_bounded_and_local_models_can_omit_authentication(self):
        def retry(r,receipts):return (429,{},r) if len(receipts)<3 else (200,{},r)
        questions={'route':{'type':'choice','instructions':'Select','criteria':{'one':'One'}}}
        with endpoint(retry) as (url,receipts),patch('jev_client.time.sleep'):
            result=jev_client.ask('',{},questions,'local-model',provider='openai',base_url=url)
            self.assertEqual(result['answers']['route']['choice'],'one')
            self.assertEqual(len(receipts),3)
            self.assertTrue(all(r['authorization'] is None for r in receipts))

    def test_provider_settings_and_credentials_do_not_cross(self):
        values=settings.load_settings(Path('/nonexistent/router-provider-test.json'))
        self.assertEqual(settings.routing_api(values)['provider'],'jev')
        self.assertEqual(settings.routing_key_env(values),'JEV_API_KEY')
        for invalid in [{'routing_provider':'unknown'},{'routing_model':''},
                        {'routing_base_url':'http://remote.example/v1'},
                        {'routing_base_url':'https://key@api.example/v1'},
                        {'routing_api_key_env':'KEY\nEVIL=1'}]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):settings.validate(invalid)
        with self.assertRaises(ValueError):settings.routing_api(settings.validate({'routing_provider':'openai'}))
        with self.assertRaises(ValueError):settings.routing_key_env(settings.validate({
            'routing_provider':'openai','routing_base_url':'https://custom.example/v1'}))
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=True):
            path=Path(directory)/'credentials.env'
            jev_client.save_key(path,'jev-fixture','JEV_API_KEY')
            jev_client.save_key(path,'other-fixture','OTHER_ROUTER_KEY')
            self.assertEqual(jev_client.load_key(path),'jev-fixture')
            self.assertEqual(jev_client.load_key(path,key_env='OTHER_ROUTER_KEY'),'other-fixture')
            self.assertEqual(jev_client.load_key(path,key_env=''),'')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(ValueError):jev_client.save_key(path,'bad\nkey','OTHER_ROUTER_KEY')
            config=Path(directory)/'config.json'
            settings.set_setting('routing_model','old-model',config)
            settings.set_setting('routing_api_key_env','OLD_KEY',config)
            settings.set_setting('routing_provider','openai',config)
            saved=settings.load_settings(config)
            self.assertIsNone(saved['routing_model'])
            self.assertIsNone(saved['routing_api_key_env'])

    async def test_decisions_routes_a_real_relay_request(self):
        await self.test_configured_api_routes_a_real_relay_request('openai_decisions')

    async def test_configured_api_routes_a_real_relay_request(self, provider='openai'):
        with tempfile.TemporaryDirectory(prefix='rp-') as directory,endpoint() as (url,receipts):
            root=Path(directory)
            env={'XDG_CONFIG_HOME':str(root/'config'),'TEST_ROUTER_KEY':'relay-fixture-key'}
            with patch.dict(os.environ,env):
                settings.private_write(settings.config_path(),settings.validate({
                    'routing_provider':provider,'routing_model':'my-routing-model','routing_base_url':url,
                    'routing_api_key_env':'TEST_ROUTER_KEY','usage_policy':'quality','routing_mode':'turn'}))
                state={'model':MODEL['model'],'reasoningEffort':'low'};forwarded=[]
                async def backend(c):
                    async for raw in c:
                        q=json.loads(raw)
                        if 'id' not in q:continue
                        method=q['method']
                        if method=='model/list':result={'data':[MODEL],'nextCursor':None}
                        elif method=='thread/turns/list':result={'data':[],'nextCursor':None}
                        elif method=='thread/read':result={'thread':state}
                        elif method=='turn/start':
                            forwarded.append(q['params']);state.update(model=q['params']['model'],reasoningEffort=q['params']['effort'])
                            result={'turn':{'id':'turn-1'}}
                        else:result={}
                        await c.send(json.dumps({'id':q['id'],'result':result}))
                async with unix_serve(backend,str(root/'backend.sock')),unix_serve(
                        lambda c:relay(c,root/'backend.sock',root),str(root/'front.sock')):
                    async with unix_connect(str(root/'front.sock')) as c:
                        await c.send(json.dumps({'id':1,'method':'turn/start','params':{
                            'threadId':'thread-1','input':[{'type':'text','text':'Make a narrow edit'}]}}))
                        result=json.loads(await asyncio.wait_for(c.recv(),10))
                        self.assertNotIn('error',result)
                self.assertEqual(len(forwarded),1)
                self.assertEqual(forwarded[0]['model'],MODEL['model'])
                self.assertEqual(receipts[0]['body']['model'],'my-routing-model')
                self.assertEqual(receipts[0]['authorization'],'Bearer relay-fixture-key')


if __name__=='__main__':unittest.main()
