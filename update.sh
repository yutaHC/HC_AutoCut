#!/bin/bash
# AutoCut CEP プラグイン 差分アップデートスクリプト
# 使い方: sh update.sh

set -e

PLUGIN_SRC="$(cd "$(dirname "$0")/autocut-plugin" && pwd)"
PLUGIN_DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-autocut"

echo "=== AutoCut アップデート ==="
echo ""

# インストール済み確認
if [ ! -d "$PLUGIN_DEST" ]; then
  echo "エラー: プラグインがインストールされていません。先に sh setup.sh を実行してください。"
  exit 1
fi

# プラグインファイルをコピー
echo "📦 プラグインファイルを更新中..."
cp "$PLUGIN_SRC/python/pipeline.py" "$PLUGIN_DEST/python/pipeline.py"
cp "$PLUGIN_SRC/index.html"         "$PLUGIN_DEST/index.html"
cp "$PLUGIN_SRC/main.js"            "$PLUGIN_DEST/main.js"
cp "$PLUGIN_SRC/jsx/hostscript.jsx" "$PLUGIN_DEST/jsx/hostscript.jsx"
echo "   → ファイル更新完了"

# 不足パッケージを追加インストール
echo ""
echo "📚 パッケージを確認中..."
VENV_PIP="$PLUGIN_DEST/venv/bin/pip"

if ! "$VENV_PIP" show openai &>/dev/null; then
  echo "   openai をインストール中..."
  "$VENV_PIP" install openai --quiet
  echo "   → openai インストール完了"
else
  echo "   → openai: インストール済み（スキップ）"
fi

echo ""
echo "====================================="
echo "✅ アップデート完了！"
echo "====================================="
echo ""
echo "Premiere Pro のパネルを右クリック → Reload してください。"
echo ""
