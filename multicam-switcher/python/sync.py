#!/usr/bin/env python3
"""
sync.py - 音声同期スクリプト
カメラ映像とWAVファイルのオフセットをFFT相互相関で検出する。
stdout に JSON Lines 形式でプログレス/結果を出力する。

Usage:
    python3 sync.py '<json_args>'

Args JSON:
    { "video_path": "...", "wav_path": "..." }

Result JSON:
    { "type": "result", "offset_sec": -1.234 }
"""

import json
import os
import subprocess
import sys
import tempfile

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# CEP環境ではPATHが最小構成のため、Homebrewの標準パスを追加する
for _brew_bin in ["/usr/local/bin", "/opt/homebrew/bin"]:
    if _brew_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _brew_bin + ":" + os.environ.get("PATH", "")

import numpy as np
from pydub import AudioSegment

SYNC_SR = 16000  # 同期用サンプリングレート（16kHzで十分）


def emit(msg: dict):
    print(json.dumps(msg, ensure_ascii=False), flush=True)


def extract_audio_from_video(video_path: str, out_wav: str, sr: int = SYNC_SR):
    """ffmpeg で映像から音声（Lチャンネルのみ）を抽出する"""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-af", "pan=mono|c0=c0",  # Lチャンネルのみ
            "-ar", str(sr),
            "-vn", out_wav,
        ],
        check=True,
        capture_output=True,
    )


def load_audio_mono(path: str, force_sr: int | None = None):
    """pydub でオーディオを読み込み、モノラル numpy 配列で返す"""
    audio = AudioSegment.from_file(path)
    if audio.channels >= 2:
        audio = audio.split_to_mono()[0]
    else:
        audio = audio.set_channels(1)
    if force_sr:
        audio = audio.set_frame_rate(force_sr)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    samples /= (2 ** (audio.sample_width * 8 - 1))
    return samples, audio.frame_rate


def find_offset_fft(ref_samples: np.ndarray, target_samples: np.ndarray, sr: int) -> float:
    """FFT 相互相関でオフセットを検出する。
    正の値 = target が ref より後ろにある（WAVがタイムライン開始より遅れている）。
    """
    n = len(ref_samples) + len(target_samples) - 1
    f_ref = np.fft.rfft(ref_samples, n=n)
    f_target = np.fft.rfft(target_samples, n=n)
    corr = np.fft.irfft(f_ref * np.conj(f_target))
    peak = int(np.argmax(np.abs(corr)))
    if peak > n // 2:
        peak -= n
    return peak / sr


def main():
    args = json.loads(sys.argv[1])
    video_path = args["video_path"]
    wav_path = args["wav_path"]

    temp_wav = None
    try:
        emit({"type": "log", "message": f"映像から音声を抽出中: {os.path.basename(video_path)}"})
        emit({"type": "progress", "value": 5, "message": "映像から音声を抽出中..."})

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_wav = tmp.name

        extract_audio_from_video(video_path, temp_wav, sr=SYNC_SR)
        emit({"type": "progress", "value": 40, "message": "基準音声を読み込み中..."})

        ref_samples, ref_sr = load_audio_mono(temp_wav, force_sr=SYNC_SR)
        emit({"type": "log", "message": f"基準音声: {len(ref_samples)/ref_sr:.1f}秒"})

        emit({"type": "progress", "value": 60, "message": "WAVを読み込み中..."})
        target_samples, _ = load_audio_mono(wav_path, force_sr=SYNC_SR)
        emit({"type": "log", "message": f"WAV: {len(target_samples)/SYNC_SR:.1f}秒"})

        emit({"type": "progress", "value": 75, "message": "FFT相互相関でオフセットを検出中..."})
        offset_sec = find_offset_fft(ref_samples, target_samples, ref_sr)

        emit({"type": "progress", "value": 100, "message": "完了"})
        emit({"type": "log", "message": f"オフセット検出結果: {offset_sec:.3f} 秒"})
        emit({"type": "result", "offset_sec": round(offset_sec, 3)})

    finally:
        if temp_wav and os.path.exists(temp_wav):
            os.remove(temp_wav)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else str(exc)
        emit({"type": "error", "message": f"ffmpeg エラー: {stderr.strip()}"})
        sys.exit(1)
    except Exception as e:
        import traceback
        emit({"type": "error", "message": str(e) + "\n" + traceback.format_exc()})
        sys.exit(1)
