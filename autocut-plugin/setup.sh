#!/bin/bash
# AutoCut standalone setup script
# Usage: sh setup.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SRC="$ROOT_DIR"
PLUGIN_DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-autocut"
VENV_DIR="$PLUGIN_DEST/venv"
REQ_FILE="$PLUGIN_SRC/requirements.lock.txt"

echo "=== AutoCut Standalone Setup ==="
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

echo "3. Check ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg が見つかりません。先に Homebrew で導入してください。"
  echo "  brew install ffmpeg"
  exit 1
fi
if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe が見つかりません。ffmpeg の導入状態を確認してください。"
  exit 1
fi

echo "4. Resolve Python 3.11 arm64"
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
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$REQ_FILE"
"$VENV_DIR/bin/pip" freeze > "$PLUGIN_DEST/pip-freeze.txt"

echo "5. Initialize personal rules template"
MY_RULES="${PLUGIN_DEST}/prompts/my_rules.md"
if [ ! -s "$MY_RULES" ]; then
  cat > "$MY_RULES" << 'TEMPLATE'
# 自分の追加ルール
#
# ここに自由に追記してください。
# チームテンプレートに上乗せして使用されます。
# 「チームに共有」ボタンでチームへのPR提案ができます。
#
# 例:
# - 「くらぶらして」はカット対象ワードに追加する
# - NG後に「もう一回いきます」が来るパターンは前後まとめてカット
TEMPLATE
  echo "✓ prompts/my_rules.md を初期化しました"
else
  echo "✓ prompts/my_rules.md は既存のものを保持しました"
fi

echo "6. Enable CEP debug mode"
defaults write com.adobe.CSXS.10 PlayerDebugMode 1 2>/dev/null || true
defaults write com.adobe.CSXS.11 PlayerDebugMode 1 2>/dev/null || true

echo ""
echo "Setup completed."
echo "Installed to: $PLUGIN_DEST"
echo "Run doctor:   cd \"$PLUGIN_DEST\" && sh doctor.sh"
echo "Next: restart Premiere Pro, then open Window > Extensions > AutoCut"
