#!/usr/bin/env bash
# YouTube ASCII interface renderer.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$PROJECT_DIR/venv_ascii/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/venv_ascii/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
else
  PYTHON_BIN=python
fi

"$PYTHON_BIN" "$PROJECT_DIR/src/youtube_html_ascii.py" "$@"
