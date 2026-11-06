"""One translation per process. JSON request on stdin, NDJSON events on stdout.

Only this wrapper is bundled; BabelDOC is installed separately in a Python environment.
Credentials are never passed in arguments or written to a configuration file.
"""
import asyncio
import contextlib
import importlib.metadata
import json
import logging
import math
from pathlib import Path
import re
import sys
import time
import threading

PROTOCOL = sys.stdout
EMIT_LOCK = threading.Lock()


def emit(event):
    with EMIT_LOCK:
        PROTOCOL.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
        PROTOCOL.flush()


def validate_request(request):
    from urllib.parse import urlsplit
    if not isinstance(request, dict):
        raise ValueError('请求格式无效。')
    source = Path(request['input']).resolve(strict=True)
    if not source.is_file():
        raise ValueError('原文 PDF 不存在。')
    with source.open('rb') as file:
        if file.read(5) != b'%PDF-':
            raise ValueError('输入文件不是 PDF。')
    output = Path(request['output']).resolve(strict=True)
    if not output.is_dir() or source.is_relative_to(output):
        raise ValueError('输出目录无效，不能包含原文。')
    backend = request.get('backend', 'api')
    if backend not in ('api', 'codex'):
        raise ValueError('未知的翻译方式。')
    concurrency = request.get('codexConcurrency', 2)
    if type(concurrency) is not int or not 1 <= concurrency <= 4:
        raise ValueError('Codex 并行批次数需要是 1 到 4 的整数。')
    if backend == 'api':
        url = urlsplit(request['baseURL'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('模型接口地址无效。')
        if not str(request['model']).strip():
            raise ValueError('请填写模型名称。')
    for key in ('langIn', 'langOut'):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{1,19}', request[key]):
            raise ValueError('语言代码无效。')
    qps = request.get('qps', 2)
    if type(qps) is not int or not 1 <= qps <= 20:
        raise ValueError('每秒请求数需要是 1 到 20 的整数。')
    return source, output


def output_pdf(path, output):
    if not path:
        raise ValueError('BabelDOC 没有生成完整的译文和双语 PDF。')
    result = Path(path).resolve(strict=True)
    if not result.is_relative_to(output):
        raise ValueError('BabelDOC 返回了工作目录以外的文件。')
    with result.open('rb') as file:
        if file.read(5) != b'%PDF-':
            raise ValueError('BabelDOC 输出文件不是 PDF。')
    return str(result)


async def prepare_resources(assets):
    """Keep the event loop responsive while BabelDOC downloads/checks its cache.

    Upstream performs this warmup before yielding any PDF progress event. Report
    cache counts separately so a first download does not look like a frozen job.
    """
    from babeldoc.const import get_cache_file_path
    file_list = assets.generate_all_assets_file_list()
    files = [(kind, entry['name'], get_cache_file_path(entry['name'], kind))
             for kind, entries in file_list.items() for entry in entries]
    started = time.monotonic()

    def snapshot():
        counts = {'fonts': 0, 'models': 0, 'cmap': 0, 'tiktoken': 0}
        size = 0
        for kind, name, path in files:
            try:
                length = path.stat().st_size
            except OSError:
                continue
            if length:
                counts[kind] += 1
                size += length
        return counts, size

    def report():
        counts, size = snapshot()
        cached = sum(counts.values())
        total = len(files)
        elapsed = int(time.monotonic() - started)
        emit({'type': 'progress', 'phase': 'resources', 'cached': cached, 'total': total,
              'bytes': size, 'elapsed': elapsed,
              'stage': f'正在下载/校验本地资源：已缓存 {cached}/{total} 个，'
                       f'{size / 1024 / 1024:.1f} MB；已用 {elapsed} 秒（首次准备后会复用）',
              'percent': round(cached * 100 / total, 1) if total else 0})

    def warmup():
        try:
            assets.warmup()
        except SystemExit as error:
            raise RuntimeError('BabelDOC 资源下载失败，请检查网络或代理后重新准备资源。') from error

    report()
    task = asyncio.create_task(asyncio.to_thread(warmup))
    while not task.done():
        await asyncio.wait({task}, timeout=2)
        report()
    try:
        await task
    except Exception as error:
        raise RuntimeError(f'本地模型/字体资源准备失败：{error}；请检查网络或代理后重试。') from error
    emit({'type': 'progress', 'phase': 'resources', 'stage': '本地模型、字体和字符映射资源已下载并校验完成', 'percent': 100})


async def prepare_only():
    emit({'type': 'progress', 'phase': 'startup', 'stage': '正在加载本地 PDF 引擎（不调用翻译 API）', 'percent': 0})
    from babeldoc.assets import assets
    await prepare_resources(assets)
    emit({'type': 'ready', 'babeldoc': importlib.metadata.version('BabelDOC'),
          'python': sys.version.split()[0]})


async def translate(request):
    started = time.monotonic()
    source, output = validate_request(request)
    emit({'type': 'progress', 'phase': 'startup', 'stage': '正在加载本地 PDF 引擎', 'percent': 0})
    # Delay heavy imports until the input has been checked.
    from babeldoc.format.pdf.high_level import async_translate
    from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
    from babeldoc.translator.translator import OpenAITranslator, set_translate_rate_limiter
    from babeldoc.assets import assets

    resources_started = time.monotonic()
    await prepare_resources(assets)
    resource_seconds = time.monotonic() - resources_started
    from translation_support import ResponseGuard, install_background_fix, annotate_partial_pdfs, short_pdf_pair
    install_background_fix()
    emit({'type': 'progress', 'phase': 'startup', 'stage': '正在初始化模型接口和本地版面分析模型', 'percent': 0})

    using_codex = request.get('backend') == 'codex'
    if using_codex:
        from codex_backend import create_translator
        translator = create_translator(request, output)
    else:
        translator = OpenAITranslator(
            lang_in=request['langIn'], lang_out=request['langOut'],
            model=request['model'], base_url=request['baseURL'],
            api_key=request.get('apiKey') or 'local-no-key', ignore_cache=False,
            send_temperature=bool(request.get('sendTemperature', False)),
        )
    qps = 1 if using_codex else request.get('qps', 2)
    guard = ResponseGuard()
    guard.install(translator, request)
    last_percent = 0
    guard.on_status = lambda message: emit({'type': 'progress', 'phase': 'translation', 'stage': message, 'percent': last_percent})
    set_translate_rate_limiter(qps)
    config = TranslationConfig(
        translator=translator, input_file=str(source),
        lang_in=request['langIn'], lang_out=request['langOut'], doc_layout_model=None,
        output_dir=output, working_dir=output / 'working', pages=request.get('pages') or None,
        no_dual=False, no_mono=False, qps=qps,
        pool_max_workers=request.get('codexConcurrency', 2) if using_codex else None,
        auto_extract_glossary=not using_codex,
        use_rich_pbar=False, report_interval=0.5,
        use_alternating_pages_dual=bool(request.get('alternating', False)),
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
    )
    emit({'type': 'progress', 'phase': 'translation', 'stage': '资源已就绪，正在开始解析和翻译 PDF', 'percent': 0})
    engine_started = time.monotonic()
    async for event in async_translate(config):
        kind = event.get('type')
        if kind == 'error':
            raise RuntimeError(str(event.get('error', 'BabelDOC 翻译失败。')))
        if kind == 'finish':
            result = event['translate_result']
            mono = output_pdf(result.mono_pdf_path, output)
            dual = output_pdf(result.dual_pdf_path, output)
            if mono == dual:
                raise ValueError('译文和双语文件路径相同。')
            warnings = guard.report(output)
            performance = warnings.setdefault('performance', {})
            performance.update(elapsedSeconds=round(time.monotonic() - started, 2),
                               resourceSeconds=round(resource_seconds, 2),
                               engineSeconds=round(time.monotonic() - engine_started, 2))
            if using_codex: performance['codex'] = translator.codex_runner.performance()
            try:
                annotate_partial_pdfs(mono, dual, warnings, bool(request.get('alternating', False)))
            except Exception:
                warnings['hasWarnings'] = True
                warnings['annotationFailed'] = True
            try:
                (output / 'translation-report.json').write_text(json.dumps(warnings, ensure_ascii=False), encoding='utf-8')
            except OSError:
                warnings['hasWarnings'] = True
                warnings['reportWriteFailed'] = True
            mono, dual = short_pdf_pair(mono, dual, output)
            emit({'type': 'result', 'mono': mono, 'dual': dual, 'warnings': warnings})
            return
        if kind in ('progress_start', 'progress_update', 'progress_end'):
            progress = event.get('overall_progress', 0)
            percent = float(progress) if isinstance(progress, (int, float)) else 0
            if not math.isfinite(percent):
                percent = 0
            last_percent = max(0, min(100, percent))
            emit({'type': 'progress', 'phase': 'translation', 'stage': str(event.get('stage', '翻译中'))[:120] + '；' + guard.progress_note(),
                  'percent': max(0, min(100, percent))})
    raise RuntimeError('BabelDOC 提前结束，没有返回翻译结果。')


def main():
    request = {}
    try:
        # Keep stdout reserved for the protocol even if dependencies print banners.
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(sys.stderr):
            if sys.argv[1:] == ['--prepare']:
                asyncio.run(prepare_only())
                return 0
            if sys.argv[1:] == ['--check']:
                version = importlib.metadata.version('BabelDOC')
                from babeldoc.format.pdf.high_level import async_translate
                from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
                from babeldoc.translator.translator import OpenAITranslator
                emit({'type': 'ready', 'babeldoc': version, 'python': sys.version.split()[0]})
                return 0
            line = sys.stdin.readline(1024 * 1024 + 1)
            if len(line) > 1024 * 1024:
                raise ValueError('请求过大。')
            request = json.loads(line)
            if sys.argv[1:] == ['--list-codex-models']:
                from codex_backend import CodexRunner
                models = CodexRunner(Path.cwd(), request.get('codexBinary', '')).list_models()
                emit({'type': 'ready', 'models': models})
                return 0
            if sys.argv[1:] in (['--check-codex'], ['--login-codex']):
                from codex_backend import CodexRunner
                runner = CodexRunner(Path.cwd(), request.get('codexBinary', ''), request.get('codexModel', ''), request.get('codexEffort', 'low'))
                result = runner.login_chatgpt() if sys.argv[1:] == ['--login-codex'] else runner.check_login()
                emit({'type': 'ready', **result})
                return 0
            asyncio.run(translate(request))
        return 0
    except Exception as error:
        message = str(error)
        if isinstance(error, (ModuleNotFoundError, importlib.metadata.PackageNotFoundError)):
            message = '此 Python 环境缺少 BabelDOC 或依赖，请运行安装脚本并选择脚本输出的 Python 路径。'
        key = request.get('apiKey') if isinstance(request, dict) else None
        if key:
            message = message.replace(key, '[REDACTED]')
        emit({'type': 'error', 'message': message[:2000]})
        return 1


if __name__ == '__main__':
    # Child processes used by dependencies must not re-enter the protocol handler.
    import multiprocessing
    multiprocessing.freeze_support()
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
