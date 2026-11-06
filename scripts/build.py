"""Build a deterministic XPI and a portable source/installer bundle (stdlib only)."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'addon/manifest.json').read_text(encoding='utf-8'))
out = ROOT / 'dist'
out.mkdir(exist_ok=True)
name = f'zotero-babeldoc-{manifest["version"]}'


def archive(target, files):
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path, arcname in sorted(files, key=lambda pair: pair[1]):
            info = zipfile.ZipInfo(arcname, (2026, 10, 27, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, path.read_bytes())


addon_files = [(p, p.relative_to(ROOT / 'addon').as_posix()) for p in (ROOT / 'addon').rglob('*')
               if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
xpi = out / f'{name}.xpi'
archive(xpi, addon_files)
sources = [(ROOT / p, p) for p in ('README.md', 'README.zh-CN.md', 'TESTING.md', 'LICENSE', 'package.json', 'requirements.txt', '.gitignore')]
for folder in ('addon', 'scripts', 'tests'):
    sources.extend((p, p.relative_to(ROOT).as_posix()) for p in (ROOT / folder).rglob('*')
                   if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
archive(out / f'{name}-source.zip', sources)
for filename in ('setup.ps1', 'setup.sh'):
    shutil.copyfile(ROOT / 'scripts' / filename, out / filename)
archive(out / f'{name}-install.zip', [(xpi, xpi.name), (ROOT / 'README.md', 'README.md'),
        (ROOT / 'README.zh-CN.md', 'README.zh-CN.md'), (ROOT / 'TESTING.md', 'TESTING.md'),
        (ROOT / 'LICENSE', 'LICENSE'), (ROOT / 'requirements.txt', 'requirements.txt'), (ROOT / 'scripts/setup.ps1', 'scripts/setup.ps1'),
        (ROOT / 'scripts/setup.sh', 'scripts/setup.sh')])
(out / 'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n'
    for p in sorted(out.iterdir()) if p.suffix in ('.xpi', '.zip')), encoding='utf-8')
print(xpi)
