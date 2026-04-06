# AutoCut Standalone

このフォルダは AutoCut CEP プラグインを 1 フォルダで扱うための最小構成です。

含まれるもの:
- `index.html`, `main.js`, `CSInterface.js`
- `CSXS/manifest.xml`
- `jsx/hostscript.jsx`
- `python/pipeline.py`
- `setup.sh`
- `doctor.sh`
- `requirements.lock.txt`
- `.gitignore`

前提:
- macOS
- Apple Silicon
- Python `3.11` arm64
- `ffmpeg` / `ffprobe`
- Adobe Premiere Pro

セットアップ:

```bash
cd /path/to/autocut-plugin
sh setup.sh
```

検証:

```bash
cd "$HOME/Library/Application Support/Adobe/CEP/extensions/haircamp-autocut"
sh doctor.sh
```

補足:
- Premiere Pro 本体と API キーはこのフォルダには含まれません。
- Python 依存は `requirements.lock.txt` で固定しています。
- セットアップ後の実インストール先には `pip-freeze.txt` も出力されます。

GitHub 公開時の扱い:
- この `autocut-plugin` フォルダ単体をリポジトリ化して問題ありません。
- `venv/`, `output/`, `data.csv`, `pip-freeze.txt`, `.DS_Store` は `.gitignore` で除外されます。
- 現在すでに存在している `output/` や `data.csv` を公開したくない場合は、コミット対象から外してください。
