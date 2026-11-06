"""Refresh release archive references after a version bump."""
from pathlib import Path
import json
root = Path(__file__).resolve().parents[1]
version = json.loads((root / 'addon/manifest.json').read_text(encoding='utf-8'))['version']
for name in ('README.md', 'README.zh-CN.md'):
    path = root / name
    text = path.read_text(encoding='utf-8')
    import re
    text = re.sub(r'zotero-babeldoc-\d+\.\d+\.\d+-install\.zip', f'zotero-babeldoc-{version}-install.zip', text)
    text = re.sub(r'zotero-babeldoc-\d+\.\d+\.\d+\.xpi', f'zotero-babeldoc-{version}.xpi', text)
    path.write_text(text, encoding='utf-8')
