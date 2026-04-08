#!/bin/bash
# MultiCam Switcher standalone setup script
# Usage: sh setup.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SRC="$ROOT_DIR"
PLUGIN_DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-multicam"
VENV_DIR="$PLUGIN_DEST/venv"
REQ_FILE="$PLUGIN_SRC/requirements.lock.txt"

echo "=== MultiCam Switcher Standalone Setup ==="
echo "source: $PLUGIN_SRC"
echo "dest:   $PLUGIN_DEST"
echo ""

if [ ! -f "$REQ_FILE" ]; then
  echo "requirements.lock.txt が見つかりません: $REQ_FILE"
  exit 1
fi

echo "1. CEP extension directory"
mkdir -p "$HOME/Library/Application Support/Adobe/CEP/extensions"

echo "2. Install plugin files"
rm -rf "$PLUGIN_DEST"
mkdir -p "$PLUGIN_DEST"
cp -R "$PLUGIN_SRC/." "$PLUGIN_DEST/"
rm -rf "$PLUGIN_DEST/venv"

echo "3. Resolve Python 3.11 arm64"
PYTHON_BIN=""
for candidate in \
  "/opt/homebrew/bin/python3.11" \
  "$HOME/.pyenv/versions/3.11.9/bin/python3" \
  "python3.11"; do
  if command -v "$candidate" >/dev/null 2>&1; then
    arch_check=$("$candidate" -c "import platform; print(platform.machine())" 2>/dev/null || true)
    version_check=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
    if [ "$arch_check" = "arm64" ] && [ "$version_check" = "3.11" ]; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.11 arm64 が見つかりません。"
  echo "  brew install python@3.11"
  exit 1
fi

echo "Python: $("$PYTHON_BIN" --version 2>&1)"

echo "4. Create venv and install dependencies"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$REQ_FILE"
"$VENV_DIR/bin/pip" freeze > "$PLUGIN_DEST/pip-freeze.txt"

echo "5. Enable CEP debug mode"
defaults write com.adobe.CSXS.10 PlayerDebugMode 1 2>/dev/null || true
defaults write com.adobe.CSXS.11 PlayerDebugMode 1 2>/dev/null || true

echo ""
echo "Setup completed."
echo "Installed to: $PLUGIN_DEST"
echo "Next: restart Premiere Pro, then open Window > Extensions > MultiCam Switcher"
