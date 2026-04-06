#!/usr/bin/env python3
"""
auto_cut.py - AI自動カット前処理ツール

動画ファイルを受け取り、AI解析によりカット区間を特定して
SRTファイルとコンテンツカットJSONを出力する。
出力は apply_cuts.py に渡して Premiere Pro で使用する。

Usage:
    python3 auto_cut.py <video_path> [options]

Options:
    --mode jet|standard|natural|custom  無音カットモード (default: standard)
    --threshold FLOAT                   custom モード時の無音閾値(秒)
    --prompt TEXT                       LLMへの追加編集指示
    --output-dir PATH                   出力先ディレクトリ (default: 動画と同じ場所)
    --dry-run                           カット候補表示のみ（Premiereへの適用なし）
    --no-llm                            LLM解析をスキップ（SRT出力のみ）
"""

import os

# OpenMP競合回避（faster-whisperを使う前に必ず設定）
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ============================================================
# セクション 1: 定数
# ============================================================

THRESHOLDS = {
    "jet": 0.3,
    "standard": 0.8,
    "natural": 1.5,
}

SILENCE_THRESH_DB = -40   # 無音と判定するdBFS閾値
SNAP_RANGE = 0.5          # スナップ処理の最大距離（秒）
APPLY_CUTS_PATH = "/Users/yoshijimayuuta/claude-skills/premiere-cut-edit/apply_cuts.py"


# ============================================================
# セクション 2: Phase 1 - 音声抽出・無音区間検出
# ============================================================

def extract_audio(video_path: str, output_wav_path: str) -> None:
    """FFmpegで音声をモノラル16kHz WAVに抽出する。"""
    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg が見つかりません。インストールしてください。", file=sys.stderr)
        sys.exit(1)

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_wav_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: 音声抽出失敗\n{result.stderr}", file=sys.stderr)
        sys.exit(1)


def get_video_duration(video_path: str) -> float:
    """動画の長さを秒で取得する。"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    return 0.0


def detect_silence_intervals(wav_path: str, min_silence_ms: int = 300,
                              silence_thresh_db: int = SILENCE_THRESH_DB) -> list:
    """
    pydubで無音区間を検出する。
    戻り値: [{"start": 秒, "end": 秒}, ...]
    """
    try:
        from pydub import AudioSegment
        from pydub.silence import detect_silence as pydub_detect_silence
    except ImportError:
        print("Warning: pydub が未インストール。無音区間スナップをスキップします。")
        print("  インストール: pip install pydub")
        return []

    audio = AudioSegment.from_wav(wav_path)
    silences_ms = pydub_detect_silence(
        audio,
        min_silence_len=min_silence_ms,
        silence_thresh=silence_thresh_db
    )
    return [{"start": s / 1000.0, "end": e / 1000.0} for s, e in silences_ms]


# ============================================================
# セクション 3: Phase 2 - 文字起こし・SRT生成
# ============================================================

def _parse_segments(segments_raw: list) -> list:
    """セグメントリストを共通フォーマットに変換する。"""
    result = []
    for seg in segments_raw:
        # dict形式（mlx-whisper）とオブジェクト形式（faster-whisper）の両対応
        if isinstance(seg, dict):
            start = seg["start"]
            end = seg["end"]
            text = seg["text"].strip()
            words = [
                {"word": w["word"], "start": w["start"], "end": w["end"]}
                for w in seg.get("words", [])
            ]
        else:
            start = seg.start
            end = seg.end
            text = seg.text.strip()
            words = [
                {"word": w.word, "start": w.start, "end": w.end}
                for w in (seg.words or [])
            ]
        result.append({"start": start, "end": end, "text": text, "words": words})
        m, s = divmod(int(start), 60)
        h, m = divmod(m, 60)
        print(f"  [{h:02d}:{m:02d}:{s:02d}] {text}")
    return result


def _transcribe_mlx(wav_path: str) -> list:
    """mlx-whisperで文字起こし（Apple Silicon GPU使用・高速）。"""
    import mlx_whisper
    model_repo = "mlx-community/whisper-medium-mlx"
    print(f"  エンジン: mlx-whisper（{model_repo}）")
    output = mlx_whisper.transcribe(
        wav_path,
        path_or_hf_repo=model_repo,
        language="ja",
        word_timestamps=True,
    )
    return _parse_segments(output.get("segments", []))


def _transcribe_faster_whisper(wav_path: str) -> list:
    """faster-whisperで文字起こし（CPU使用・フォールバック）。"""
    from faster_whisper import WhisperModel
    print("  エンジン: faster-whisper（CPU/int8）")
    model = WhisperModel("medium", device="cpu", compute_type="int8")
    segments_gen, _ = model.transcribe(
        wav_path,
        language="ja",
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}
    )
    return _parse_segments(list(segments_gen))


def transcribe_to_segments(wav_path: str) -> list:
    """
    文字起こしを行い、セグメントリストを返す。
    mlx-whisper（Apple Silicon GPU）のみ使用。
    戻り値: [{"start": float, "end": float, "text": str, "words": [...]}, ...]
    """
    print("  文字起こし開始...")
    start_time = time.time()

    try:
        result = _transcribe_mlx(wav_path)
    except ImportError:
        print("Error: mlx-whisper が未インストールです。", file=sys.stderr)
        print("  インストール: ~/mlx-venv/bin/pip install mlx-whisper", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: mlx-whisper 失敗（{type(e).__name__}: {e}）", file=sys.stderr)
        sys.exit(1)

    elapsed = time.time() - start_time
    print(f"  → 完了 セグメント: {len(result)}件 / 所要時間: {elapsed:.0f}秒")
    return result


def format_srt_time(seconds: float) -> str:
    """秒を SRT タイムコード形式（HH:MM:SS,mmm）に変換する。"""
    ms = int(round((seconds % 1) * 1000))
    total_s = int(seconds)
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list, output_path: str) -> Path:
    """
    セグメントリストをSRTファイルとして書き出す。
    apply_cuts.py の parse_srt() と互換のフォーマット。
    """
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{format_srt_time(seg['start'])} --> {format_srt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")

    output = Path(output_path)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


# ============================================================
# セクション 4: Phase 3 - Claude API によるコンテンツ解析
# ============================================================

def build_claude_prompt(segments: list, user_prompt: str = "") -> tuple:
    """
    Claude API 呼び出し用のシステムプロンプトとユーザーメッセージを生成する。
    戻り値: (system_prompt, user_message)
    """
    system_prompt = """あなたは動画編集の専門家です。文字起こしデータを分析して、カットすべき区間を特定してください。

以下の基準でカット候補を検出してください：
- フィラーワード（えー、あのー、うーん、まぁ等）の連続・不要な間投詞
- 言い間違い・言い直し（前のテイクをカット、最後の言い直しを残す）
- 内容が重複している箇所（後のテイクを正として前を削除）
- 文が途中で止まり次のセグメントで言い直している箇所
- ユーザーの追加指示がある場合はそれにも従う

カット判断のルール：
- 確実にカットすべきものだけを選ぶ（迷う場合はスキップ）
- start/end はセグメントのタイムスタンプをそのまま使う
- カット後に文脈が自然につながるか確認する

出力形式：JSONのみ（前後の説明文なし）
[
  {"start": 開始秒（float）, "end": 終了秒（float）, "desc": "カット理由（日本語）"},
  ...
]
カット候補がない場合は空配列 [] を返す。"""

    transcript_lines = [
        f"[{seg['start']:.3f}s - {seg['end']:.3f}s] {seg['text']}"
        for seg in segments
    ]
    transcript_text = "\n".join(transcript_lines)

    user_message = f"文字起こしデータ：\n{transcript_text}"
    if user_prompt:
        user_message += f"\n\n追加の編集指示：\n{user_prompt}"
    else:
        user_message += "\n\nフィラーワード、言い間違い、内容重複を検出してください。"

    return system_prompt, user_message


def analyze_content_cuts(segments: list, user_prompt: str = "") -> list:
    """
    Claude API でトランスクリプトを解析し、コンテンツカット候補を返す。
    戻り値: [{"start": float, "end": float, "desc": str}, ...]
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Warning: ANTHROPIC_API_KEY が未設定です。--no-llm を使用するか環境変数を設定してください。",
              file=sys.stderr)
        return []

    try:
        import anthropic
    except ImportError:
        print("Error: anthropic が未インストールです。", file=sys.stderr)
        print("  インストール: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt, user_message = build_claude_prompt(segments, user_prompt)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    raw = response.content[0].text.strip()

    # JSONを抽出（コードブロックが含まれる場合も対応）
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        print("Warning: LLMレスポンスからJSONを抽出できませんでした。", file=sys.stderr)
        print(f"  レスポンス: {raw[:200]}", file=sys.stderr)
        return []

    try:
        cuts = json.loads(match.group())
        # 型検証
        validated = []
        for c in cuts:
            if isinstance(c, dict) and "start" in c and "end" in c:
                validated.append({
                    "start": float(c["start"]),
                    "end": float(c["end"]),
                    "desc": str(c.get("desc", ""))
                })
        return validated
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Warning: JSONパース失敗: {e}", file=sys.stderr)
        return []


# ============================================================
# セクション 5: Phase 4 - 無音区間へのスナップ
# ============================================================

def snap_to_silence(cut_point: float, silence_intervals: list,
                    max_snap_distance: float = SNAP_RANGE) -> tuple:
    """
    カット点を最寄りの無音区間境界にスナップする。
    戻り値: (スナップ後の値, スナップしたか)
    """
    if not silence_intervals:
        return round(cut_point, 3), False

    best_point = cut_point
    best_dist = float("inf")

    for interval in silence_intervals:
        # 無音区間の開始点（発話の直後）
        dist_start = abs(interval["start"] - cut_point)
        if dist_start < best_dist:
            best_dist = dist_start
            best_point = interval["start"]

        # 無音区間の終了点（発話の直前）
        dist_end = abs(interval["end"] - cut_point)
        if dist_end < best_dist:
            best_dist = dist_end
            best_point = interval["end"]

    if best_dist <= max_snap_distance:
        return round(best_point, 3), True
    return round(cut_point, 3), False


def snap_content_cuts(content_cuts: list, silence_intervals: list,
                      max_snap: float = SNAP_RANGE) -> list:
    """
    コンテンツカット一覧の start/end を無音区間境界にスナップする。
    """
    if not silence_intervals:
        return content_cuts

    result = []
    for cut in content_cuts:
        new_start, snapped_s = snap_to_silence(cut["start"], silence_intervals, max_snap)
        new_end, snapped_e = snap_to_silence(cut["end"], silence_intervals, max_snap)

        if snapped_s:
            delta = new_start - cut["start"]
            print(f"  [スナップ] start {cut['start']:.3f}s → {new_start:.3f}s (Δ{delta:+.3f}s)")
        if snapped_e:
            delta = new_end - cut["end"]
            print(f"  [スナップ] end   {cut['end']:.3f}s → {new_end:.3f}s (Δ{delta:+.3f}s)")

        if new_end > new_start:
            result.append({
                "start": new_start,
                "end": new_end,
                "desc": cut["desc"]
            })
        else:
            print(f"  [スキップ] スナップ後に start >= end となるためスキップ: {cut['desc']}")

    return result


# ============================================================
# セクション 6: 出力・コマンド表示
# ============================================================

def write_cuts_json(content_cuts: list, output_path: str) -> Path:
    """コンテンツカットを --content-cuts 形式の JSON ファイルに書き出す。"""
    output = Path(output_path)
    with output.open("w", encoding="utf-8") as f:
        json.dump(content_cuts, f, ensure_ascii=False, indent=2)
    return output


def print_apply_command(srt_path: str, content_cuts: list, mode: str,
                        threshold: float) -> None:
    """apply_cuts.py の実行コマンドをターミナルに表示する。"""
    print("\n" + "=" * 60)
    print("apply_cuts.py 実行コマンド")
    print("=" * 60)

    cmd = f"echo 'y' | python3 {APPLY_CUTS_PATH} \\\n  {srt_path} \\\n  --mode {mode}"

    if mode == "custom":
        cmd += f" \\\n  --threshold {threshold}"

    if content_cuts:
        cuts_json = json.dumps(content_cuts, ensure_ascii=False)
        cmd += f" \\\n  --content-cuts '{cuts_json}'"

    print(cmd)
    print()
    print("# 注意: タイムコードは SRT の元位置ベースです。")
    print("# 無音カットを先に適用した場合はタイムコードのずれが生じます。")
    print("=" * 60)


def print_dry_run_report(silence_intervals: list, content_cuts: list,
                         threshold: float) -> None:
    """ドライランモードの結果を表示する。"""
    silence_cuts = [s for s in silence_intervals
                    if (s["end"] - s["start"]) >= threshold]

    print("\n=== DRY RUN: カット候補 ===")
    print(f"\n【無音カット】{len(silence_cuts)}箇所（閾値: {threshold}秒以上）")
    for s in silence_cuts:
        dur = s["end"] - s["start"]
        print(f"  {s['start']:.3f}s → {s['end']:.3f}s  ({dur:.2f}秒)")

    print(f"\n【コンテンツカット】{len(content_cuts)}箇所")
    for c in content_cuts:
        dur = c["end"] - c["start"]
        print(f"  {c['start']:.3f}s → {c['end']:.3f}s  ({dur:.2f}秒)  {c['desc']}")


# ============================================================
# セクション 7: main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="AI自動カット前処理ツール - 動画→SRT+カットJSON生成",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("video_path", help="動画ファイルのパス")
    parser.add_argument(
        "--mode",
        choices=["jet", "standard", "natural", "custom"],
        default="standard",
        help="無音カットモード (default: standard)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        help="customモード時の無音閾値(秒)"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="",
        help="LLMへの追加編集指示"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="出力先ディレクトリ (default: 動画と同じ場所)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="カット候補表示のみ（ファイル出力なし）"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="LLM解析をスキップ（SRT出力のみ）"
    )
    args = parser.parse_args()

    # 閾値を決定
    if args.mode == "custom":
        if args.threshold is None:
            print("Error: --mode custom には --threshold が必要です。", file=sys.stderr)
            sys.exit(1)
        threshold = args.threshold
    else:
        threshold = THRESHOLDS[args.mode]
    min_silence_ms = int(threshold * 1000)

    # 入力ファイルの確認
    video_path = Path(args.video_path)
    if not video_path.exists():
        print(f"Error: ファイルが見つかりません: {video_path}", file=sys.stderr)
        sys.exit(1)

    # 出力先の決定
    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = video_path.parent

    stem = video_path.stem
    srt_path = output_dir / f"{stem}.srt"
    cuts_json_path = output_dir / f"{stem}_cuts.json"

    # ヘッダー表示
    print("=" * 60)
    print("auto_cut.py - AI自動カット前処理")
    print("=" * 60)
    print(f"動画    : {video_path}")
    print(f"モード  : {args.mode}  閾値: {threshold}秒")
    print(f"出力先  : {output_dir}/")
    if args.prompt:
        print(f"指示    : {args.prompt}")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")

        # Phase 1-A: 音声抽出
        print("[Phase 1] 音声抽出・無音区間検出...")
        extract_audio(str(video_path), wav_path)

        duration = get_video_duration(str(video_path))
        if duration > 0:
            h, r = divmod(int(duration), 3600)
            m, s = divmod(r, 60)
            print(f"  → 音声抽出完了 (動画尺: {h:02d}:{m:02d}:{s:02d})")

        # Phase 1-B: 無音区間検出
        silence_intervals = detect_silence_intervals(wav_path, min_silence_ms)
        print(f"  → 無音区間: {len(silence_intervals)}箇所 検出")

        # Phase 2-A: 文字起こし
        print("\n[Phase 2] 文字起こし（faster-whisper medium）...")
        segments = transcribe_to_segments(wav_path)

        # Phase 2-B: SRT書き出し
        if not args.dry_run:
            write_srt(segments, str(srt_path))
            print(f"  → SRT出力: {srt_path}")

        # Phase 3: LLM解析
        content_cuts = []
        if not args.no_llm:
            print("\n[Phase 3] Claude APIでコンテンツ解析中...")
            content_cuts = analyze_content_cuts(segments, args.prompt)
            print(f"  → コンテンツカット: {len(content_cuts)}件 検出")

            # Phase 4: スナップ処理
            if content_cuts and silence_intervals:
                print("\n[Phase 4] 無音区間にスナップ中...")
                content_cuts = snap_content_cuts(content_cuts, silence_intervals)

        # ドライランモード
        if args.dry_run:
            print_dry_run_report(silence_intervals, content_cuts, threshold)
            return

        # JSON書き出し（コンテンツカットがある場合）
        if content_cuts:
            write_cuts_json(content_cuts, str(cuts_json_path))
            print(f"  → JSON出力: {cuts_json_path}")

        # 実行コマンド表示
        print_apply_command(str(srt_path), content_cuts, args.mode, threshold)


if __name__ == "__main__":
    main()
