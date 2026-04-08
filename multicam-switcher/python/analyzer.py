"""
analyzer.py
MultiCam Switcher — 音声解析スクリプト

Usage:
    python3 analyzer.py '<json_args>'

Args JSON:
    {
      "cameras": [{"path": "/path/to/cam1.wav", "cam": 1}, ...],
      "cutaway": 7,
      "offset": 0.0,
      "settings": {
        "threshold_db": -40.0,
        "min_speech_sec": 0.3,
        "smoothing_sec": 0.3,
        "min_interval_sec": 0.5,
        "overlap_cutaway_sec": 1.5,
        "silence_cutaway_sec": 2.0
      }
    }

Stdout: JSON Lines
    {"type": "log", "message": "..."}
    {"type": "progress", "value": 0-100, "message": "..."}
    {"type": "result", "cuts": [...], "count": N}
    {"type": "error", "message": "..."}
"""

import json
import sys
import os

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import numpy as np
from pydub import AudioSegment

FRAME_MS = 50


def emit(msg):
    """JSON Lines 形式で stdout に出力する。"""
    print(json.dumps(msg, ensure_ascii=False), flush=True)


# ---------------------------------------------------------------------------
# コアロジック（/tmp/multicam_switcher_pyside6.py から移植・ロジック変更なし）
# ---------------------------------------------------------------------------

def load_audio_mono(path, force_sr=None):
    """
    音声ファイルをモノラル numpy 配列として読み込む。
    複数チャンネルの場合は L チャンネル（ch0）のみ使用する。

    Returns:
        (samples: np.ndarray[float32], sample_rate: int)
    """
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


def detect_speech_frames(
    samples,
    sr,
    threshold_db,
    min_speech_sec,
    smoothing_sec,
    frame_ms=FRAME_MS,
):
    """
    RMS ベースの VAD（Voice Activity Detection）。
    各フレームが発話中かどうかを bool リストで返す。

    Args:
        samples: モノラル音声サンプル (float32 ndarray)
        sr: サンプリングレート
        threshold_db: 無音判定閾値 (dBFS)
        min_speech_sec: 最小発話長（秒）
        smoothing_sec: スムージング幅（秒）
        frame_ms: フレーム長（ミリ秒）

    Returns:
        list[bool] — 各フレームの発話フラグ
    """
    frame_size = max(1, int(sr * frame_ms / 1000))
    num_frames = len(samples) // frame_size
    rms_db = []
    for i in range(num_frames):
        chunk = samples[i * frame_size: (i + 1) * frame_size]
        if len(chunk) == 0:
            rms_db.append(-120.0)
            continue
        rms = np.sqrt(np.mean(chunk ** 2))
        rms_db.append(20 * np.log10(rms + 1e-9))

    flags = [db > threshold_db for db in rms_db]
    smooth_frames = max(1, int(smoothing_sec * 1000 / frame_ms))
    for i in range(max(0, len(flags) - smooth_frames)):
        if flags[i] and flags[i + smooth_frames]:
            for j in range(smooth_frames):
                flags[i + j] = True

    min_frames = max(1, int(min_speech_sec * 1000 / frame_ms))
    run_start = None
    for i, flag in enumerate(flags + [False]):
        if flag and run_start is None:
            run_start = i
        elif not flag and run_start is not None:
            if i - run_start < min_frames:
                for j in range(run_start, i):
                    flags[j] = False
            run_start = None
    return flags


def build_cut_list(
    person_flags,
    person_cams,
    cutaway_cam,
    offset_sec,
    frame_ms,
    min_interval_sec,
    overlap_cutaway_sec=1.5,
    silence_cutaway_sec=2.0,
):
    """
    発話フラグリストからカットポイントリストを生成する。

    Args:
        person_flags: 各人物の発話フラグリスト (list[list[bool]])
        person_cams: 各人物のカメラ番号 (list[int])
        cutaway_cam: 引き絵カメラ番号
        offset_sec: タイムラインオフセット（秒）
        frame_ms: フレーム長（ミリ秒）
        min_interval_sec: 最小カット間隔（秒）
        overlap_cutaway_sec: 複数人同時発話がこの秒数以上続いたら引き絵へ
        silence_cutaway_sec: 無音がこの秒数以上続いたら引き絵へ

    Returns:
        list[dict] — [{"time_sec": float, "cam": int, "reason": str}, ...]
    """
    if not person_flags:
        return []

    max_frames = max(len(flags) for flags in person_flags)
    padded_flags = []
    for flags in person_flags:
        padded_flags.append(flags + [False] * (max_frames - len(flags)))

    overlap_frames = int(overlap_cutaway_sec * 1000 / frame_ms)
    silence_frames = int(silence_cutaway_sec * 1000 / frame_ms)

    cut_list = []
    current_cam = cutaway_cam
    non_single_count = 0

    for idx in range(max_frames):
        video_time = idx * frame_ms / 1000 + offset_sec
        if video_time < 0:
            continue

        speaking = [i for i in range(len(padded_flags)) if padded_flags[i][idx]]

        if len(speaking) == 1:
            non_single_count = 0
            new_cam = person_cams[speaking[0]]
            reason = "Person {} 発話".format(speaking[0] + 1)
        elif len(speaking) == 0:
            non_single_count += 1
            if non_single_count < silence_frames:
                continue
            new_cam = cutaway_cam
            reason = "引き絵（無音）"
        else:
            non_single_count += 1
            if non_single_count < overlap_frames:
                continue
            new_cam = cutaway_cam
            reason = "引き絵（複数発話）"

        if new_cam != current_cam:
            if cut_list and (video_time - cut_list[-1]["time_sec"]) < min_interval_sec:
                continue
            cut_list.append({"time_sec": video_time, "cam": new_cam, "reason": reason})
            current_cam = new_cam

    return cut_list


def format_seconds(seconds):
    """秒数を HH:MM:SS.mmm 形式の文字列に変換する。"""
    total_ms = int(round(max(0.0, seconds) * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    sec = total_sec % 60
    total_min = total_sec // 60
    minute = total_min % 60
    hour = total_min // 60
    return "{:02d}:{:02d}:{:02d}.{:03d}".format(hour, minute, sec, ms)


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        emit({"type": "error", "message": "引数が不足しています。JSON 文字列を第1引数で渡してください。"})
        sys.exit(1)

    args = json.loads(sys.argv[1])
    cameras = args["cameras"]
    cutaway = args.get("cutaway", 7)
    offset = args.get("offset", 0.0)
    settings = args.get("settings", {})

    threshold_db = settings.get("threshold_db", -40.0)
    min_speech_sec = settings.get("min_speech_sec", 0.3)
    smoothing_sec = settings.get("smoothing_sec", 0.3)
    min_interval_sec = settings.get("min_interval_sec", 0.5)
    overlap_cutaway_sec = settings.get("overlap_cutaway_sec", 1.5)
    silence_cutaway_sec = settings.get("silence_cutaway_sec", 2.0)

    person_flags = []
    person_cams = []

    for idx, cam_info in enumerate(cameras):
        emit({"type": "log", "message": "Person {} WAVを読み込み: {}".format(
            idx + 1, os.path.basename(cam_info["path"])
        )})

        samples, sr = load_audio_mono(cam_info["path"])
        flags = detect_speech_frames(
            samples, sr, threshold_db, min_speech_sec, smoothing_sec
        )

        segments = sum(
            1 for i in range(len(flags))
            if flags[i] and (i == 0 or not flags[i - 1])
        )
        emit({"type": "log", "message": "Person {}: 発話区間 {}件".format(idx + 1, segments)})

        person_flags.append(flags)
        person_cams.append(cam_info["cam"])

        progress_value = int((idx + 1) / len(cameras) * 70)
        emit({"type": "progress", "value": progress_value,
              "message": "音声解析 {}/{}".format(idx + 1, len(cameras))})

    emit({"type": "log", "message": "カットリストを生成中..."})
    emit({"type": "progress", "value": 85, "message": "カットリスト生成中..."})

    cut_list = build_cut_list(
        person_flags, person_cams, cutaway, offset, FRAME_MS, min_interval_sec,
        overlap_cutaway_sec=overlap_cutaway_sec,
        silence_cutaway_sec=silence_cutaway_sec,
    )

    emit({"type": "log", "message": "カットリスト生成完了: {}件".format(len(cut_list))})

    # ログにカットリストをプレビュー出力（最大200件）
    for cut in cut_list[:200]:
        emit({"type": "log", "message": "{} → CAM {} ({})".format(
            format_seconds(cut["time_sec"]), cut["cam"], cut["reason"]
        )})
    if len(cut_list) > 200:
        emit({"type": "log", "message": "... 残り {}件は省略".format(len(cut_list) - 200)})

    emit({"type": "progress", "value": 100, "message": "完了"})
    emit({"type": "result", "cuts": cut_list, "count": len(cut_list)})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        emit({"type": "error", "message": str(e) + "\n" + traceback.format_exc()})
        sys.exit(1)
