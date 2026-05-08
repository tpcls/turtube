#!/usr/bin/env bash
# video_ascii.py 실행 래퍼 (venv 자동 활성화)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$PROJECT_DIR/venv_ascii/bin/activate"
python "$PROJECT_DIR/src/video_ascii.py" "$@"
