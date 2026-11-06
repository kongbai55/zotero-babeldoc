import importlib.util
from pathlib import Path
import types
import unittest
import json
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('translation_support', ROOT / 'addon/content/translation_support.py')
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def response(text='完整译文', reason='stop'):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(finish_reason=reason,
        message=types.SimpleNamespace(content=text))])


class SupportTest(unittest.TestCase):
    def test_long_pdf_names_are_normalized_before_zotero_reads_them(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            directory=Path(folder)
            long='论文_'+('long-paper-title-'*9)
            mono,dual=[directory/(long+'.'+kind+'.pdf') for kind in ('mono','dual')]
            mono.write_bytes(b'%PDF-1.4\nmono');dual.write_bytes(b'%PDF-1.4\ndual')
            first,second=support.short_pdf_pair(mono,dual,directory)
            self.assertEqual(Path(first).name,'translated.pdf')
            self.assertEqual(Path(second).name,'bilingual.pdf')
            self.assertEqual(Path(first).read_bytes(),b'%PDF-1.4\nmono')
            self.assertEqual(Path(second).read_bytes(),b'%PDF-1.4\ndual')
            self.assertLess(len(first),260)
            self.assertFalse(mono.exists())
            self.assertEqual(support.short_pdf_pair(first,second,directory),(first,second))

    def test_short_name_export_rejects_external_pdf_before_creating_copies(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            directory=Path(folder);output=directory/'output';output.mkdir()
            first,second=directory/'first.pdf',output/'second.pdf'
            first.write_bytes(b'%PDF-1.4\n');second.write_bytes(b'%PDF-1.4\n')
            with self.assertRaises(ValueError):support.short_pdf_pair(first,second,output)
            self.assertTrue(first.exists());self.assertFalse((output/'export').exists())

    def test_rejects_empty_and_truncated_responses(self):
        for value in [response(''),response(' '),response(None),response('partial','length'),response('filtered','content_filter')]:
            with self.assertRaises(support.TranslationFailure):
                support.ResponseGuard.validate(value)
        self.assertIsNotNone(support.ResponseGuard.validate(response()))

    def test_empty_cache_is_retranslated_but_good_cache_is_preserved(self):
        for value, expected in [('',None),('   ',None),('[{"id":0,"output":""}]',None),('已完成','已完成')]:
            cache = support.CheckedCache(types.SimpleNamespace(get=lambda _:value))
            self.assertEqual(cache.get('source'), expected)
        cache = support.CheckedCache(types.SimpleNamespace(set=lambda a,b:None))
        with self.assertRaises(ValueError):
            cache.set('source','')

    def test_empty_response_does_not_poison_later_requests(self):
        recorded = []
        def create(**kwargs):
            recorded.append(kwargs)
            return response('', 'length') if len(recorded) == 1 else response('有效译文')
        translator = types.SimpleNamespace(cache=object(),extra_body={},client=types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
        guard = support.ResponseGuard()
        guard.install(translator, {'backend':'api','baseURL':'https://api.deepseek.com'})
        with self.assertRaises(support.TranslationFailure):
            translator.client.chat.completions.create(messages=[],max_tokens=2048)
        self.assertEqual(recorded[0]['max_tokens'],8192)
        self.assertEqual(translator.extra_body['thinking'],{'type':'disabled'})
        result = translator.client.chat.completions.create(messages=[{'content':'other'}])
        self.assertEqual(result.choices[0].message.content,'有效译文')
        self.assertFalse(guard.paused)
        self.assertEqual(len(recorded),2)

    def test_partial_batch_keeps_valid_siblings_and_is_not_cached(self):
        partial = '[{"id":0,"output":""},{"id":1,"output":"有效译文"}]'
        self.assertIsNotNone(support.ResponseGuard.validate(response(partial)))
        self.assertEqual(support.blank_batch_items(partial),1)
        cache = support.CheckedCache(types.SimpleNamespace(set=lambda *args:self.fail('bad batch cached')))
        with self.assertRaises(ValueError): cache.set('source',partial)

    def test_three_connection_failures_pause_new_calls_without_repeated_provider_requests(self):
        calls=[]
        def fail(**kwargs):
            calls.append(kwargs)
            raise support.TranslationFailure('network','provider private detail')
        translator=types.SimpleNamespace(cache=object(),extra_body={},client=types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fail))))
        guard=support.ResponseGuard();guard.install(translator,{'backend':'api'})
        for n in range(5):
            with self.assertRaises(support.TranslationFailure):translator.client.chat.completions.create(messages=[{'content':str(n)}])
        self.assertEqual(len(calls),3)
        self.assertTrue(guard.paused)

    def test_fatal_quota_pauses_calls_but_still_allows_cached_results(self):
        calls = []
        def fail(**kwargs):
            calls.append(kwargs)
            outer = RuntimeError('do not expose private-key or paper')
            outer.__cause__ = support.TranslationFailure('quota','private provider details')
            raise outer
        translator = types.SimpleNamespace(cache=types.SimpleNamespace(get=lambda text:'cached' if text=='hit' else None),extra_body={},
            client=types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fail))),
            llm_translate=lambda *args,**kwargs:None)
        guard = support.ResponseGuard();guard.install(translator,{'backend':'codex'})
        with self.assertRaises(support.TranslationFailure) as error: translator.client.chat.completions.create(messages=[])
        self.assertNotIn('private',str(error.exception)); self.assertTrue(guard.paused)
        self.assertEqual(translator.llm_translate('hit'),'cached')
        with self.assertRaises(support.TranslationFailure): translator.llm_translate('miss')
        self.assertEqual(len(calls),1)

    def test_tracking_report_counts_only_untranslated_attempted_paragraphs(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            working=Path(folder)/'working';working.mkdir()
            (working/'translate_tracking.json').write_text(json.dumps({'page':[{'paragraph':[
                {'input':'failed','output':'','llm_translate_trackers':[{}]},
                {'input':'ok','output':'译文','llm_translate_trackers':[{}]},
                {'input':'not requested','output':'','llm_translate_trackers':[]},
            ]}]}),encoding='utf-8')
            guard=support.ResponseGuard();report=guard.report(folder)
        self.assertEqual(report['untranslatedParagraphs'],1)
        self.assertEqual(report['pages'],{1:1})
        self.assertTrue(report['hasWarnings'])

    def test_only_pastel_filled_text_backgrounds_move_behind_text(self):
        def unit(height=10,instruction='0.79 0.80 0.91 rg',filled=True):
            return types.SimpleNamespace(curve=types.SimpleNamespace(box=types.SimpleNamespace(y=10,y2=10+height),
                graphic_state=types.SimpleNamespace(passthrough_per_char_instruction=instruction),fill_background=filled))
        self.assertTrue(support.is_text_background(unit()))
        self.assertFalse(support.is_text_background(unit(height=100)))
        self.assertFalse(support.is_text_background(unit(instruction='0 0 0 rg')))
        self.assertFalse(support.is_text_background(unit(filled=False)))
