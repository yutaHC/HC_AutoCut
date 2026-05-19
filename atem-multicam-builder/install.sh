#!/bin/bash
# ATEM Multicam Builder CEP パネル インストールスクリプト

set -e

EXTENSION_ID="com.haircamp.atem-multicam-builder"
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/$EXTENSION_ID"

echo "インストール先: $DEST"

# 既存があれば削除
if [ -L "$DEST" ] || [ -d "$DEST" ]; then
  rm -rf "$DEST"
  echo "既存を削除しました"
fi

# シンボリックリンク作成（開発時はリンクが便利）
ln -s "$SRC" "$DEST"
echo "シンボリックリンク作成: $SRC → $DEST"

# デバッグモード有効化（未署名の拡張機能を許可）
# Premiere Pro のバージョンに合わせて CSXS.11 等を変更する
for ver in 9 10 11 12 13; do
  defaults write "com.adobe.CSXS.$ver" PlayerDebugMode 1
done
echo "PlayerDebugMode を有効化しました"

echo ""
echo "✅ インストール完了"
echo "   Premiere Pro を再起動 → Window > Extensions > ATEM Multicam Builder"
