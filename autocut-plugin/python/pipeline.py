#!/usr/bin/env python3
"""
pipeline.py - AutoCut CEPプラグイン用パイプライン

動画ファイルを受け取り、自動カット編集済みの FCP XML を出力する。
stdout に JSON Lines 形式でプログレスを出力し、CEP パネルがリアルタイムで読む。

Usage:
    python3 pipeline.py --video <path> --mode jet|standard|natural \
                        --api-key <key> --output <xml_path>
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"  # OpenMP競合回避

# CEP環境ではPATHが最小構成のため、Homebrewの標準パスを追加する
# mlx-whisperが内部でffmpegを呼び出すため、os.environへの追加が必要
for _brew_bin in ["/usr/local/bin", "/opt/homebrew/bin"]:
    if _brew_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _brew_bin + ":" + os.environ.get("PATH", "")

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# ============================================================
# 定数
# ============================================================

THRESHOLDS = {
    "jet":      0.3,
    "standard": 0.8,
    "natural":  1.5,
}

SILENCE_THRESH_DB = -40
BUFFER = 0.2      # カット両端のバッファ（秒）
MIN_CUT = 0.3     # 最小カット長（秒）


# ============================================================
# プログレス出力
# ============================================================

def emit(obj: dict):
    """JSON Lines 形式でプログレスを stdout に出力する。"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def progress(step: str, pct: int, msg: str):
    emit({"type": "progress", "step": step, "pct": pct, "msg": msg})


def done(xml_path: str, cuts: int, saved_sec: float, cut_regions: list = None, json_path: str = None):
    emit({"type": "done", "xml_path": xml_path, "cuts": cuts, "saved_sec": round(saved_sec, 1),
          "cut_regions": cut_regions or [], "json_path": json_path or ""})


def error(msg: str):
    emit({"type": "error", "message": msg})


# ============================================================
# Phase 1: 音声抽出
# ============================================================

def _find_bin(name: str) -> str:
    """CEP環境でもHomebrewのバイナリを見つける。"""
    # shutil.which が見つける場合はそのまま使う
    found = shutil.which(name)
    if found:
        return found
    # Homebrew の標準パスを直接探す
    for candidate in [f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"]:
        if os.path.isfile(candidate):
            return candidate
    return name  # 見つからなくてもコマンド名をそのまま返す（エラーは subprocess で出る）


def extract_audio(video_path: str, output_wav_path: str, start_sec: float = 0.0, duration_sec: float = None) -> None:
    """FFmpeg でモノラル 16kHz WAV に抽出する。start_sec/duration_sec で区間指定可能。"""
    ffmpeg_bin = _find_bin("ffmpeg")
    if not os.path.isfile(ffmpeg_bin) and not shutil.which(ffmpeg_bin):
        error("ffmpeg が見つかりません。brew install ffmpeg を実行してください。")
        sys.exit(1)

    cmd = [ffmpeg_bin, "-y"]
    if start_sec > 0.01:
        cmd += ["-ss", str(start_sec)]
    cmd += ["-i", video_path]
    if duration_sec is not None and duration_sec > 0:
        cmd += ["-t", str(duration_sec)]
    cmd += ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", output_wav_path]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error("音声抽出に失敗しました: " + result.stderr[-300:])
        sys.exit(1)


def get_video_duration(video_path: str) -> float:
    """ffprobe で動画の長さ（秒）を取得する。"""
    ffprobe_bin = _find_bin("ffprobe")
    cmd = [
        ffprobe_bin, "-v", "quiet",
        "-print_format", "json",
        "-show_format", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            pass
    return 0.0


# ============================================================
# Phase 2: 無音区間検出
# ============================================================

def detect_silence_intervals(wav_path: str, threshold_sec: float) -> list:
    """
    pydub で無音区間を検出する。
    戻り値: [{"start": 秒, "end": 秒}, ...]
    """
    try:
        from pydub import AudioSegment
        from pydub.silence import detect_silence as pydub_detect_silence
    except ImportError:
        return []

    min_silence_ms = max(int(threshold_sec * 1000), 100)
    audio = AudioSegment.from_wav(wav_path)
    silences_ms = pydub_detect_silence(
        audio,
        min_silence_len=min_silence_ms,
        silence_thresh=SILENCE_THRESH_DB,
    )
    return [{"start": s / 1000.0, "end": e / 1000.0} for s, e in silences_ms]


# ============================================================
# Phase 3: 文字起こし（mlx-whisper）
# ============================================================

def transcribe_to_segments(wav_path: str) -> list:
    """
    mlx-whisper で Apple Silicon GPU 文字起こしを行う。
    戻り値: [{"start": float, "end": float, "text": str}, ...]
    """
    try:
        import mlx_whisper
    except ImportError:
        error("mlx-whisper が未インストールです。setup.sh を実行してください。")
        sys.exit(1)

    output = mlx_whisper.transcribe(
        wav_path,
        path_or_hf_repo="mlx-community/whisper-medium-mlx",
        language="ja",
        word_timestamps=True,
    )

    segments = []
    for seg in output.get("segments", []):
        if isinstance(seg, dict):
            segments.append({
                "start": float(seg["start"]),
                "end":   float(seg["end"]),
                "text":  seg["text"].strip(),
            })
    return segments


def write_srt(segments: list, srt_path: str) -> None:
    """セグメントリストを SRT ファイルに書き出す。"""
    def fmt(sec: float) -> str:
        ms = int(round((sec % 1) * 1000))
        total_s = int(sec)
        s = total_s % 60
        m = (total_s // 60) % 60
        h = total_s // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{fmt(seg['start'])} --> {fmt(seg['end'])}", seg["text"], ""]
    Path(srt_path).write_text("\n".join(lines), encoding="utf-8")


def parse_srt(srt_path: str) -> list:
    """SRT ファイルをパースして [{start, end, text}] を返す。"""
    pattern = re.compile(
        r"(\d+):(\d+):(\d+)[,.](\d+)\s+-->\s+(\d+):(\d+):(\d+)[,.](\d+)"
    )
    segments = []
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()

    for block in content.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        m = None
        for line in lines:
            m = pattern.match(line.strip())
            if m:
                break
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
        start = int(h1)*3600 + int(m1)*60 + int(s1) + int(ms1)/1000
        end   = int(h2)*3600 + int(m2)*60 + int(s2) + int(ms2)/1000
        text_lines = [l for l in lines if not pattern.match(l.strip()) and not l.strip().isdigit()]
        text = " ".join(text_lines).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


# ============================================================
# Phase 4: LLM によるコンテンツ解析（Claude / OpenAI）
# ============================================================

SYSTEM_PROMPT = """あなたは動画編集の専門家です。セミナー・講義動画の文字起こしを分析し、
カットすべき区間を特定してください。この動画はカメラの前で1人が喋る収録形式です。

## カット対象パターン（優先度順）

### 1. 収録前の準備・段取り（最優先・大きなカット）
収録本番が始まる前の、スタッフとのやりとりや準備トークをカットする。
- 「もう一回」「最初から」「スタート」「ではお願いします」「チェックいきましょう」
  → これらの「本番開始合図」の前にあるセグメントは全てカット対象
- 台本の確認、目線の置き場の相談、撮影準備トーク
- 「このセクションなんでしたっけ」のような段取り確認
- NG確認後の再スタート前の無音・待機

### 2. 撮影中断・ハプニング
- 物理的な中断（「ちょっと待って」「お茶取っていいですか」「水飲んでいいですか」等）
- 撮影スタッフへの話しかけ（「大丈夫ですか」「つないでくれますか」等）
- 収録後の確認会話（「短すぎる？」「いい感じで」等）
  → 中断開始から再開（話の続き）までを1つのカット範囲にまとめる

### 3. 言い間違い・NGトリガー
以下のワードが出た場合、そのセグメントから言い直しの直前までをカット：
- 「ごめんなさい」「すいません」「噛みすぎ」「もう一回」「違う」「間違えた」
- ただし謝罪が本番内容の一部の場合（例：お客さんへの謝罪の話）は除外する

### 4. 前テイク全体のカット（重複検出）
- 同じセクションや同じ話題の説明が2回以上登場する場合
- 前テイクを全てカットし、最後のテイクのみ残す
- 「合ってます」「はい、いきます」などの確認ワードの前後でテイクが切り替わることが多い

### 5. 収録後のNG会話
- 本編の説明が終わった後の「ありがとうございます」「よかった」「くらぶらして」等

## カット判断のルール
- カット後に前後の発話が自然につながるか必ず確認する
- 関連する複数の問題は1つのカット範囲にまとめる（細切れにしない）
- 確実なものだけカット（迷う場合はスキップ）
- start は対象セグメントのstart、end は対象セグメントのendをそのまま使う

## 出力形式：JSONのみ（前後の説明文なし）
[
  {"start": 開始秒（float）, "end": 終了秒（float）, "desc": "カット理由（日本語）"},
  ...
]
カット候補がない場合は空配列 [] を返す。"""


def _parse_llm_response(raw: str) -> list:
    """LLMのレスポンスからカット候補リストをパースする。"""
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        return []
    cuts = json.loads(match.group())
    validated = []
    for c in cuts:
        if isinstance(c, dict) and "start" in c and "end" in c:
            validated.append({
                "start": float(c["start"]),
                "end":   float(c["end"]),
                "desc":  str(c.get("desc", "")),
            })
    return validated


def _build_transcript_text(segments: list) -> str:
    lines = [
        f"[{seg['start']:.3f}s - {seg['end']:.3f}s] {seg['text']}"
        for seg in segments
    ]
    return "\n".join(lines)


def analyze_content_cuts(segments: list, api_key: str, provider: str = "claude") -> list:
    """
    LLM でトランスクリプトを解析してカット候補を返す。
    provider: "claude" または "openai"
    エラー時は空リストを返して処理を継続する。
    戻り値: [{"start": float, "end": float, "desc": str}, ...]
    """
    transcript_text = _build_transcript_text(segments)
    user_content = (
        f"文字起こしデータ：\n{transcript_text}\n\n"
        "収録前の準備NG・撮影中断・言い間違い・前テイク・収録後NG会話を検出してください。"
        "特に「スタート」「ではお願いします」などの本番開始合図の前にある準備トークは必ずカットしてください。"
    )

    try:
        if provider == "openai":
            return _analyze_openai(api_key, user_content)
        else:
            return _analyze_claude(api_key, user_content)
    except Exception:
        return []


def _analyze_claude(api_key: str, user_content: str) -> list:
    try:
        import anthropic
    except ImportError:
        return []

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = response.content[0].text.strip()
    return _parse_llm_response(raw)


def _analyze_openai(api_key: str, user_content: str) -> list:
    try:
        from openai import OpenAI
    except ImportError:
        return []

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
    )
    raw = response.choices[0].message.content.strip()
    return _parse_llm_response(raw)


# ============================================================
# Phase 5: カット計算 → 残すセグメント計算
# ============================================================

def compute_silence_cuts(segments: list, threshold_sec: float) -> list:
    """
    SRT セグメント間のギャップから無音カット候補を計算する。
    戻り値: [{"start": 秒, "end": 秒}] （降順）
    """
    cuts = []
    for i in range(len(segments) - 1):
        seg_end   = segments[i]["end"]
        next_start = segments[i + 1]["start"]
        gap = next_start - seg_end
        if gap < threshold_sec:
            continue
        cut_start = seg_end + BUFFER
        cut_end   = next_start - BUFFER
        if cut_end - cut_start < MIN_CUT:
            continue
        cuts.append({"start": round(cut_start, 3), "end": round(cut_end, 3)})

    # 後ろから前の順（ripple削除のため）
    cuts.sort(key=lambda c: c["start"], reverse=True)
    return cuts


def merge_cuts(cut_list: list) -> list:
    """重複・重なるカット区間をマージして昇順リストを返す。"""
    if not cut_list:
        return []
    sorted_cuts = sorted(cut_list, key=lambda c: c["start"])
    merged = [sorted_cuts[0].copy()]
    for cur in sorted_cuts[1:]:
        prev = merged[-1]
        if cur["start"] <= prev["end"]:
            prev["end"] = max(prev["end"], cur["end"])
        else:
            merged.append(cur.copy())
    return merged


def compute_keep_segments(total_duration: float, all_cuts: list) -> list:
    """
    カット区間の逆転：残すセグメントのリストを計算する。
    戻り値: [(start_sec, end_sec), ...] 昇順
    """
    merged = merge_cuts(all_cuts)
    keeps = []
    pos = 0.0
    for cut in merged:
        if cut["start"] > pos + 0.01:
            keeps.append((round(pos, 3), round(cut["start"], 3)))
        pos = cut["end"]
    if total_duration - pos > 0.01:
        keeps.append((round(pos, 3), round(total_duration, 3)))
    return keeps


# ============================================================
# Phase 6: FCP XML 生成
# ============================================================

def _path_to_url(file_path: str) -> str:
    """ファイルパスを file:// URL に変換する。"""
    abs_path = str(Path(file_path).resolve())
    return "file://" + urllib.request.pathname2url(abs_path)


def generate_fcpxml(
    source_video_path: str,
    keep_segments: list,
    fps: float,
    total_source_frames: int,
    sequence_name: str,
) -> str:
    """
    FCP XML (xmeml v4) 文字列を生成する。
    keep_segments: [(start_sec, end_sec), ...] 昇順
    """
    timebase = max(1, round(fps))
    file_url = _path_to_url(source_video_path)
    file_name = Path(source_video_path).name

    # クリップアイテムを計算（フレーム単位）
    items = []
    timeline_pos = 0
    for i, (start_sec, end_sec) in enumerate(keep_segments):
        in_frame  = int(round(start_sec * fps))
        out_frame = int(round(end_sec * fps))
        dur       = out_frame - in_frame
        if dur <= 0:
            continue
        items.append({
            "id":    i + 1,
            "in":    in_frame,
            "out":   out_frame,
            "start": timeline_pos,
            "end":   timeline_pos + dur,
            "dur":   dur,
        })
        timeline_pos += dur

    total_timeline_frames = timeline_pos

    def rate_tag(tb: int) -> str:
        return f"<rate><timebase>{tb}</timebase><ntsc>FALSE</ntsc></rate>"

    def file_tag(item: dict) -> str:
        """<file> 要素（最初のアイテムのみ完全記述、以降は id参照）"""
        if item["id"] == 1:
            return f"""<file id="file-1">
              <name>{xml_escape(file_name)}</name>
              <pathurl>{xml_escape(file_url)}</pathurl>
              {rate_tag(timebase)}
              <duration>{total_source_frames}</duration>
            </file>"""
        else:
            return '<file id="file-1"/>'

    def video_clipitem(item: dict) -> str:
        return f"""          <clipitem id="clipitem-v{item['id']}">
            <name>{xml_escape(file_name)}</name>
            <duration>{item['dur']}</duration>
            {rate_tag(timebase)}
            <start>{item['start']}</start>
            <end>{item['end']}</end>
            <in>{item['in']}</in>
            <out>{item['out']}</out>
            {file_tag(item)}
          </clipitem>"""

    def audio_clipitem(item: dict, channel: int) -> str:
        return f"""          <clipitem id="clipitem-a{item['id']}-ch{channel}">
            <name>{xml_escape(file_name)}</name>
            <duration>{item['dur']}</duration>
            {rate_tag(timebase)}
            <start>{item['start']}</start>
            <end>{item['end']}</end>
            <in>{item['in']}</in>
            <out>{item['out']}</out>
            <file id="file-1"/>
            <sourcetrack>
              <mediatype>audio</mediatype>
              <trackindex>{channel}</trackindex>
            </sourcetrack>
          </clipitem>"""

    video_clips = "\n".join(video_clipitem(it) for it in items)
    audio_ch1   = "\n".join(audio_clipitem(it, 1) for it in items)
    audio_ch2   = "\n".join(audio_clipitem(it, 2) for it in items)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence id="sequence-1">
    <name>{xml_escape(sequence_name)}_AutoCut</name>
    <duration>{total_timeline_frames}</duration>
    {rate_tag(timebase)}
    <timecode>
      {rate_tag(timebase)}
      <string>00:00:00:00</string>
      <frame>0</frame>
      <displayformat>NDF</displayformat>
    </timecode>
    <media>
      <video>
        <format>
          <samplecharacteristics>
            {rate_tag(timebase)}
            <width>1920</width>
            <height>1080</height>
          </samplecharacteristics>
        </format>
        <track>
{video_clips}
        </track>
      </video>
      <audio>
        <numOutputChannels>2</numOutputChannels>
        <track>
{audio_ch1}
        </track>
        <track>
{audio_ch2}
        </track>
      </audio>
    </media>
  </sequence>
</xmeml>"""
    return xml


# ============================================================
# main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="AutoCut パイプライン")
    parser.add_argument("--video",        default=None,
                        help="入力動画ファイルパス（単一クリップ・後方互換）")
    parser.add_argument("--clips-json",   default=None,
                        help="クリップ情報JSON（複数クリップ対応）")
    parser.add_argument("--mode",         default="standard",
                        choices=["jet", "standard", "natural"],
                        help="無音カットモード")
    parser.add_argument("--api-key",      required=True,
                        help="LLM API キー（選択したプロバイダのキー）")
    parser.add_argument("--llm-provider", default="claude",
                        choices=["claude", "openai"],
                        help="使用するLLMプロバイダ（claude / openai）")
    parser.add_argument("--output",       default="/tmp/autocut_result.xml",
                        help="FCP XML 出力先")
    args = parser.parse_args()

    # クリップ情報を構築（--clips-json 優先、フォールバックは --video）
    if args.clips_json:
        clips = json.loads(args.clips_json)
    elif args.video:
        dur = get_video_duration(args.video)
        clips = [{
            "path": args.video,
            "timelineStart": 0.0,
            "timelineEnd": dur,
            "mediaInPoint": 0.0,
            "mediaOutPoint": dur,
        }]
    else:
        error("--video または --clips-json のいずれかが必要です")
        sys.exit(1)

    threshold    = THRESHOLDS[args.mode]
    api_key      = args.api_key
    llm_provider = args.llm_provider
    output_xml   = args.output
    n_clips      = len(clips)

    # タイムライン全体の長さ（最後のクリップの終端）
    total_duration = max(c.get("timelineEnd", 0.0) for c in clips)

    with tempfile.TemporaryDirectory() as tmpdir:
        all_segments = []

        # ---- 各クリップを個別に音声抽出 & 文字起こし ----
        for i, clip in enumerate(clips):
            clip_path = clip["path"]
            tl_start  = float(clip.get("timelineStart", 0.0))
            tl_end    = float(clip.get("timelineEnd",   0.0))
            media_in  = float(clip.get("mediaInPoint",  0.0))
            media_out = float(clip.get("mediaOutPoint", 0.0))

            # 抽出区間の長さ（メディアin/outが有効なら使用、それ以外はタイムライン尺）
            if media_out > media_in:
                duration_sec = media_out - media_in
            else:
                duration_sec = tl_end - tl_start if tl_end > tl_start else None

            if not Path(clip_path).exists():
                error(f"動画ファイルが見つかりません: {clip_path}")
                sys.exit(1)

            # 音声抽出（進捗: 10%〜35% をクリップ数で均等分割）
            pct_extract = 10 + i * 25 // n_clips
            label = f" ({i+1}/{n_clips})" if n_clips > 1 else ""
            progress("extracting", pct_extract, f"音声を抽出中...{label}")

            wav_path = os.path.join(tmpdir, f"audio_{i}.wav")
            extract_audio(clip_path, wav_path, start_sec=media_in, duration_sec=duration_sec)

            # 文字起こし（進捗: 35%〜60%）
            pct_transcribe = 35 + i * 25 // n_clips
            progress("transcribing", pct_transcribe, f"文字起こし中（mlx-whisper）{label}")
            segments = transcribe_to_segments(wav_path)

            # タイムラインオフセットを適用してセグメント時刻を絶対位置に変換
            for seg in segments:
                seg["start"] = round(seg["start"] + tl_start, 3)
                seg["end"]   = round(seg["end"]   + tl_start, 3)

            all_segments.extend(segments)

        # 全セグメントを時系列順にソート
        all_segments.sort(key=lambda s: s["start"])

        # SRT 書き出し（デバッグ用）
        srt_path = os.path.join(tmpdir, "transcript.srt")
        write_srt(all_segments, srt_path)

        # ---- 無音カット計算 ----
        progress("detecting", 62, "カット区間を計算中...")
        silence_cuts = compute_silence_cuts(all_segments, threshold)

        # ---- LLM 解析 ----
        provider_label = "Claude" if llm_provider == "claude" else "OpenAI"
        progress("analyzing", 72, f"AI解析中（{provider_label}・言い間違い・重複検出）...")
        content_cuts = analyze_content_cuts(all_segments, api_key, llm_provider)

        # ---- FCP XML 生成 ----
        progress("generating", 88, "FCP XMLを生成中...")

        all_cuts      = silence_cuts + content_cuts
        keep_segments = compute_keep_segments(total_duration, all_cuts)
        total_cut_sec = sum(
            c["end"] - c["start"] for c in merge_cuts(all_cuts)
        )

        # FPS は最初のクリップの ffprobe 結果を使用（デフォルト30）
        fps = 30.0
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-print_format", "json",
                clips[0]["path"],
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            data = json.loads(r.stdout)
            fr = data["streams"][0]["r_frame_rate"]
            num, den = map(int, fr.split("/"))
            fps = num / den
        except Exception:
            pass

        total_source_frames = int(round(total_duration * fps))
        sequence_name = Path(clips[0]["path"]).stem

        xml_str = generate_fcpxml(
            source_video_path=clips[0]["path"],
            keep_segments=keep_segments,
            fps=fps,
            total_source_frames=total_source_frames,
            sequence_name=sequence_name,
        )

        # XML を書き出し
        Path(output_xml).parent.mkdir(parents=True, exist_ok=True)
        Path(output_xml).write_text(xml_str, encoding="utf-8")

        merged = merge_cuts(all_cuts)
        # デュレーション0のカットを除外（razor が同一TCに2回かかりエラーになる）
        merged = [c for c in merged if round(c["end"] - c["start"], 3) > 0.0]
        cut_count = len(merged)
        cut_regions_list = [{"start": c["start"], "end": c["end"]} for c in merged]

        # JSON ファイルも書き出す（ExtendScript 直接カット用）
        json_path = output_xml.replace(".xml", ".json")
        Path(json_path).write_text(
            json.dumps({"cuts": cut_regions_list, "sequence_name": sequence_name}, ensure_ascii=False),
            encoding="utf-8",
        )

        done(output_xml, cut_count, total_cut_sec, cut_regions_list, json_path)


if __name__ == "__main__":
    main()
