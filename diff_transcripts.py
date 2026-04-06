#!/usr/bin/env python3
"""
diff_transcripts.py - カット前後の動画を比較してカット差分データを生成する

Usage:
    # CSVから一括処理
    python3 diff_transcripts.py --csv autocut-plugin/data.csv

    # 個別ファイルを指定
    python3 diff_transcripts.py before.mp4 after.mp4

    # 既存SRTを使用（転写スキップ）
    python3 diff_transcripts.py before.mp4 after.mp4 \\
        --before-srt before.srt --after-srt after.srt

    # 結果を確認するだけ（JSON保存しない）
    python3 diff_transcripts.py before.mp4 after.mp4 --dry-run
"""

import argparse
import csv
import difflib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

# ============================================================
# 定数
# ============================================================

MLX_PYTHON = os.path.expanduser("~/mlx-venv/bin/python")
AUTO_CUT_PY = os.path.expanduser(
    "~/Documents/claude/haircamp/video/Adobe_Premiere_Pro_MCP/auto_cut.py"
)

MIN_GAP = 0.1          # 最小ギャップ（秒）- これ以下のカットは除外
MATCH_THRESHOLD = 0.75  # テキスト類似度の閾値


# ============================================================
# SRTパース
# ============================================================

def parse_srt(srt_path: str) -> list:
    """SRTファイルをパースして [{start, end, text}] を返す。"""
    pattern = re.compile(
        r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*'
        r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})'
    )
    segments = []
    with open(srt_path, encoding='utf-8-sig') as f:
        content = f.read()

    for block in re.split(r'\n\s*\n', content.strip()):
        lines = [l.rstrip('\r') for l in block.strip().split('\n')]
        if len(lines) < 2:
            continue

        tc_line = None
        for line in lines:
            if pattern.match(line):
                tc_line = line
                break
        if tc_line is None:
            continue

        m = pattern.match(tc_line)
        h1, m1, s1, ms1, h2, m2, s2, ms2 = [int(x) for x in m.groups()]
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end   = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000

        tc_idx = lines.index(tc_line)
        text = ' '.join(lines[tc_idx + 1:]).strip()

        if text:
            segments.append({'start': start, 'end': end, 'text': text})

    return segments


# ============================================================
# テキスト正規化
# ============================================================

def normalize(text: str) -> str:
    """比較用にテキストを正規化する（句読点・空白・大文字小文字を統一）。"""
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'[。、！？!?,.\s]', '', text)
    return text.lower().strip()


# ============================================================
# 転写（mlx-whisper）
# ============================================================

def transcribe(video_path: str, srt_path: str, output_dir: str) -> str:
    """
    auto_cut.py --no-llm でSRTを生成する。
    srt_path が既に存在すればスキップ。
    戻り値: 生成されたSRTのパス
    """
    if Path(srt_path).exists():
        print(f"  [skip] キャッシュあり: {Path(srt_path).name}")
        return srt_path

    print(f"  [転写] {Path(video_path).name} ...")
    cmd = [
        MLX_PYTHON, AUTO_CUT_PY,
        video_path,
        "--no-llm",
        "--output-dir", output_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"転写失敗:\n{result.stderr[-500:]}")

    # auto_cut.py が出力するSRT: {output_dir}/{stem}.srt
    stem = Path(video_path).stem
    generated = Path(output_dir) / f"{stem}.srt"
    if not generated.exists():
        raise RuntimeError(f"SRTが生成されませんでした: {generated}")

    # 指定のパスに移動（stem名が異なる場合）
    if str(generated) != srt_path:
        Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.move(str(generated), srt_path)

    return srt_path


# ============================================================
# アライメント
# ============================================================

def align_segments(before_segs: list, after_segs: list) -> set:
    """
    カット前後のセグメントをテキストでアライメントする。
    戻り値: カット前でマッチしたインデックスの集合
    """
    before_texts = [normalize(s['text']) for s in before_segs]
    after_texts  = [normalize(s['text']) for s in after_segs]

    matcher = difflib.SequenceMatcher(None, before_texts, after_texts, autojunk=False)

    matched = set()
    for block in matcher.get_matching_blocks():
        bi, ai, size = block
        if size == 0:
            continue
        for offset in range(size):
            b_text = before_texts[bi + offset]
            a_text = after_texts[ai + offset]
            # 閾値チェック（短いテキストは単純一致でも通過）
            ratio = difflib.SequenceMatcher(None, b_text, a_text).ratio()
            if ratio >= MATCH_THRESHOLD or b_text == a_text:
                matched.add(bi + offset)

    return matched


# ============================================================
# カット検出
# ============================================================

def detect_cuts(before_segs: list, matched_indices: set) -> list:
    """
    マッチしなかったセグメントを連続グループ化してカット区間を返す。
    戻り値: [{"start", "end", "text", "cut_type"}]
    """
    cuts = []
    gap_start = None
    gap_end = None
    gap_texts = []

    for i, seg in enumerate(before_segs):
        if i not in matched_indices:
            # カットされたセグメント
            if gap_start is None:
                gap_start = seg['start']
            gap_end = seg['end']
            gap_texts.append(seg['text'])
        else:
            # 残ったセグメント → 手前のギャップを確定
            if gap_start is not None:
                _append_cut(cuts, gap_start, gap_end, gap_texts)
                gap_start = None
                gap_end = None
                gap_texts = []

    # 末尾のギャップ
    if gap_start is not None:
        _append_cut(cuts, gap_start, gap_end, gap_texts)

    return cuts


def _append_cut(cuts: list, start: float, end: float, texts: list):
    if end - start < MIN_GAP:
        return
    combined_text = ' '.join(texts).strip()
    cut_type = 'content' if combined_text else 'silence'
    cuts.append({
        'start':    round(start, 3),
        'end':      round(end, 3),
        'text':     combined_text,
        'cut_type': cut_type,
    })


# ============================================================
# 1ペア処理
# ============================================================

def process_pair(
    input_path: str,
    output_path: str,
    output_dir: str,
    before_srt: str = None,
    after_srt: str = None,
) -> list:
    """カット前後の動画1ペアを処理してカット差分リストを返す。"""
    input_stem  = Path(input_path).stem
    output_stem = Path(output_path).stem

    # SRTのキャッシュパス
    before_srt_path = before_srt or str(Path(output_dir) / f"{input_stem}_input.srt")
    after_srt_path  = after_srt  or str(Path(output_dir) / f"{output_stem}_output.srt")

    # 転写
    print(f"\n[1/4] 転写: input")
    transcribe(input_path, before_srt_path, output_dir)

    print(f"[2/4] 転写: output")
    transcribe(output_path, after_srt_path, output_dir)

    # パース
    print(f"[3/4] SRTパース")
    before_segs = parse_srt(before_srt_path)
    after_segs  = parse_srt(after_srt_path)
    print(f"  input:  {len(before_segs)} segments")
    print(f"  output: {len(after_segs)} segments")

    # アライメント & カット検出
    print(f"[4/4] アライメント & カット検出")
    matched = align_segments(before_segs, after_segs)
    cuts = detect_cuts(before_segs, matched)

    content_cuts = [c for c in cuts if c['cut_type'] == 'content']
    silence_cuts = [c for c in cuts if c['cut_type'] == 'silence']
    total_sec = sum(c['end'] - c['start'] for c in cuts)
    print(f"  content: {len(content_cuts)}箇所")
    print(f"  silence: {len(silence_cuts)}箇所")
    print(f"  合計削減: {total_sec:.1f}秒")

    return cuts


# ============================================================
# main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="カット前後の動画を比較してカット差分データを生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--csv",        help="ペアリストのCSVファイル（input,output列）")
    parser.add_argument("before",       nargs="?", help="カット前の動画またはSRT")
    parser.add_argument("after",        nargs="?", help="カット後の動画またはSRT")
    parser.add_argument("--before-srt", help="カット前の既存SRT（転写スキップ）")
    parser.add_argument("--after-srt",  help="カット後の既存SRT（転写スキップ）")
    parser.add_argument("--output",     help="出力JSONパス（単体処理時）")
    parser.add_argument("--output-dir", help="出力ディレクトリ（デフォルト: CSVと同じ場所/output/）")
    parser.add_argument("--dry-run",    action="store_true", help="JSONを保存せずstdoutに出力")
    args = parser.parse_args()

    # ペアリスト作成
    pairs = []

    if args.csv:
        csv_path = Path(args.csv)
        output_dir = args.output_dir or str(csv_path.parent / "output")
        with open(csv_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                pairs.append((row['input'].strip(), row['output'].strip()))

    elif args.before and args.after:
        output_dir = args.output_dir or str(Path(args.before).parent)
        pairs.append((args.before, args.after))

    else:
        parser.print_help()
        sys.exit(1)

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 処理
    for i, (input_path, output_path) in enumerate(pairs, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(pairs)}]")
        print(f"  input:  {input_path}")
        print(f"  output: {output_path}")
        print('='*60)

        output_stem = Path(output_path).stem
        json_path = args.output or str(Path(output_dir) / f"{output_stem}_diff.json")

        cuts = process_pair(
            input_path, output_path, output_dir,
            before_srt=args.before_srt,
            after_srt=args.after_srt,
        )

        if args.dry_run:
            print(json.dumps(cuts, ensure_ascii=False, indent=2))
        else:
            Path(json_path).write_text(
                json.dumps(cuts, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            print(f"\n  → {json_path}")


if __name__ == "__main__":
    main()
