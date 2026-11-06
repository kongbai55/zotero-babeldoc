#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
INSTALL_DIR=${INSTALL_DIR:-"$ROOT/.venv"}
"$PYTHON" -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14), 'Use Python 3.10-3.13 (recommended: 3.12)'"
"$PYTHON" -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/bin/python" -m pip install -r "$ROOT/requirements.txt"
if [ "${1:-}" = '--warmup' ]; then "$INSTALL_DIR/bin/babeldoc" --warmup; fi
printf '\nPaste this Python path into Zotero > Tools > BabelDOC > Settings:\n%s\n' "$INSTALL_DIR/bin/python"
