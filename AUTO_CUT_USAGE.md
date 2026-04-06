# auto_cut.py 使用マニュアル

動画ファイルを入力し、AI解析でカット区間を特定。SRT + カットJSONを生成して `apply_cuts.py` に渡す前処理ツール。

---

## 事前準備（初回のみ）

```bash
# FFmpeg
brew install ffmpeg

# Pythonパッケージ
pyenv shell 3.9.0
pip install -r requirements_auto_cut.txt
```

`~/.zshrc` に追加：
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## 基本の流れ

```
動画ファイル
    ↓ auto_cut.py
  video.srt（文字起こし）
  video_cuts.json（コンテンツカット）
  実行コマンドをターミナルに表示
    ↓ 表示されたコマンドをコピーして実行
  apply_cuts.py → Premiere Pro に適用
```

---

## 実行コマンド

```bash
pyenv shell 3.9.0 && python3 auto_cut.py /path/to/video.mp4 --mode standard
```

実行後、ターミナルに `apply_cuts.py` のコマンドが表示されるのでそのままコピー実行する。

---

## オプション

| オプション | 説明 | 例 |
|---|---|---|
| `--mode` | 無音カット閾値モード | `jet`(0.3s) / `standard`(0.8s) / `natural`(1.5s) / `custom` |
| `--threshold` | custom モード時の閾値(秒) | `--threshold 1.0` |
| `--prompt` | LLMへの追加編集指示 | `--prompt "えーあのをカットして"` |
| `--output-dir` | 出力先ディレクトリ | `--output-dir /path/to/dir` |
| `--dry-run` | カット候補確認のみ（Premiereに適用しない） | `--dry-run` |
| `--no-llm` | LLM解析スキップ（SRT出力のみ） | `--no-llm` |

---

## 用途別コマンド例

```bash
# テンポ重視（YouTubeジェットカット）
python3 auto_cut.py video.mp4 --mode jet

# 自然な喋り（長尺コンテンツ向け）
python3 auto_cut.py video.mp4 --mode natural

# フィラーワードだけ除去
python3 auto_cut.py video.mp4 --prompt "えー・あのー・うーん等のフィラーワードのみカット"

# まず内容確認してから適用
python3 auto_cut.py video.mp4 --dry-run

# SRTだけ欲しい（AI解析不要）
python3 auto_cut.py video.mp4 --no-llm
```

---

## 出力ファイル

| ファイル | 内容 |
|---|---|
| `{stem}.srt` | 文字起こしSRT（apply_cuts.py に渡す） |
| `{stem}_cuts.json` | コンテンツカット一覧（`--content-cuts` 形式） |

---

## Premiere適用手順

1. `Window > Extensions > MCP Bridge (CEP)` を開いて `Start Bridge`
2. ターミナルに表示された `apply_cuts.py` コマンドを実行

```bash
echo 'y' | python3 /Users/yoshijimayuuta/claude-skills/premiere-cut-edit/apply_cuts.py \
  /path/to/video.srt \
  --mode standard \
  --content-cuts '[{"start":23.82,"end":37.49,"desc":"言い間違い"}]'
```

> **注意**: 無音カットを先に適用した場合、コンテンツカットのタイムコードがずれる。
> その場合は `compute_cuts_float.py` で補正が必要。

---

## ファイル場所

```
Adobe_Premiere_Pro_MCP/
├── auto_cut.py               # このツール
├── requirements_auto_cut.txt # 依存パッケージ
└── AUTO_CUT_USAGE.md         # このマニュアル

/Users/yoshijimayuuta/claude-skills/premiere-cut-edit/
└── apply_cuts.py             # Premiere適用ツール（auto_cut.pyの出力を受け取る）
```
