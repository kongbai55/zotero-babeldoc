"""Prepare an isolated Zotero test profile; never touches personal library/profile."""
from pathlib import Path
import json
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
suffix = sys.argv[1] if len(sys.argv) > 1 else ''
if not all(c.isalnum() or c == '-' for c in suffix):
    raise ValueError('Invalid test suffix')
profile = ROOT / ('.test-profile' + suffix)
data = ROOT / ('.test-data' + suffix)
results = ROOT / 'test-results'
for path in (profile, data, results, profile / 'extensions'):
    path.mkdir(exist_ok=True)
files = {p.relative_to(ROOT / 'addon').as_posix(): p.read_bytes() for p in (ROOT / 'addon').rglob('*')
         if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
files['test-smoke.js'] = (ROOT / 'tests/zotero-smoke.js').read_bytes()
files['fixture_worker.py'] = (ROOT / 'tests/fixture_worker.py').read_bytes()
report = str(results / 'zotero-smoke.json')
files['test-config.json'] = json.dumps({'root': str(ROOT), 'report': report,
    'python': str(ROOT / '.venv/Scripts/python.exe'), 'fixture': str(results / 'fixture.pdf'),
    'preview': '--preview' in sys.argv[2:]}).encode()
files['bootstrap.js'] += ('''
var originalStartup = startup;
startup = async function(data, reason) {
  try {
    await originalStartup(data, reason);
    Services.scriptloader.loadSubScript(data.rootURI + 'test-smoke.js', this, 'UTF-8');
    runBabelSmoke(data.rootURI);
  } catch(error) {
    await IOUtils.writeJSON(REPORT, {success:false,error:String(error),stack:error.stack});
    Services.startup.quit(Components.interfaces.nsIAppStartup.eForceQuit);
  }
};
'''.replace('REPORT', json.dumps(report))).encode()
with zipfile.ZipFile(profile / 'extensions/babeldoc-local@zotero-babeldoc.local.xpi', 'w', zipfile.ZIP_DEFLATED) as z:
    for name, content in files.items():
        z.writestr(name, content)
prefs = {
    'extensions.autoDisableScopes': 0, 'extensions.enabledScopes': 15, 'extensions.startupScanScopes': 15,
    'xpinstall.signatures.required': False, 'extensions.zotero.useDataDir': True,
    'extensions.zotero.dataDir': str(data), 'extensions.zotero.firstRun2': False,
    'extensions.zotero.firstRunGuidance': False, 'extensions.zotero.automaticScraperUpdates': False,
    'extensions.zotero.sync.autoSync': False, 'app.update.enabled': False, 'app.update.auto': False,
    'extensions.update.enabled': False, 'toolkit.telemetry.enabled': False,
    'browser.shell.checkDefaultBrowser': False, 'extensions.zotero.debug.log': True,
    'extensions.zotero.debug.store': True, 'extensions.logging.enabled': True,
}
(profile / 'user.js').write_text('\n'.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});' for k, v in prefs.items()), encoding='utf-8')
stream = b'BT /F1 14 Tf 40 760 Td (BabelDOC transport and attachment fixture.) Tj ET'
objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
pdf = bytearray(b'%PDF-1.4\n'); offsets = [0]
for i, obj in enumerate(objects, 1):
    offsets.append(len(pdf)); pdf.extend(f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n')
xref = len(pdf); pdf.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
for offset in offsets[1:]:
    pdf.extend(f'{offset:010d} 00000 n \n'.encode())
pdf.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
(results / 'fixture.pdf').write_bytes(pdf)
print(profile)
