#!/bin/bash
# AutoCut standalone environment verifier

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$ROOT_DIR/venv/bin/python3"
REQ_FILE="$ROOT_DIR/requirements.lock.txt"
FAIL=0

check() {
  local label="$1"
  local cmd="$2"
  if eval "$cmd" >/dev/null 2>&1; then
    echo "[OK]   $label"
  else
    echo "[FAIL] $label"
    FAIL=1
  fi
}

echo "=== AutoCut Doctor ==="
echo "root: $ROOT_DIR"

check "manifest.xml exists" "[ -f \"$ROOT_DIR/CSXS/manifest.xml\" ]"
check "main.js exists" "[ -f \"$ROOT_DIR/main.js\" ]"
check "pipeline.py exists" "[ -f \"$ROOT_DIR/python/pipeline.py\" ]"
check "requirements.lock.txt exists" "[ -f \"$REQ_FILE\" ]"
check "ffmpeg available" "command -v ffmpeg"
check "ffprobe available" "command -v ffprobe"
check "venv python exists" "[ -x \"$PYTHON_BIN\" ]"

if [ -x "$PYTHON_BIN" ]; then
  PY_INFO=$("$PYTHON_BIN" -c "import platform,sys; print(platform.machine(), f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || true)
  echo "python: $PY_INFO"
  check "python is 3.11 arm64" "[ \"$("$PYTHON_BIN" -c \"import platform,sys; print(platform.machine() == 'arm64' and sys.version_info[:2] == (3, 11))\")\" = \"True\" ]"
  check "anthropic import" "\"$PYTHON_BIN\" -c 'import anthropic'"
  check "openai import" "\"$PYTHON_BIN\" -c 'import openai'"
  check "mlx_whisper import" "\"$PYTHON_BIN\" -c 'import mlx_whisper'"
  check "pydub import" "\"$PYTHON_BIN\" -c 'import pydub'"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "doctor failed"
  exit 1
fi

echo "doctor passed"
