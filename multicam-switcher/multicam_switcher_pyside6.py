import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass

import numpy as np
from pydub import AudioSegment
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


BRIDGE_DIR = "/tmp/premiere-mcp-bridge"
CAM_KEYCODES = {1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26}
FRAME_MS = 50


@dataclass
class CutPoint:
    time_sec: float
    cam: int
    reason: str


def load_audio_mono(path: str, force_sr: int | None = None) -> tuple[np.ndarray, int]:
    audio = AudioSegment.from_file(path)
    # Lチャンネル（ch0）のみ使用
    if audio.channels >= 2:
        audio = audio.split_to_mono()[0]
    else:
        audio = audio.set_channels(1)
    if force_sr:
        audio = audio.set_frame_rate(force_sr)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    samples /= (2 ** (audio.sample_width * 8 - 1))
    return samples, audio.frame_rate


def load_wav_mono(path: str) -> tuple[np.ndarray, int]:
    return load_audio_mono(path)


def extract_audio_from_video(video_path: str, out_wav: str, sr: int = 16000):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-af", "pan=mono|c0=c0",  # Lチャンネルのみ
            "-ar",
            str(sr),
            "-vn",
            out_wav,
        ],
        check=True,
        capture_output=True,
    )


def find_offset_fft(ref_samples: np.ndarray, target_samples: np.ndarray, sr: int) -> float:
    n = len(ref_samples) + len(target_samples) - 1
    f_ref = np.fft.rfft(ref_samples, n=n)
    f_target = np.fft.rfft(target_samples, n=n)
    corr = np.fft.irfft(f_ref * np.conj(f_target))
    peak = int(np.argmax(np.abs(corr)))
    if peak > n // 2:
        peak -= n
    return peak / sr


def detect_speech_frames(
    samples: np.ndarray,
    sr: int,
    threshold_db: float,
    min_speech_sec: float,
    smoothing_sec: float,
    frame_ms: int = FRAME_MS,
) -> list[bool]:
    frame_size = max(1, int(sr * frame_ms / 1000))
    num_frames = len(samples) // frame_size
    rms_db: list[float] = []
    for i in range(num_frames):
        chunk = samples[i * frame_size : (i + 1) * frame_size]
        if len(chunk) == 0:
            rms_db.append(-120.0)
            continue
        rms = np.sqrt(np.mean(chunk**2))
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
    person_flags: list[list[bool]],
    person_cams: list[int],
    cutaway_cam: int,
    offset_sec: float,
    frame_ms: int,
    min_interval_sec: float,
    overlap_cutaway_sec: float = 1.5,
    silence_cutaway_sec: float = 2.0,
) -> list[CutPoint]:
    """
    overlap_cutaway_sec: 複数人同時発話がこの秒数以上続いたら引き絵へ（短い重複は無視）
    silence_cutaway_sec: 無音がこの秒数以上続いたら引き絵へ（短い無音は現カメラ維持）
    """
    if not person_flags:
        return []
    max_frames = max(len(flags) for flags in person_flags)
    padded_flags = []
    for flags in person_flags:
        padded_flags.append(flags + [False] * (max_frames - len(flags)))

    overlap_frames = int(overlap_cutaway_sec * 1000 / frame_ms)
    silence_frames = int(silence_cutaway_sec * 1000 / frame_ms)

    cut_list: list[CutPoint] = []
    current_cam = cutaway_cam
    non_single_count = 0  # 複数/無音が続いているフレーム数

    for idx in range(max_frames):
        video_time = idx * frame_ms / 1000 + offset_sec
        if video_time < 0:
            continue

        speaking = [i for i in range(len(padded_flags)) if padded_flags[i][idx]]

        if len(speaking) == 1:
            non_single_count = 0
            new_cam = person_cams[speaking[0]]
            reason = f"Person {speaking[0] + 1} 発話"
        elif len(speaking) == 0:
            non_single_count += 1
            # 無音が silence_cutaway_sec 未満なら現カメラを維持
            if non_single_count < silence_frames:
                continue
            new_cam = cutaway_cam
            reason = "引き絵（無音）"
        else:
            non_single_count += 1
            # 重複発話が overlap_cutaway_sec 未満なら現カメラを維持
            if non_single_count < overlap_frames:
                continue
            new_cam = cutaway_cam
            reason = "引き絵（複数発話）"

        if new_cam != current_cam:
            if cut_list and (video_time - cut_list[-1].time_sec) < min_interval_sec:
                # 最小間隔未満はスキップ（直前カットを保持）
                continue
            cut_list.append(CutPoint(video_time, new_cam, reason))
            current_cam = new_cam
    return cut_list


def execute_bridge(script: str, timeout: int = 15) -> dict:
    if not os.path.isdir(BRIDGE_DIR):
        return {"error": "bridge_dir_missing"}

    cmd_id = str(uuid.uuid4())[:8]
    cmd_file = os.path.join(BRIDGE_DIR, f"command-{cmd_id}.json")
    res_file = os.path.join(BRIDGE_DIR, f"response-{cmd_id}.json")
    try:
        with open(cmd_file, "w", encoding="utf-8") as f:
            json.dump({"id": cmd_id, "script": script}, f)
        start = time.time()
        while time.time() - start < timeout:
            if os.path.exists(res_file):
                time.sleep(0.1)
                with open(res_file, encoding="utf-8") as f:
                    content = f.read()
                os.remove(res_file)
                try:
                    return json.loads(content)
                except Exception:
                    return {"raw": content}
            time.sleep(0.25)
    finally:
        if os.path.exists(cmd_file):
            os.remove(cmd_file)
    return {"error": "timeout"}


def move_cti(seconds: float) -> bool:
    ticks = str(int(seconds * 254016000000))
    script = f"""
(function() {{
  var seq = app.project.activeSequence;
  if (!seq) return JSON.stringify({{success: false}});
  seq.setPlayerPosition("{ticks}");
  return JSON.stringify({{success: true}});
}})()
"""
    result = execute_bridge(script)
    inner = result.get("result", result)
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            pass
    return isinstance(inner, dict) and bool(inner.get("success"))


def activate_premiere_once() -> bool:
    """Premiere Pro を前面に出す（適用開始時に1回だけ呼ぶ）"""
    result = subprocess.run(
        ["osascript", "-e", """
tell application "Adobe Premiere Pro 2025"
    activate
end tell
delay 0.5
"""],
        capture_output=True,
    )
    return result.returncode == 0


def cut_and_switch(t_sec: float, cam: int) -> bool:
    """
    CTI移動（bridge）→ Cmd+K + 数字キー（osascript 1回）でカット+カメラ切替。
    クリップ選択は不要。Cmd+K がタイムラインにフォーカスを当てるため数字キーが確実に届く。
    """
    keycode = CAM_KEYCODES.get(cam)
    if not keycode:
        return False
    ticks = str(int(t_sec * 254016000000))

    # CTI 移動（ブリッジ）
    execute_bridge(
        f'(function(){{app.project.activeSequence.setPlayerPosition("{ticks}");return "ok"}})()'
    )

    # Cmd+K（カット）→ 右矢印で1フレーム進める → 数字キー（カメラ切替）
    # Cmd+K直後はCTIがカット境界に止まり、数字キーが左クリップに効いてしまうため
    # 1フレーム右に進めてから数字キーを押すことで右クリップのカメラを変更する
    result = subprocess.run(
        ["osascript", "-e", f"""
tell application "System Events"
    tell process "Adobe Premiere Pro 2025"
        key down command
        key code 40
        key up command
        delay 0.1
        key code 124
        delay 0.05
        key code {keycode}
    end tell
end tell
"""],
        capture_output=True,
    )
    return result.returncode == 0


def fetch_sequences() -> list[dict]:
    script = """
(function() {
  var seqs = [];
  for (var i = 0; i < app.project.sequences.numSequences; i++) {
    var s = app.project.sequences[i];
    seqs.push({name: s.name, id: s.sequenceID});
  }
  return JSON.stringify({sequences: seqs});
})()
"""
    result = execute_bridge(script)
    # ブリッジの戻り値は {"success": true, "result": "JSON文字列"}
    inner = result.get("result", result) if isinstance(result, dict) else result
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            return []
    return inner.get("sequences", []) if isinstance(inner, dict) else []


def format_seconds(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    sec = total_sec % 60
    total_min = total_sec // 60
    minute = total_min % 60
    hour = total_min // 60
    return f"{hour:02d}:{minute:02d}:{sec:02d}.{ms:03d}"


class SyncWorker(QThread):
    progress = Signal(int)
    log = Signal(str)
    finished_ok = Signal(float)
    failed = Signal(str)

    def __init__(self, video_path: str, wav_path: str):
        super().__init__()
        self.video_path = video_path
        self.wav_path = wav_path

    def run(self):
        temp_wav = None
        try:
            self.progress.emit(5)
            self.log.emit("[同期] カメラ映像から音声を抽出中")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                temp_wav = tmp.name
            extract_audio_from_video(self.video_path, temp_wav, sr=16000)
            self.progress.emit(45)

            self.log.emit("[同期] 基準音声とWAVを読み込み中")
            ref_samples, ref_sr = load_audio_mono(temp_wav, force_sr=16000)
            target_samples, target_sr = load_audio_mono(self.wav_path, force_sr=16000)
            if ref_sr != target_sr:
                raise RuntimeError("サンプリングレートが一致しません")
            self.progress.emit(70)

            self.log.emit("[同期] FFT相互相関でオフセットを検出中")
            offset_sec = find_offset_fft(ref_samples, target_samples, ref_sr)
            self.progress.emit(100)
            self.finished_ok.emit(offset_sec)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else str(exc)
            self.failed.emit(f"ffmpeg実行に失敗しました: {stderr.strip()}")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if temp_wav and os.path.exists(temp_wav):
                os.remove(temp_wav)


class AnalysisWorker(QThread):
    progress = Signal(int)
    log = Signal(str)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(
        self,
        wav_paths: list[str],
        person_cams: list[int],
        cutaway_cam: int,
        offset_sec: float,
        threshold_db: float,
        min_speech_sec: float,
        smoothing_sec: float,
        min_interval_sec: float,
        overlap_cutaway_sec: float = 1.5,
        silence_cutaway_sec: float = 2.0,
    ):
        super().__init__()
        self.wav_paths = wav_paths
        self.person_cams = person_cams
        self.cutaway_cam = cutaway_cam
        self.offset_sec = offset_sec
        self.threshold_db = threshold_db
        self.min_speech_sec = min_speech_sec
        self.smoothing_sec = smoothing_sec
        self.min_interval_sec = min_interval_sec
        self.overlap_cutaway_sec = overlap_cutaway_sec
        self.silence_cutaway_sec = silence_cutaway_sec

    def run(self):
        try:
            flags_list: list[list[bool]] = []
            total = len(self.wav_paths)
            for idx, path in enumerate(self.wav_paths, start=1):
                self.log.emit(f"[解析] Person {idx} WAVを読み込み: {os.path.basename(path)}")
                samples, sr = load_wav_mono(path)
                flags = detect_speech_frames(
                    samples,
                    sr,
                    self.threshold_db,
                    self.min_speech_sec,
                    self.smoothing_sec,
                    frame_ms=FRAME_MS,
                )
                flags_list.append(flags)
                segments = sum(
                    1
                    for i in range(len(flags))
                    if flags[i] and (i == 0 or not flags[i - 1])
                )
                speech_frames = sum(flags)
                speech_ratio = speech_frames / max(len(flags), 1) * 100
                cam = self.person_cams[idx - 1]
                self.log.emit(
                    f"[解析] Person {idx} (CAM {cam}): "
                    f"発話区間 {segments}件 / 発話率 {speech_ratio:.1f}% "
                    f"({speech_frames}/{len(flags)}フレーム)"
                )
                self.progress.emit(int(idx / total * 70))

            cut_list = build_cut_list(
                flags_list,
                self.person_cams,
                self.cutaway_cam,
                self.offset_sec,
                FRAME_MS,
                self.min_interval_sec,
                overlap_cutaway_sec=self.overlap_cutaway_sec,
                silence_cutaway_sec=self.silence_cutaway_sec,
            )
            # カメラ別カット数の集計
            cam_counts: dict[int, int] = {}
            for cut in cut_list:
                cam_counts[cut.cam] = cam_counts.get(cut.cam, 0) + 1
            summary = ", ".join(f"CAM{k}:{v}件" for k, v in sorted(cam_counts.items()))
            self.log.emit(f"[カットリスト] {len(cut_list)}件 ({summary})")
            for cut in cut_list[:200]:
                self.log.emit(f"{format_seconds(cut.time_sec)} → CAM {cut.cam} ({cut.reason})")
            if len(cut_list) > 200:
                self.log.emit(f"[カットリスト] 残り {len(cut_list) - 200} 件は省略")
            self.progress.emit(100)
            self.finished_ok.emit(cut_list)
        except Exception as exc:
            self.failed.emit(str(exc))


class ApplyWorker(QThread):
    progress = Signal(int)
    log = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, cut_list: list[CutPoint]):
        super().__init__()
        self.cut_list = cut_list

    def run(self):
        try:
            total = len(self.cut_list)
            if total == 0:
                self.finished_ok.emit()
                return
            # Premiere を前面に（1回だけ）
            activate_premiere_once()

            # 前から順に処理（マルチカムはrippleなので順序不問だが前からの方が自然）
            sorted_cuts = sorted(self.cut_list, key=lambda c: c.time_sec)
            done = 0
            for idx, cut in enumerate(sorted_cuts, start=1):
                self.log.emit(f"[適用] {format_seconds(cut.time_sec)} → CAM {cut.cam} ({cut.reason})")
                if cut_and_switch(cut.time_sec, cut.cam):
                    done += 1
                else:
                    self.log.emit(f"[警告] {format_seconds(cut.time_sec)} のカット/切替に失敗")
                self.progress.emit(int(idx / total * 100))

            self.log.emit(f"[完了] {done}/{total}件 適用")
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class FilePickerRow(QWidget):
    def __init__(self, button_text: str, filter_text: str):
        super().__init__()
        self.filter_text = filter_text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.button = QPushButton(button_text)
        self.line_edit = QLineEdit()
        self.line_edit.setReadOnly(True)
        layout.addWidget(self.button)
        layout.addWidget(self.line_edit, 1)
        self.button.clicked.connect(self.pick_file)

    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "ファイルを選択", "", self.filter_text)
        if path:
            self.line_edit.setText(path)

    def path(self) -> str:
        return self.line_edit.text().strip()

    def set_path(self, path: str):
        self.line_edit.setText(path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("マルチカム オートスイッチャー")
        self.setMinimumSize(800, 600)

        self.cut_list: list[CutPoint] = []
        self.sync_worker: SyncWorker | None = None
        self.analysis_worker: AnalysisWorker | None = None
        self.apply_worker: ApplyWorker | None = None

        tabs = QTabWidget()
        tabs.addTab(self.build_sync_tab(), "同期設定")
        tabs.addTab(self.build_speaker_tab(), "話者設定")
        tabs.addTab(self.build_run_tab(), "実行")
        self.setCentralWidget(tabs)

        self.load_sequences()

    def build_sync_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        ref_group = QGroupBox("基準音声（カメラ映像から抽出）")
        ref_layout = QVBoxLayout(ref_group)
        self.video_picker = FilePickerRow("選択...", "Video Files (*.mp4 *.mov *.mxf *.mkv);;All Files (*)")
        ref_layout.addWidget(QLabel("カメラ映像ファイル"))
        ref_layout.addWidget(self.video_picker)
        ref_layout.addWidget(QLabel("※ どれか1台のカメラ映像ファイルを選択してください"))

        wav_group = QGroupBox("ピンマイクWAV（基準用）")
        wav_layout = QVBoxLayout(wav_group)
        self.ref_wav_picker = FilePickerRow("選択...", "Audio Files (*.wav *.mp3 *.m4a *.aif *.aiff);;All Files (*)")
        wav_layout.addWidget(QLabel("WAVファイル"))
        wav_layout.addWidget(self.ref_wav_picker)
        wav_layout.addWidget(QLabel("※ 上のカメラ映像と同一シーンを収録した任意の1本を選択"))

        control_row = QHBoxLayout()
        self.sync_button = QPushButton("自動同期を実行")
        self.sync_button.clicked.connect(self.run_sync)
        self.sync_progress = QProgressBar()
        control_row.addWidget(self.sync_button)
        control_row.addWidget(self.sync_progress, 1)

        result_layout = QFormLayout()
        self.offset_result_label = QLabel("未実行")
        self.manual_offset_spin = QDoubleSpinBox()
        self.manual_offset_spin.setDecimals(3)
        self.manual_offset_spin.setRange(-86400.0, 86400.0)
        self.manual_offset_spin.setSingleStep(0.1)
        result_layout.addRow("オフセット結果", self.offset_result_label)
        result_layout.addRow("手動調整（秒）", self.manual_offset_spin)

        layout.addWidget(ref_group)
        layout.addWidget(wav_group)
        layout.addLayout(control_row)
        layout.addLayout(result_layout)
        layout.addStretch(1)
        return tab

    def build_speaker_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        mic_group = QGroupBox("話者 / マイク設定")
        mic_layout = QGridLayout(mic_group)
        self.person_rows: list[tuple[FilePickerRow, QComboBox]] = []
        for i in range(4):
            picker = FilePickerRow("WAV選択...", "Audio Files (*.wav *.mp3 *.m4a *.aif *.aiff);;All Files (*)")
            combo = self.make_cam_combo(default=i + 1)
            mic_layout.addWidget(QLabel(f"Person {i + 1}"), i, 0)
            mic_layout.addWidget(picker, i, 1)
            mic_layout.addWidget(QLabel("→ CAM"), i, 2)
            mic_layout.addWidget(combo, i, 3)
            self.person_rows.append((picker, combo))

        cutaway_group = QGroupBox("引き絵カメラ")
        cutaway_layout = QFormLayout(cutaway_group)
        self.cutaway_cam_combo = self.make_cam_combo(default=7)
        cutaway_layout.addRow("複数人発話 / 無音時", self.cutaway_cam_combo)

        seq_group = QGroupBox("シーケンス")
        seq_layout = QVBoxLayout(seq_group)
        seq_row = QHBoxLayout()
        self.sequence_combo = QComboBox()
        self.sequence_refresh_button = QPushButton("再取得")
        self.sequence_refresh_button.clicked.connect(self.load_sequences)
        seq_row.addWidget(self.sequence_combo, 1)
        seq_row.addWidget(self.sequence_refresh_button)
        self.sequence_manual_edit = QLineEdit()
        self.sequence_manual_edit.setPlaceholderText("シーケンス名を手動入力")
        seq_layout.addLayout(seq_row)
        seq_layout.addWidget(self.sequence_manual_edit)
        self.sequence_manual_edit.hide()

        vad_group = QGroupBox("VAD設定")
        vad_layout = QFormLayout(vad_group)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(-120.0, 0.0)
        self.threshold_spin.setValue(-40.0)
        self.threshold_spin.setDecimals(1)
        self.min_speech_spin = QDoubleSpinBox()
        self.min_speech_spin.setRange(0.05, 10.0)
        self.min_speech_spin.setValue(0.3)
        self.min_speech_spin.setDecimals(2)
        self.smoothing_spin = QDoubleSpinBox()
        self.smoothing_spin.setRange(0.0, 5.0)
        self.smoothing_spin.setValue(0.3)
        self.smoothing_spin.setDecimals(2)
        self.min_interval_spin = QDoubleSpinBox()
        self.min_interval_spin.setRange(0.0, 10.0)
        self.min_interval_spin.setValue(0.5)
        self.min_interval_spin.setDecimals(2)
        self.overlap_cutaway_spin = QDoubleSpinBox()
        self.overlap_cutaway_spin.setRange(0.0, 10.0)
        self.overlap_cutaway_spin.setValue(1.5)
        self.overlap_cutaway_spin.setDecimals(1)
        self.silence_cutaway_spin = QDoubleSpinBox()
        self.silence_cutaway_spin.setRange(0.0, 10.0)
        self.silence_cutaway_spin.setValue(2.0)
        self.silence_cutaway_spin.setDecimals(1)
        vad_layout.addRow("無音閾値（dBFS）", self.threshold_spin)
        vad_layout.addRow("最小発話長（秒）", self.min_speech_spin)
        vad_layout.addRow("無音スムージング（秒）", self.smoothing_spin)
        vad_layout.addRow("最小カット間隔（秒）", self.min_interval_spin)
        vad_layout.addRow("重複→引き絵 閾値（秒）", self.overlap_cutaway_spin)
        vad_layout.addRow("無音→引き絵 閾値（秒）", self.silence_cutaway_spin)

        layout.addWidget(mic_group)
        layout.addWidget(cutaway_group)
        layout.addWidget(seq_group)
        layout.addWidget(vad_group)
        layout.addStretch(1)
        return tab

    def build_run_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        button_row = QHBoxLayout()
        self.analyze_button = QPushButton("カットリストを解析")
        self.apply_button = QPushButton("Premiereに適用")
        self.apply_button.setEnabled(False)
        self.analyze_button.clicked.connect(self.run_analysis)
        self.apply_button.clicked.connect(self.apply_to_premiere)
        button_row.addWidget(self.analyze_button)
        button_row.addWidget(self.apply_button)

        self.run_progress = QProgressBar()
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)

        layout.addLayout(button_row)
        layout.addWidget(self.run_progress)
        layout.addWidget(self.log_edit, 1)
        return tab

    def make_cam_combo(self, default: int) -> QComboBox:
        combo = QComboBox()
        for cam in range(1, 8):
            combo.addItem(str(cam), cam)
        combo.setCurrentIndex(max(0, default - 1))
        return combo

    def append_log(self, message: str):
        self.log_edit.append(message)

    def load_sequences(self):
        self.sequence_combo.clear()
        sequences = fetch_sequences()
        if sequences:
            for seq in sequences:
                self.sequence_combo.addItem(seq.get("name", "(no name)"), seq.get("id"))
            self.sequence_manual_edit.hide()
            self.append_log(f"[Premiere] シーケンス {len(sequences)} 件を取得")
        else:
            self.sequence_combo.addItem("取得失敗: 手動入力を使用")
            self.sequence_manual_edit.show()
            self.append_log("[Premiere] シーケンス取得に失敗したため手動入力に切替")

    def run_sync(self):
        video_path = self.video_picker.path()
        wav_path = self.ref_wav_picker.path()
        if not video_path or not wav_path:
            QMessageBox.warning(self, "入力不足", "同期用の映像ファイルとWAVファイルを指定してください。")
            return

        self.sync_progress.setValue(0)
        self.sync_button.setEnabled(False)
        self.sync_worker = SyncWorker(video_path, wav_path)
        self.sync_worker.progress.connect(self.sync_progress.setValue)
        self.sync_worker.log.connect(self.append_log)
        self.sync_worker.finished_ok.connect(self.on_sync_finished)
        self.sync_worker.failed.connect(self.on_sync_failed)
        self.sync_worker.finished.connect(lambda: self.sync_button.setEnabled(True))
        self.sync_worker.start()

    def on_sync_finished(self, offset_sec: float):
        self.offset_result_label.setText(f"{offset_sec:.3f} 秒")
        self.manual_offset_spin.setValue(offset_sec)
        self.append_log(f"[同期] オフセット結果: {offset_sec:.3f} 秒")

    def on_sync_failed(self, message: str):
        self.append_log(f"[同期] エラー: {message}")
        QMessageBox.critical(self, "同期エラー", message)

    def collect_analysis_inputs(self) -> tuple[list[str], list[int], float] | None:
        wav_paths: list[str] = []
        person_cams: list[int] = []
        for idx, (picker, combo) in enumerate(self.person_rows, start=1):
            path = picker.path()
            if not path:
                QMessageBox.warning(self, "入力不足", f"Person {idx} のWAVファイルを指定してください。")
                return None
            wav_paths.append(path)
            person_cams.append(combo.currentData())
        return wav_paths, person_cams, self.manual_offset_spin.value()

    def run_analysis(self):
        collected = self.collect_analysis_inputs()
        if not collected:
            return

        wav_paths, person_cams, offset_sec = collected
        self.cut_list = []
        self.run_progress.setValue(0)
        self.analyze_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.analysis_worker = AnalysisWorker(
            wav_paths=wav_paths,
            person_cams=person_cams,
            cutaway_cam=self.cutaway_cam_combo.currentData(),
            offset_sec=offset_sec,
            threshold_db=self.threshold_spin.value(),
            min_speech_sec=self.min_speech_spin.value(),
            smoothing_sec=self.smoothing_spin.value(),
            min_interval_sec=self.min_interval_spin.value(),
            overlap_cutaway_sec=self.overlap_cutaway_spin.value(),
            silence_cutaway_sec=self.silence_cutaway_spin.value(),
        )
        self.analysis_worker.progress.connect(self.run_progress.setValue)
        self.analysis_worker.log.connect(self.append_log)
        self.analysis_worker.finished_ok.connect(self.on_analysis_finished)
        self.analysis_worker.failed.connect(self.on_analysis_failed)
        self.analysis_worker.finished.connect(lambda: self.analyze_button.setEnabled(True))
        self.analysis_worker.start()

    def on_analysis_finished(self, cut_list: list[CutPoint]):
        self.cut_list = cut_list
        self.apply_button.setEnabled(bool(cut_list))
        self.append_log(f"[解析] 完了: {len(cut_list)} 件のカットポイント")
        if not cut_list:
            QMessageBox.information(self, "解析結果", "カットポイントは生成されませんでした。")

    def on_analysis_failed(self, message: str):
        self.append_log(f"[解析] エラー: {message}")
        QMessageBox.critical(self, "解析エラー", message)

    def apply_to_premiere(self):
        if not self.cut_list:
            QMessageBox.warning(self, "未解析", "先にカットリストを解析してください。")
            return

        self.run_progress.setValue(0)
        self.analyze_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.apply_worker = ApplyWorker(self.cut_list)
        self.apply_worker.progress.connect(self.run_progress.setValue)
        self.apply_worker.log.connect(self.append_log)
        self.apply_worker.finished_ok.connect(self.on_apply_finished)
        self.apply_worker.failed.connect(self.on_apply_failed)
        self.apply_worker.finished.connect(self.on_apply_thread_finished)
        self.apply_worker.start()

    def on_apply_finished(self):
        self.append_log("[適用] Premiereへの反映が完了")
        QMessageBox.information(self, "完了", "Premiereへの適用が完了しました。")

    def on_apply_failed(self, message: str):
        self.append_log(f"[適用] エラー: {message}")
        QMessageBox.critical(self, "適用エラー", message)

    def on_apply_thread_finished(self):
        self.analyze_button.setEnabled(True)
        self.apply_button.setEnabled(bool(self.cut_list))


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
