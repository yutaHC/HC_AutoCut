#!/bin/bash
# AutoCut CEP プラグイン セットアップスクリプト
# 使い方: sh setup.sh

set -e

PLUGIN_SRC="$(cd "$(dirname "$0")/autocut-plugin" && pwd)"
PLUGIN_DEST="$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-autocut"

echo "=== AutoCut セットアップ ==="
echo ""

# 1. CEP 拡張ディレクトリを作成
echo "📁 CEP 拡張ディレクトリを作成中..."
mkdir -p "$HOME/Library/Application Support/Adobe/CEP/extensions"

# 2. プラグインをコピー（既存があれば上書き）
echo "📦 プラグインをインストール中..."
rm -rf "$PLUGIN_DEST"
cp -r "$PLUGIN_SRC" "$PLUGIN_DEST"
echo "   → $PLUGIN_DEST"

# 3. ffmpeg 確認 / インストール
echo ""
echo "🔧 ffmpeg を確認中..."
if command -v ffmpeg &>/dev/null; then
  echo "   → ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
else
  echo "   ffmpeg が見つかりません。Homebrew でインストールします..."
  if ! command -v brew &>/dev/null; then
    echo "   エラー: Homebrew が未インストールです。https://brew.sh からインストールしてください。"
    exit 1
  fi
  brew install ffmpeg
fi

# 4. Python venv 作成
echo ""
echo "🐍 Python 仮想環境を作成中..."
VENV_DIR="$PLUGIN_DEST/venv"

# Python 3.11+ を探す（mlx-whisper は ARM64 Python が必要）
PYTHON_BIN=""
for candidate in \
  "$HOME/mlx-venv/bin/python3" \
  "/opt/homebrew/bin/python3.13" \
  "/opt/homebrew/bin/python3.12" \
  "/opt/homebrew/bin/python3.11" \
  "/opt/homebrew/bin/python3" \
  "python3"; do
  if command -v "$candidate" &>/dev/null; then
    # ARM64 確認（mlx-whisper はApple Silicon専用）
    arch_check=$("$candidate" -c "import platform; print(platform.machine())" 2>/dev/null)
    if [ "$arch_check" = "arm64" ]; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "   エラー: ARM64 Python が見つかりません。"
  echo "   brew install python3 でインストールしてください。"
  exit 1
fi

echo "   Python: $PYTHON_BIN ($("$PYTHON_BIN" --version))"
"$PYTHON_BIN" -m venv "$VENV_DIR"
echo "   → venv 作成完了: $VENV_DIR"

# 5. 依存パッケージをインストール
echo ""
echo "📚 依存パッケージをインストール中..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

echo "   pydub..."
"$VENV_DIR/bin/pip" install pydub --quiet

echo "   anthropic..."
"$VENV_DIR/bin/pip" install anthropic --quiet

echo "   openai..."
"$VENV_DIR/bin/pip" install openai --quiet

echo "   mlx-whisper（初回はモデルDLで時間がかかります）..."
"$VENV_DIR/bin/pip" install mlx-whisper --quiet

echo "   → 依存パッケージ インストール完了"

# 6. CEP デバッグモードを有効化（Premiere がプラグインを読み込めるようにする）
echo ""
echo "🔓 CEP デバッグモードを有効化中..."
defaults write com.adobe.CSXS.10 PlayerDebugMode 1 2>/dev/null || true
defaults write com.adobe.CSXS.11 PlayerDebugMode 1 2>/dev/null || true
echo "   → 有効化完了"

# 7. 完了メッセージ
echo ""
echo "====================================="
echo "✅ セットアップ完了！"
echo "====================================="
echo ""
echo "次のステップ："
echo "1. Premiere Pro を再起動してください"
echo "2. ウィンドウ > エクステンション > AutoCut を開く"
echo "3. API キーを入力してカット編集を開始"
echo ""
echo "インストール先: $PLUGIN_DEST"
