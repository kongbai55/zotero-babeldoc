import asyncio
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('worker', ROOT / 'addon/content/worker.py')
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class WorkerTest(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.root = Path(self.tmp.name)
        self.source = self.root / '论文 with spaces.pdf'
        self.source.write_bytes(b'%PDF-1.4\noriginal')
        self.output = self.root / 'output'
        self.output.mkdir()
        self.request = dict(input=str(self.source), output=str(self.output), baseURL='http://127.0.0.1:11434/v1',
                            model='local-test', langIn='en', langOut='zh', qps=2, apiKey='test-secret')

    def tearDown(self):
        self.tmp.cleanup()

    def test_invalid_source_and_output_confinement(self):
        worker.validate_request(self.request)
        self.source.write_text('not pdf')
        with self.assertRaises(ValueError):
            worker.validate_request(self.request)
        with self.assertRaises(ValueError):
            worker.output_pdf(self.source, self.output)

    def test_exact_api_and_result_paths_with_fake_babeldoc(self):
        self.check_export(False)

    def test_partial_result_and_annotation_failure_still_export_both_pdfs(self):
        self.check_export(True)

    def test_codex_parallelism_reaches_engine_and_report(self):
        self.check_export(False,True)

    def check_export(self, partial, codex=False):
        if codex:self.request.update(backend='codex',codexConcurrency=3)
        observed = {}
        def annotate(*args):
            if partial: raise OSError('annotation failed')

        def config(**kwargs):
            observed['config'] = kwargs
            return types.SimpleNamespace(**kwargs)

        def translator(**kwargs):
            observed['translator'] = kwargs
            return object()

        async def translate(config):
            # Dependency noise must not leak into protocol output in CLI mode.
            yield {'type': 'progress_update', 'stage': '翻译', 'overall_progress': 50}
            mono, dual = self.output / '翻译.mono.pdf', self.output / '翻译.dual.pdf'
            mono.write_bytes(b'%PDF-1.4\nmono'); dual.write_bytes(b'%PDF-1.4\ndual')
            yield {'type': 'finish', 'translate_result': types.SimpleNamespace(mono_pdf_path=mono, dual_pdf_path=dual)}

        mods = {
            'babeldoc.format.pdf.high_level': types.SimpleNamespace(async_translate=translate),
            'babeldoc.format.pdf.translation_config': types.SimpleNamespace(TranslationConfig=config,
                WatermarkOutputMode=types.SimpleNamespace(NoWatermark='none')),
            'babeldoc.translator.translator': types.SimpleNamespace(OpenAITranslator=translator, set_translate_rate_limiter=lambda q: None),
            'babeldoc.assets': types.SimpleNamespace(assets=object()),
            'codex_backend': types.SimpleNamespace(create_translator=lambda *args:types.SimpleNamespace(
                codex_runner=types.SimpleNamespace(performance=lambda:{'calls':2,'concurrency':3}))),
            'translation_support': types.SimpleNamespace(ResponseGuard=lambda:types.SimpleNamespace(
                install=lambda *args:None,report=lambda *args:{'hasWarnings':partial,'untranslatedParagraphs':1 if partial else 0},progress_note=lambda:''),
                install_background_fix=lambda:None,annotate_partial_pdfs=annotate,short_pdf_pair=lambda mono,dual,output:(mono,dual)),
        }
        protocol = io.StringIO()
        async def prepared(_):
            observed['prepared'] = True
        with patch.dict(sys.modules, mods), patch.object(worker, 'PROTOCOL', protocol), \
                patch.object(worker, 'prepare_resources', prepared):
            asyncio.run(worker.translate(self.request))
        events = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(events[-1]['type'], 'result')
        self.assertEqual(events[-1]['warnings']['hasWarnings'],partial)
        if partial:self.assertTrue(events[-1]['warnings']['annotationFailed'])
        self.assertTrue(Path(events[-1]['dual']).is_file())
        self.assertFalse(observed['config']['no_dual'])
        self.assertFalse(observed['config']['no_mono'])
        self.assertIsNone(observed['config']['doc_layout_model'])
        if codex:
            self.assertEqual(observed['config']['pool_max_workers'],3)
            self.assertEqual(observed['config']['qps'],1)
            self.assertFalse(observed['config']['auto_extract_glossary'])
            self.assertEqual(events[-1]['warnings']['performance']['codex'],{'calls':2,'concurrency':3})
        else:self.assertFalse(observed['translator']['send_temperature'])
        self.assertGreaterEqual(events[-1]['warnings']['performance']['elapsedSeconds'],0)
        self.assertTrue(observed['prepared'])
        self.assertEqual(self.source.read_bytes(), b'%PDF-1.4\noriginal')

    def test_protocol_reports_missing_dependency_without_traceback(self):
        # The system Python deliberately has no BabelDOC installed.
        if importlib.util.find_spec('babeldoc'):
            self.skipTest('This check needs an interpreter without BabelDOC')
        proc = subprocess.run([sys.executable, '-u', str(ROOT / 'addon/content/worker.py')],
                              input=json.dumps(self.request), capture_output=True, text=True, encoding='utf-8', timeout=30)
        if proc.returncode == 0:
            self.skipTest('BabelDOC is available in the test interpreter')
        event = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(event['type'], 'error')
        self.assertNotIn('test-secret', proc.stdout)
        self.assertNotIn('Traceback', proc.stdout)

    def test_error_message_redacts_key(self):
        async def fail(_):
            raise RuntimeError('Authorization: test-secret')
        protocol = io.StringIO()
        with patch.object(worker, 'translate', fail), patch.object(worker, 'PROTOCOL', protocol), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps(self.request))), patch.object(sys, 'argv', ['worker.py']):
            self.assertEqual(worker.main(), 1)
        self.assertIn('[REDACTED]', protocol.getvalue())
        self.assertNotIn('test-secret', protocol.getvalue())

    def test_resource_preparation_reports_cache_progress_and_preserves_files(self):
        cache = self.root / 'cache'
        (cache / 'fonts').mkdir(parents=True)
        (cache / 'models').mkdir()
        (cache / 'fonts/font.ttf').write_bytes(b'font')
        def warmup():
            (cache / 'models/layout.onnx').write_bytes(b'model')
        assets = types.SimpleNamespace(warmup=warmup, generate_all_assets_file_list=lambda: {
            'fonts': [{'name': 'font.ttf'}], 'models': [{'name': 'layout.onnx'}]})
        const = types.SimpleNamespace(get_cache_file_path=lambda name, kind: cache / kind / name)
        protocol = io.StringIO()
        with patch.dict(sys.modules, {'babeldoc.const': const}), patch.object(worker, 'PROTOCOL', protocol):
            asyncio.run(worker.prepare_resources(assets))
        events = [json.loads(line) for line in protocol.getvalue().splitlines()]
        self.assertEqual(events[0]['cached'], 1)
        self.assertEqual(events[0]['total'], 2)
        self.assertEqual(events[-2]['cached'], 2)
        self.assertEqual(events[-1]['percent'], 100)
        self.assertTrue((cache / 'fonts/font.ttf').is_file())

    def test_resource_failure_is_actionable_instead_of_silent_exit(self):
        def fail():
            raise SystemExit(1)
        assets = types.SimpleNamespace(warmup=fail, generate_all_assets_file_list=lambda: {})
        const = types.SimpleNamespace(get_cache_file_path=lambda name, kind: self.root / name)
        with patch.dict(sys.modules, {'babeldoc.const': const}), patch.object(worker, 'PROTOCOL', io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '资源.*失败'):
                asyncio.run(worker.prepare_resources(assets))


if __name__ == '__main__':
    (ROOT / '.tmp').mkdir(exist_ok=True)
    unittest.main()
