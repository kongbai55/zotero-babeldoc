"""Synthetic subprocess fixture: verifies transport/import, never performs translation."""
import json
from pathlib import Path
import shutil
import sys
import time

request = json.loads(sys.stdin.readline())
if request['model'] == 'wait-for-cancel':
    print(json.dumps({'type': 'progress', 'stage': 'waiting', 'percent': 1}), flush=True)
    time.sleep(120)
elif request['model'] == 'fail-secret':
    print(json.dumps({'type': 'error', 'message': 'failed key ' + request['apiKey']}), flush=True)
    sys.exit(1)
else:
    root = Path(request['output'])
    paths = {}
    for kind in ('mono', 'dual'):
        name = '论文_' + 'long-paper-title-' * 9 if request['model'] == 'long-path-fixture' else 'fixture'
        path = root / f'{name}.{kind}.pdf'
        shutil.copyfile(request['input'], path)
        paths[kind] = str(path)
    if request['model'] == 'fail-after-output':
        print(json.dumps({'type':'error','message':'fixture engine error after PDFs'}),flush=True)
        sys.exit(1)
    if request['model'] == 'long-path-fixture':
        from translation_support import short_pdf_pair
        paths['mono'],paths['dual']=short_pdf_pair(paths['mono'],paths['dual'],root)
    warnings = {'hasWarnings':True,'untranslatedParagraphs':2,'pages':{'1':2},'reasons':[{'code':'empty','message':'模型返回空译文','count':1}]} if request['model'] == 'partial-fixture' else {'hasWarnings':False}
    warnings['performance']={'elapsedSeconds':12,'modelCalls':4,'callSeconds':10,'maxCallSeconds':3}
    print(json.dumps({'type': 'progress', 'stage': 'fixture', 'percent': 50}), flush=True)
    print(json.dumps({'type': 'result', **paths, 'warnings':warnings}), flush=True)
