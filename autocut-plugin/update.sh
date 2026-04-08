#!/bin/bash
# AutoCut アップデートスクリプト
# カットロジック（prompts/）と pipeline.py を GitHub から最新版に更新する。
# Usage: sh update.sh

set -euo pipefail

REPO="yutaHC/HC_AutoCut"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/autocut-plugin"
DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-autocut"

echo "=== AutoCut アップデート ==="

if [ ! -d "$DEST" ]; then
  echo "エラー: プラグインがインストールされていません。先に setup.sh を実行してください。"
  exit 1
fi

# pipeline.py / propose.py を取得
curl -fsSL "${BASE_URL}/python/pipeline.py" -o "${DEST}/python/pipeline.py"
echo "✓ pipeline.py"
curl -fsSL "${BASE_URL}/python/propose.py" -o "${DEST}/python/propose.py"
echo "✓ propose.py"

# UIファイルを取得
curl -fsSL "${BASE_URL}/index.html" -o "${DEST}/index.html"
echo "✓ index.html"
curl -fsSL "${BASE_URL}/main.js" -o "${DEST}/main.js"
echo "✓ main.js"

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

# my_rules.md は個人ファイルのため上書きしない
MY_RULES="${DEST}/prompts/my_rules.md"
if [ ! -f "$MY_RULES" ]; then
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
  echo "✓ prompts/my_rules.md（初期テンプレート作成）"
else
  echo "✓ prompts/my_rules.md（個人ルールを保持）"
fi

echo ""
echo "最新バージョン: ${LATEST}"
echo "アップデート完了。"
echo ""
echo "Premiere Pro パネルを右クリック → Reload してください。"
