#!/bin/bash
# AutoCut アップデートスクリプト
# カットロジック（prompts/）と pipeline.py を GitHub から最新版に更新する。
# Usage: sh update.sh

set -euo pipefail

REPO="yutaHC/HC_AutoCut"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-autocut"

echo "=== AutoCut アップデート ==="

if [ ! -d "$DEST" ]; then
  echo "エラー: プラグインがインストールされていません。先に setup.sh を実行してください。"
  exit 1
fi

# pipeline.py を取得
curl -fsSL "${BASE_URL}/python/pipeline.py" -o "${DEST}/python/pipeline.py"
echo "✓ pipeline.py"

# versions.txt を取得
curl -fsSL "${BASE_URL}/prompts/versions.txt" -o /tmp/autocut_versions.txt
echo "✓ versions.txt"

# 全バージョンのプロンプトを取得
mkdir -p "${DEST}/prompts"
while IFS= read -r version; do
  version=$(echo "$version" | tr -d '[:space:]')
  [ -z "$version" ] && continue
  curl -fsSL "${BASE_URL}/prompts/cut_logic_${version}.md" \
    -o "${DEST}/prompts/cut_logic_${version}.md"
  echo "✓ prompts/cut_logic_${version}.md"
done < /tmp/autocut_versions.txt

LATEST=$(grep -v '^\s*$' /tmp/autocut_versions.txt | tail -1 | tr -d '[:space:]')
echo ""
echo "最新バージョン: ${LATEST}"
echo "アップデート完了。"
echo ""
echo "Premiere Pro パネルを右クリック → Reload してください。"
