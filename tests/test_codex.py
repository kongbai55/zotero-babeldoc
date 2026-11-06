import importlib.util
import json
import io
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('codex_backend', ROOT / 'addon/content/codex_backend.py')
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


class CodexTest(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        with patch.object(backend, 'find_codex', return_value='C:/codex.exe'):
            self.runner = backend.CodexRunner(self.tmp.name, model='account-model')

    def tearDown(self):
        self.tmp.cleanup()

    def test_arguments_enforce_ephemeral_chatgpt_and_stdin(self):
        args = self.runner.arguments()
        self.assertIn('--ephemeral', args)
        self.assertIn('--ignore-user-config', args)
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertIn('model_provider="openai"', args)
        self.assertIn('read-only', args)
        self.assertEqual(args[-1], '-')
        self.assertTrue(Path(args[args.index('--output-schema')+1]).is_absolute())

    def test_calls_overlap_but_never_exceed_configured_limit_and_usage_is_aggregated(self):
        events='\n'.join(json.dumps(e) for e in [
            {'type':'item.completed','item':{'type':'agent_message','text':'{"translation":"译文"}'}},
            {'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':30}},
        ])
        for limit in (1,2,4):
            with patch.object(backend,'find_codex',return_value='C:/codex.exe'):
                runner=backend.CodexRunner(self.tmp.name,concurrency=limit)
            runner.checked=True
            lock=threading.Lock(); ready=threading.Event(); release=threading.Event()
            active=peak=0
            def fake_run(*args,**kwargs):
                nonlocal active,peak
                with lock:
                    active+=1;peak=max(peak,active)
                    if active==limit:ready.set()
                try:
                    if not release.wait(5):raise AssertionError('concurrency test timed out')
                    return 0,events
                finally:
                    with lock:active-=1
            with patch.object(backend,'run_process',side_effect=fake_run),ThreadPoolExecutor(max_workers=4) as pool:
                jobs=[pool.submit(runner.complete,[{'content':'source'}]) for _ in range(4)]
                try:
                    self.assertTrue(ready.wait(5),'requests did not overlap at the selected limit')
                    self.assertEqual(peak,limit)
                finally:release.set()
                self.assertTrue(all(job.result(timeout=5)=='译文' for job in jobs))
            metrics=runner.performance()
            self.assertEqual(metrics['calls'],4)
            self.assertEqual(metrics['usageEvents'],4)
            self.assertEqual(metrics['inputTokens'],400)
            self.assertEqual(metrics['cachedInputTokens'],80)
            self.assertEqual(metrics['outputTokens'],120)
            self.assertEqual(metrics['concurrency'],limit)

    def test_timeout_releases_concurrency_slot_and_is_timed(self):
        self.runner.checked=True
        events=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'{"translation":"译文"}'}})
        with patch.object(backend,'run_process',side_effect=[backend.CodexCallError('timeout','timeout'),(0,events)]):
            with self.assertRaises(backend.CodexCallError):self.runner.complete([{'content':'source'}])
            self.assertEqual(self.runner.complete([{'content':'source'}]),'译文')
        self.assertEqual(self.runner.performance()['calls'],2)
        self.assertEqual(self.runner.performance()['usageEvents'],0)
        for value in (0,5,True,'2'):
            with self.assertRaises(ValueError):backend.CodexRunner(self.tmp.name,concurrency=value)

    def test_api_key_login_is_rejected_without_model_call(self):
        with patch.object(backend, 'run_process', return_value=(0, 'Logged in using an API key')) as run:
            with self.assertRaisesRegex(RuntimeError, 'ChatGPT'):
                self.runner.complete([{'role':'user','content':'private paper text'}])
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.runner.checked)

    def test_provider_errors_are_classified_without_exposing_raw_details(self):
        cases={'usage limit reached private-key':'quota','401 token expired private-key':'auth',
               'model is not supported':'model_unavailable','stream disconnected private paper':'network','request timed out':'timeout'}
        self.runner.checked=True
        for message,code in cases.items():
            events=json.dumps({'type':'turn.failed','error':{'message':message}})
            with patch.object(backend,'run_process',return_value=(1,events)):
                with self.assertRaises(backend.CodexCallError) as result:self.runner.complete([{'content':'source'}])
            self.assertEqual(result.exception.code,code)
            self.assertNotIn('private',str(result.exception))

    def test_selected_effort_is_forwarded_to_cli(self):
        for effort in backend.EFFORTS:
            with patch.object(backend, 'find_codex', return_value='C:/codex.exe'):
                runner = backend.CodexRunner(self.tmp.name, model='chosen-model', effort=effort)
            args = runner.arguments()
            self.assertIn(f'model_reasoning_effort="{effort}"',args)
            self.assertEqual(args[args.index('--model')+1],'chosen-model')
        with self.assertRaises(ValueError):
            backend.CodexRunner(self.tmp.name,effort='ultra')

    def test_chatgpt_response_and_prompt_not_persisted(self):
        events = '\n'.join([json.dumps({'type':'thread.started','thread_id':'ephemeral-id'}),
            json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'translation':'测试 {v1}'})}})])
        with patch.object(backend, 'run_process', side_effect=[(0,'Logged in using ChatGPT'),(0,events)]) as run:
            self.assertEqual(self.runner.complete([{'role':'user','content':'private paper text {v1}'}]), '测试 {v1}')
        self.assertIn('private paper text', run.call_args.args[2])
        self.assertNotIn('private paper text', self.runner.schema.read_text())
        self.assertEqual([p.name for p in self.runner.directory.iterdir()], ['response-schema.json'])

    def test_reconnection_notice_does_not_discard_a_successful_final_answer(self):
        self.runner.checked=True
        events='\n'.join(json.dumps(event) for event in [
            {'type':'error','message':'Reconnecting after stream disconnected'},
            {'type':'item.completed','item':{'type':'agent_message','text':'{"translation":"已恢复的有效译文"}'}},
            {'type':'turn.completed'},
        ])
        with patch.object(backend,'run_process',return_value=(0,events)):
            self.assertEqual(self.runner.complete([{'content':'source'}]),'已恢复的有效译文')

    def test_empty_and_failed_responses_do_not_fall_back_to_api(self):
        self.runner.checked = True
        for result in [(1,''), (0,json.dumps({'type':'turn.failed'})),
                (0,json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'{"translation":""}'}}))]:
            with patch.object(backend, 'run_process', return_value=result):
                with self.assertRaises(RuntimeError):
                    self.runner.complete([{'role':'user','content':'source'}])

    def test_login_uses_official_cli_and_checks_chatgpt_status(self):
        with patch.object(backend, 'run_process', side_effect=[(0,'Login complete'),(0,'Logged in using ChatGPT')]) as run:
            self.assertEqual(self.runner.login_chatgpt()['account'],'chatgpt')
            self.assertEqual(run.call_args_list[0].args[0], ['C:/codex.exe','login'])

    def test_catalog_filters_hidden_nontext_and_uses_reported_capabilities(self):
        entry = {'model':'account-model','displayName':'Account model','isDefault':True,
                 'supportedReasoningEfforts':[{'reasoningEffort':'low'},{'reasoningEffort':'medium'},{'reasoningEffort':'ultra'}],
                 'defaultReasoningEffort':'medium','inputModalities':['text','image']}
        models = backend.normalise_models([entry, entry, {**entry,'model':'hidden','hidden':True},
                  {**entry,'model':'image','inputModalities':['image']},
                  {'model':'unknown-capabilities'}, None])
        self.assertEqual(len(models),1)
        self.assertEqual(models[0]['id'],'account-model')
        self.assertEqual(models[0]['efforts'],['low','medium'])
        self.assertEqual(models[0]['defaultEffort'],'medium')
        self.assertTrue(models[0]['isDefault'])

    def test_catalog_pagination_only_initializes_and_lists_without_chat(self):
        class Capture(io.BytesIO):
            def close(self):
                self.captured = self.getvalue()
                super().close()
        process = MagicMock()
        process.stdin = Capture()
        process.stdout = io.BytesIO(('\n'.join(json.dumps(event) for event in [
            {'id':1,'result':{'userAgent':'test'}},
            {'method':'catalog/updated','params':{}},
            {'id':2,'result':{'data':[{'model':'first','supportedReasoningEfforts':[{'reasoningEffort':'low'}]}],'nextCursor':'second-page'}},
            {'id':3,'result':{'data':[{'model':'second','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}],'nextCursor':None}},
        ])+'\n').encode())
        process.poll.return_value = 0
        with patch.object(backend.subprocess,'Popen',return_value=process) as launch, \
             patch.object(backend,'_child_job',return_value=lambda:None), \
             patch.object(self.runner,'check_login') as login, \
             patch.dict(backend.os.environ,{'OPENAI_API_KEY':'private-key'}):
            models = self.runner.list_models()
        self.assertEqual([model['id'] for model in models],['first','second'])
        requests = [json.loads(line) for line in process.stdin.captured.splitlines()]
        self.assertEqual([r['method'] for r in requests],['initialize','initialized','model/list','model/list'])
        self.assertEqual(requests[-1]['params']['cursor'],'second-page')
        self.assertFalse(requests[-1]['params']['includeHidden'])
        self.assertNotIn('OPENAI_API_KEY',launch.call_args.kwargs['env'])
        login.assert_called_once()

    def test_catalog_disconnect_fails_without_using_a_bundled_model_list(self):
        process = MagicMock()
        process.stdin, process.stdout = io.BytesIO(), io.BytesIO()
        process.poll.return_value = 0
        self.runner.checked = True
        with patch.object(backend.subprocess,'Popen',return_value=process), \
             patch.object(backend,'_child_job',return_value=lambda:None):
            with self.assertRaisesRegex(RuntimeError,'连接提前结束'):
                self.runner.list_models()
