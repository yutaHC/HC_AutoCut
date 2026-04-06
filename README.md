# HC AutoCut

Adobe Premiere Pro 向けの AutoCut CEP プラグインです。

このリポジトリで実際に使う中心は [`autocut-plugin/`](/Users/yoshijimayuuta/Documents/claude/haircamp/video/Adobe_Premiere_Pro_MCP/autocut-plugin) です。  
GitHub ではこのフォルダ単体を公開物として扱えるように整理しています。

## Main Folder

```text
autocut-plugin/
├── .gitignore
├── README.md
├── setup.sh
├── doctor.sh
├── requirements.lock.txt
├── index.html
├── main.js
├── CSInterface.js
├── CSXS/manifest.xml
├── jsx/hostscript.jsx
└── python/pipeline.py
```

## What It Does

- Premiere Pro の CEP パネルとして動作
- シーケンスからクリップ情報を取得
- Python パイプラインで音声抽出と文字起こしを実行
- 無音区間と内容解析からカット候補を作成
- Premiere Pro 上に AutoCut 済みシーケンスを生成

## Requirements

- macOS
- Apple Silicon
- Adobe Premiere Pro
- Python `3.11` arm64
- `ffmpeg` / `ffprobe`
- Anthropic または OpenAI の API キー

## Setup

```bash
cd autocut-plugin
sh setup.sh
```

インストール後の確認:

```bash
cd "$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-autocut"
sh doctor.sh
```

## GitHub Notes

- `autocut-plugin/.gitignore` でローカル生成物を除外しています
- `venv/`, `output/`, `data.csv`, `pip-freeze.txt` はコミット対象外です
- 公開時は `autocut-plugin` を主対象として見る想定です

詳細は [`autocut-plugin/README.md`](/Users/yoshijimayuuta/Documents/claude/haircamp/video/Adobe_Premiere_Pro_MCP/autocut-plugin/README.md) を参照してください。
