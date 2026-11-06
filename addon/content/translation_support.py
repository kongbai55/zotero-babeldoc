"""Validation for model responses and a narrow background-layer rendering fix."""
import hashlib
import json
import re
import threading
import time
import shutil
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def short_pdf_pair(mono, dual, directory):
    """Normalize generated names in Python before Gecko touches long paths."""
    root = Path(directory).resolve(strict=True)
    sources = [Path(path).resolve(strict=True) for path in (mono, dual)]
    if sources[0] == sources[1]: raise ValueError('译文和双语文件路径相同。')
    for path in sources:
        if not path.is_relative_to(root): raise ValueError('PDF 输出路径不在任务目录内。')
        with path.open('rb') as stream:
            if stream.read(5) != b'%PDF-': raise ValueError('输出文件不是 PDF。')
    staging = root / 'export'
    staging.mkdir(exist_ok=True)
    staging = staging.resolve(strict=True)
    if not staging.is_relative_to(root): raise ValueError('PDF 导出目录不在任务目录内。')
    targets = [staging / name for name in ('translated.pdf', 'bilingual.pdf')]
    if any(source in targets and source != targets[i] for i, source in enumerate(sources)):
        raise ValueError('PDF 导出文件名冲突。')
    for source, target in zip(sources, targets):
        if source != target: shutil.copy2(source, target)
        if source.stat().st_size != target.stat().st_size: raise OSError('PDF 短文件名副本不完整。')
    # Normalize generated files only after both verified copies exist.
    for source in sources:
        if source not in targets:
            try: source.unlink()
            except OSError: pass
    return tuple(str(path) for path in targets)


def blank_batch_items(value):
    try:
        if isinstance(value, str):
            value = value.strip()
            value = re.sub(r'^```(?:json)?\s*|\s*```$', '', value)
            value = re.sub(r'^<json>\s*|\s*</json>$', '', value)
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return 0
    items = parsed if isinstance(parsed, list) else [parsed]
    return sum(1 for item in items if isinstance(item, dict) and 'id' in item
               and ('output' in item or 'input' in item)
               and (not isinstance(item.get('output', item.get('input')), str)
                    or not item.get('output', item.get('input')).strip()))


class TranslationFailure(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


ERROR_MESSAGES = {
    'quota': '账户额度不足或触发请求限制', 'auth': '登录或认证失败',
    'model_unavailable': '所选模型不可用', 'network': '模型连接中断',
    'timeout': '模型调用超时', 'empty': '模型返回空译文',
    'truncated': '模型输出被截断或过滤', 'format': '模型返回格式不完整',
    'other': '模型调用失败（未提供可识别原因）',
    'empty_batch': '批量响应含空段落，交由逐段翻译处理',
}


def error_code(error):
    current = error
    for _ in range(6):
        code = getattr(current, 'code', None)
        if isinstance(code, str) and code in ERROR_MESSAGES: return code
        status = getattr(current, 'status_code', None)
        if status in (401, 403): return 'auth'
        if status == 429: return 'quota'
        if status == 404: return 'model_unavailable'
        current = getattr(current, '__cause__', None)
        if current is None: break
    name = type(error).__name__.lower()
    if 'timeout' in name: return 'timeout'
    if 'connection' in name: return 'network'
    return 'other'


class CheckedCache:
    def __init__(self, cache):
        self.cache = cache
    def __getattr__(self, name):
        return getattr(self.cache, name)
    def get(self, source):
        value = self.cache.get(source)
        # Legacy BabelDOC cached empty answers as successful translations.
        return value if isinstance(value, str) and value.strip() and not blank_batch_items(value) else None
    def set(self, source, value):
        if not isinstance(value, str) or not value.strip() or blank_batch_items(value):
            raise ValueError('拒绝缓存空译文。')
        return self.cache.set(source, value)


class ResponseGuard:
    def __init__(self):
        self.failures = {}
        self.lock = threading.Lock()
        self.paused = False
        self.consecutive_errors = 0
        self.skipped = 0
        self.completed = 0
        self.call_seconds = 0
        self.max_call_seconds = 0
        self.active = {}
        self.on_status = None

    @staticmethod
    def validate(response):
        choices = getattr(response, 'choices', None)
        if not choices:
            raise TranslationFailure('format', '模型没有返回译文。')
        choice = choices[0]
        if getattr(choice, 'finish_reason', None) not in (None, 'stop'):
            raise TranslationFailure('truncated', '模型输出被截断或过滤，本次保留原文。')
        content = getattr(getattr(choice, 'message', None), 'content', None)
        if not isinstance(content, str) or not content.strip():
            raise TranslationFailure('empty', '模型返回空译文，本次保留原文。')
        # BabelDOC retries only the blank items, preserving valid siblings. Do not
        # reject an entire batch or poison all later requests for one empty item.
        return response

    def progress_note(self):
        with self.lock:
            waiting = int(time.monotonic() - min(self.active.values())) if self.active else 0
            return (f'模型调用已完成 {self.completed} 次；当前等待 {waiting} 秒' if self.active else
                    f'模型调用已完成 {self.completed} 次') + ('；模型请求已暂停，未译处保留原文' if self.paused else '')

    def notify(self):
        if self.on_status:
            self.on_status(self.progress_note())

    def install(self, translator, request):
        translator.cache = CheckedCache(translator.cache)
        # Keep valid historical translations reusable; don't clear the user's cache.
        if request.get('backend', 'api') == 'api' and urlsplit(request.get('baseURL', '')).hostname == 'api.deepseek.com':
            translator.extra_body['thinking'] = {'type': 'disabled'}
        original = translator.client.chat.completions.create
        def create(**kwargs):
            with self.lock:
                if self.paused:
                    raise TranslationFailure('other', '模型请求已暂停，本段保留原文。')
            kwargs['max_tokens'] = max(int(kwargs.get('max_tokens') or 0), 8192)
            key = hashlib.sha256(json.dumps(kwargs.get('messages', []), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            with self.lock: self.active[threading.get_ident()] = time.monotonic()
            self.notify()
            try:
                response = self.validate(original(**kwargs))
            except Exception as error:
                code = error_code(error)
                with self.lock:
                    self.failures[key] = code
                    if code in ('quota', 'auth', 'model_unavailable'): self.paused = True
                    if code in ('network', 'timeout', 'other'):
                        self.consecutive_errors += 1
                        if self.consecutive_errors >= 3: self.paused = True
                # Avoid upstream's 100-attempt RateLimitError retry loop.
                raise TranslationFailure(code, ERROR_MESSAGES[code] + '；本段保留原文。') from error
            else:
                with self.lock:
                    self.consecutive_errors = 0
                    if blank_batch_items(response.choices[0].message.content): self.failures[key] = 'empty_batch'
                return response
            finally:
                with self.lock:
                    started = self.active.pop(threading.get_ident(), None)
                    elapsed = time.monotonic() - started if started is not None else 0
                    self.call_seconds += elapsed
                    self.max_call_seconds = max(self.max_call_seconds, elapsed)
                    self.completed += 1
                self.notify()
        translator.client.chat.completions.create = create
        for name in ('translate', 'llm_translate'):
            original_method = getattr(translator, name, None)
            if not callable(original_method): continue
            def call(text, ignore_cache=False, rate_limit_params=None, _original=original_method):
                if self.paused:
                    if not (getattr(translator, 'ignore_cache', False) or ignore_cache):
                        cached = translator.cache.get(text)
                        if cached is not None: return cached
                    with self.lock: self.skipped += 1
                    raise TranslationFailure('other', '模型请求已暂停，本段保留原文。')
                return _original(text, ignore_cache=ignore_cache, rate_limit_params=rate_limit_params)
            setattr(translator, name, call)

    def report(self, directory):
        pages = Counter()
        checked = False
        for path in (Path(directory) / 'working').rglob('translate_tracking.json'):
            try:
                tracking = json.loads(path.read_text(encoding='utf-8'))
                for index, page in enumerate(tracking.get('page', []), 1):
                    for para in page.get('paragraph', []):
                        if (str(para.get('input') or '').strip() and para.get('llm_translate_trackers')
                                and not str(para.get('output') or '').strip()): pages[index] += 1
                checked = True
            except (ValueError, OSError, TypeError): pass
        with self.lock:
            reasons = Counter(self.failures.values())
            return {'hasWarnings': bool(reasons or pages or self.skipped),
                    'untranslatedParagraphs': sum(pages.values()), 'pages': dict(pages),
                    'failedRequests': len(self.failures), 'skippedRequests': self.skipped,
                    'requestsPaused': self.paused, 'trackingChecked': checked,
                    'performance': {'modelCalls': self.completed, 'callSeconds': round(self.call_seconds, 2),
                                    'maxCallSeconds': round(self.max_call_seconds, 2)},
                    'reasons': [{'code': code, 'message': ERROR_MESSAGES[code], 'count': n} for code, n in reasons.items()]}


def annotate_partial_pdfs(mono, dual, report, alternating=False):
    """Comments mark original-language fallback without changing page layout."""
    if not report.get('hasWarnings'): return
    import pymupdf
    summary = ('BabelDOC: Translation needs review. Untranslated paragraphs retain the original text. '
               f"Detected untranslated paragraphs: {report['untranslatedParagraphs']}. "
               f"Failed requests: {report['failedRequests']}. "
               'See page comments and the Zotero attachment note for details.')
    for path, bilingual in ((mono, False), (dual, True)):
        with pymupdf.open(path) as doc:
            first = doc[0].add_text_annot((12, 12), summary)
            first.set_info(title='BabelDOC', subject='Partial translation / 含未译原文')
            for page, count in report.get('pages', {}).items():
                index = int(page) - 1
                if bilingual and alternating: index = index * 2 + 1
                if not 0 <= index < len(doc): continue
                note = doc[index].add_text_annot((12, 34),
                    f'BabelDOC: {count} paragraph(s) on source page {page} were not translated; original text retained.')
                note.set_info(title='BabelDOC', subject='未译原文 / Original text retained')
            doc.saveIncr()


def is_text_background(unit):
    curve = getattr(unit, 'curve', None)
    box = getattr(curve, 'box', None)
    if curve is None or box is None:
        return False
    try:
        height = abs(box.y2 - box.y)
    except (AttributeError, TypeError):
        return False
    if not 0 < height <= 30:
        return False
    instruction = getattr(getattr(curve, 'graphic_state', None), 'passthrough_per_char_instruction', '') or ''
    if not isinstance(instruction, str):
        instruction = instruction.encode().decode('latin-1') if hasattr(instruction, 'encode') else str(instruction)
    colors = re.findall(r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg\b', instruction)
    if not colors:
        return False
    rgb = tuple(float(v) for v in colors[-1])
    if min(rgb) < 0.4 or max(rgb) - min(rgb) < 0.02:
        return False
    return bool(getattr(curve, 'fill_background', False) or
                (getattr(curve, 'passthrough_paint', False) and re.search(r'\bf\*?\b', instruction)))


def install_background_fix():
    from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
    original = PDFCreater.create_render_units_for_page
    def create(self, page, config):
        units = original(self, page, config)
        for unit in units:
            if is_text_background(unit):
                # Preserve the paths and colors, only paint pastel text backgrounds first.
                unit.render_order = -1000000000
        return units
    PDFCreater.create_render_units_for_page = create
