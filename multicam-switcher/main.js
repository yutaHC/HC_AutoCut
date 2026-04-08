/**
 * main.js
 * MultiCam Switcher CEPパネル メインロジック
 */

'use strict';

const csInterface = new CSInterface();
const path = require('path');
const { spawn, exec } = require('child_process');
const fs = require('fs');
const os = require('os');

const PLUGIN_DIR = csInterface.getSystemPath(SystemPath.EXTENSION);
const PYTHON_BIN = path.join(PLUGIN_DIR, 'venv', 'bin', 'python3');
const ANALYZER_SCRIPT = path.join(PLUGIN_DIR, 'python', 'analyzer.py');
const SYNC_SCRIPT = path.join(PLUGIN_DIR, 'python', 'sync.py');

// マルチカム用 macOS キーコード (カメラ番号 → キーコード)
const CAM_KEYCODES = {1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26};
const TICKS_PER_SEC = 254016000000;

// 最大行数
const MAX_ROWS = 7;

let cameraRows = [];   // [{path, cam, rowEl}]
let cutList = [];      // [{time_sec, cam, reason}]
let isRunning = false;
let pendingFileRowIdx = -1;  // ファイル選択ダイアログを開いた行のインデックス

// 同期関連
let syncVideoPath = '';
let syncWavPath = '';
let syncResultSec = null;
let isSyncing = false;

// ---- 初期化 ----

window.addEventListener('load', () => {
  log('MultiCam Switcher が起動しました');

  if (!fs.existsSync(PYTHON_BIN)) {
    log('⚠️  setup.sh を先に実行してください（venv が見つかりません）');
    log('ターミナル: cd "' + PLUGIN_DIR + '" && sh setup.sh');
  }

  // 初期2行を追加
  addCameraRow('', 1);
  addCameraRow('', 2);

  // hidden file input のイベント
  document.getElementById('fileInputHidden').addEventListener('change', onFileSelected);
  document.getElementById('syncVideoInputHidden').addEventListener('change', onSyncVideoSelected);
  document.getElementById('syncWavInputHidden').addEventListener('change', onSyncWavSelected);
});

// ---- カメラ行管理 ----

function addCameraRow(filePath, camNum) {
  filePath = filePath || '';
  camNum = camNum || 1;

  const idx = cameraRows.length;
  if (idx >= MAX_ROWS) {
    log('行は最大 ' + MAX_ROWS + ' まで追加できます');
    return;
  }

  const container = document.getElementById('cameraRowsContainer');

  const rowEl = document.createElement('div');
  rowEl.className = 'camera-row';
  rowEl.dataset.idx = idx;

  const labelEl = document.createElement('span');
  labelEl.className = 'camera-row-label';
  labelEl.textContent = 'Person ' + (idx + 1);

  const fileBtn = document.createElement('button');
  fileBtn.className = 'file-btn';
  fileBtn.textContent = 'WAV 選択...';
  fileBtn.addEventListener('click', () => openFileDialog(parseInt(rowEl.dataset.idx)));

  const pathEl = document.createElement('div');
  pathEl.className = 'file-path';
  pathEl.textContent = filePath ? path.basename(filePath) : '（未選択）';
  pathEl.title = filePath;

  const camLabelEl = document.createElement('span');
  camLabelEl.className = 'cam-label';
  camLabelEl.textContent = '→ CAM';

  const camSelect = document.createElement('select');
  camSelect.className = 'cam-select';
  for (let c = 1; c <= 7; c++) {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    if (c === camNum) opt.selected = true;
    camSelect.appendChild(opt);
  }

  const removeBtn = document.createElement('button');
  removeBtn.className = 'remove-btn';
  removeBtn.textContent = '×';
  removeBtn.addEventListener('click', () => removeCameraRow(parseInt(rowEl.dataset.idx)));

  rowEl.appendChild(labelEl);
  rowEl.appendChild(fileBtn);
  rowEl.appendChild(pathEl);
  rowEl.appendChild(camLabelEl);
  rowEl.appendChild(camSelect);
  rowEl.appendChild(removeBtn);

  container.appendChild(rowEl);

  cameraRows.push({
    path: filePath,
    cam: camNum,
    rowEl: rowEl,
    pathEl: pathEl,
    camSelect: camSelect,
  });

  // ラベルを再番号付け
  renumberRows();
}

function removeCameraRow(idx) {
  if (cameraRows.length <= 1) {
    log('最低1行は必要です');
    return;
  }
  if (idx < 0 || idx >= cameraRows.length) return;

  const row = cameraRows[idx];
  row.rowEl.parentNode.removeChild(row.rowEl);
  cameraRows.splice(idx, 1);

  renumberRows();
}

function renumberRows() {
  cameraRows.forEach((row, i) => {
    row.rowEl.dataset.idx = i;
    const label = row.rowEl.querySelector('.camera-row-label');
    if (label) label.textContent = 'Person ' + (i + 1);
  });
}

function openFileDialog(rowIdx) {
  pendingFileRowIdx = rowIdx;
  const input = document.getElementById('fileInputHidden');
  input.value = '';
  input.click();
}

function onFileSelected(event) {
  const files = event.target.files;
  if (!files || !files.length) return;
  const file = files[0];
  const filePath = file.path || (file.name ? file.name : '');
  const idx = pendingFileRowIdx;
  if (idx >= 0 && idx < cameraRows.length) {
    cameraRows[idx].path = filePath;
    cameraRows[idx].pathEl.textContent = path.basename(filePath) || filePath;
    cameraRows[idx].pathEl.title = filePath;
  }
  pendingFileRowIdx = -1;
}

// ---- VAD 折りたたみ ----

function toggleVad() {
  const body = document.getElementById('vadBody');
  const arrow = document.getElementById('vadArrow');
  const isOpen = body.classList.contains('open');
  if (isOpen) {
    body.classList.remove('open');
    arrow.classList.remove('open');
  } else {
    body.classList.add('open');
    arrow.classList.add('open');
  }
}

// ---- 音声同期 ----

function toggleSync() {
  const body = document.getElementById('syncBody');
  const arrow = document.getElementById('syncArrow');
  const isOpen = body.classList.contains('open');
  if (isOpen) {
    body.classList.remove('open');
    arrow.classList.remove('open');
  } else {
    body.classList.add('open');
    arrow.classList.add('open');
  }
}

function openSyncVideoDialog() {
  document.getElementById('syncVideoInputHidden').value = '';
  document.getElementById('syncVideoInputHidden').click();
}

function openSyncWavDialog() {
  document.getElementById('syncWavInputHidden').value = '';
  document.getElementById('syncWavInputHidden').click();
}

function onSyncVideoSelected(event) {
  const files = event.target.files;
  if (!files || !files.length) return;
  const file = files[0];
  syncVideoPath = file.path || file.name || '';
  const el = document.getElementById('syncVideoPath');
  el.textContent = path.basename(syncVideoPath) || syncVideoPath;
  el.title = syncVideoPath;
}

function onSyncWavSelected(event) {
  const files = event.target.files;
  if (!files || !files.length) return;
  const file = files[0];
  syncWavPath = file.path || file.name || '';
  const el = document.getElementById('syncWavPath');
  el.textContent = path.basename(syncWavPath) || syncWavPath;
  el.title = syncWavPath;
}

async function runSync() {
  if (isSyncing || isRunning) return;
  if (!syncVideoPath) { alert('基準カメラ映像を選択してください'); return; }
  if (!syncWavPath)   { alert('基準マイクWAVを選択してください'); return; }
  if (!fs.existsSync(PYTHON_BIN)) {
    alert('setup.sh を先に実行してください');
    return;
  }

  isSyncing = true;
  document.getElementById('syncBtn').disabled = true;
  document.getElementById('syncProgressWrap').style.display = 'block';
  document.getElementById('syncResultWrap').style.display = 'none';
  setSyncProgress(0, '同期処理を開始...');
  log('自動同期を開始: ' + path.basename(syncVideoPath) + ' ↔ ' + path.basename(syncWavPath));

  const args = { video_path: syncVideoPath, wav_path: syncWavPath };

  const proc = spawn(PYTHON_BIN, [SYNC_SCRIPT, JSON.stringify(args)], {
    env: Object.assign({}, process.env, { KMP_DUPLICATE_LIB_OK: 'TRUE' })
  });

  let buffer = '';

  proc.stdout.on('data', (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop();
    lines.forEach(line => {
      line = line.trim();
      if (!line) return;
      try {
        const msg = JSON.parse(line);
        if (msg.type === 'progress') setSyncProgress(msg.value, msg.message);
        else if (msg.type === 'log') appendLog(msg.message);
        else if (msg.type === 'result') {
          syncResultSec = msg.offset_sec;
          document.getElementById('syncResultValue').textContent = msg.offset_sec.toFixed(3);
          document.getElementById('syncResultWrap').style.display = 'flex';
          appendLog('[同期完了] オフセット: ' + msg.offset_sec.toFixed(3) + ' 秒');
        } else if (msg.type === 'error') {
          appendLog('[同期エラー] ' + msg.message);
        }
      } catch (e) {
        appendLog(line);
      }
    });
  });

  proc.stderr.on('data', (data) => {
    appendLog('[stderr] ' + data.toString().trim());
  });

  proc.on('close', () => {
    isSyncing = false;
    document.getElementById('syncBtn').disabled = false;
  });

  proc.on('error', (err) => {
    isSyncing = false;
    document.getElementById('syncBtn').disabled = false;
    appendLog('[同期エラー] 起動失敗: ' + err.message);
  });
}

function setSyncProgress(pct, msg) {
  document.getElementById('syncProgressBar').style.width = pct + '%';
  document.getElementById('syncProgressMsg').textContent = msg;
}

function applySyncResult() {
  if (syncResultSec === null) return;
  document.getElementById('offsetInput').value = syncResultSec.toFixed(3);
  appendLog('[同期] オフセット ' + syncResultSec.toFixed(3) + ' 秒 を適用しました');
}

// ---- 解析開始 ----

async function startAnalysis() {
  if (isRunning) return;

  // バリデーション
  const rowsWithPath = cameraRows.filter(r => r.path && r.path.trim());
  if (rowsWithPath.length === 0) {
    alert('少なくとも1行にWAVファイルを選択してください');
    return;
  }
  for (let i = 0; i < cameraRows.length; i++) {
    if (!cameraRows[i].path || !cameraRows[i].path.trim()) {
      alert('Person ' + (i + 1) + ' のWAVファイルが未選択です。行を削除するか、ファイルを選択してください。');
      return;
    }
  }

  if (!fs.existsSync(PYTHON_BIN)) {
    alert('setup.sh を先に実行してください。\n\nターミナルで:\n  cd "' + PLUGIN_DIR + '"\n  sh setup.sh');
    return;
  }

  isRunning = true;
  cutList = [];
  disableUI();
  resetResult();
  setProgress(0, '解析を開始...');
  document.getElementById('progressWrap').style.display = 'block';

  // 各行からカム番号を取得（セレクトの現在値）
  const cameras = cameraRows.map(r => ({
    path: r.path.trim(),
    cam: parseInt(r.camSelect.value),
  }));

  const args = {
    cameras: cameras,
    cutaway: parseInt(document.getElementById('cutawayCam').value),
    offset: parseFloat(document.getElementById('offsetInput').value) || 0.0,
    settings: {
      threshold_db: parseFloat(document.getElementById('thresholdDb').value),
      min_speech_sec: parseFloat(document.getElementById('minSpeechSec').value),
      smoothing_sec: parseFloat(document.getElementById('smoothingSec').value),
      min_interval_sec: parseFloat(document.getElementById('minIntervalSec').value),
      overlap_cutaway_sec: parseFloat(document.getElementById('overlapCutawaySec').value),
      silence_cutaway_sec: parseFloat(document.getElementById('silenceCutawaySec').value),
    }
  };

  log('解析開始: ' + cameras.length + ' 人分の音声を解析');
  cameras.forEach((c, i) => {
    log('  Person ' + (i + 1) + ': ' + path.basename(c.path) + ' → CAM ' + c.cam);
  });

  const proc = spawn(PYTHON_BIN, [ANALYZER_SCRIPT, JSON.stringify(args)], {
    env: Object.assign({}, process.env, { KMP_DUPLICATE_LIB_OK: 'TRUE' })
  });

  let buffer = '';

  proc.stdout.on('data', (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop();
    lines.forEach(line => {
      line = line.trim();
      if (!line) return;
      try {
        const msg = JSON.parse(line);
        handleAnalyzerMessage(msg);
      } catch (e) {
        log(line);
      }
    });
  });

  proc.stderr.on('data', (data) => {
    log('[stderr] ' + data.toString().trim());
  });

  proc.on('close', (code) => {
    isRunning = false;
    enableUI();
    if (code !== 0 && cutList.length === 0) {
      showError('解析がエラーで終了しました (code: ' + code + ')');
    }
  });

  proc.on('error', (err) => {
    isRunning = false;
    enableUI();
    showError('起動エラー: ' + err.message);
  });
}

function handleAnalyzerMessage(msg) {
  if (msg.type === 'progress') {
    updateProgress(msg.value, msg.message);
  } else if (msg.type === 'log') {
    appendLog(msg.message);
  } else if (msg.type === 'result') {
    cutList = msg.cuts || [];
    showResult(msg);
    document.getElementById('applyBtn').disabled = (cutList.length === 0);
  } else if (msg.type === 'error') {
    showError(msg.message);
  }
}

// ---- Premiereに適用 ----

async function applyToPremiere() {
  if (!cutList.length) {
    alert('先に解析を実行してください');
    return;
  }
  if (isRunning) return;

  isRunning = true;
  disableUI();
  setProgress(0, 'Premiereへの適用を開始...');
  document.getElementById('progressWrap').style.display = 'block';

  log('Premiere Pro を前面に表示...');
  await activatePremiere();

  const sorted = [...cutList].sort((a, b) => a.time_sec - b.time_sec);
  let done = 0;
  let failed = 0;

  for (const cut of sorted) {
    try {
      const ticks = Math.round(cut.time_sec * TICKS_PER_SEC).toString();
      await evalScriptAsync('setPlayheadPosition("' + ticks + '")');
      await cutAndSwitch(cut.cam);
      done++;
      appendLog('[適用] ' + formatTime(cut.time_sec) + ' → CAM ' + cut.cam + ' (' + cut.reason + ')');
    } catch (e) {
      failed++;
      appendLog('[警告] ' + formatTime(cut.time_sec) + ' 失敗: ' + e.message);
    }
    updateProgress(Math.round((done + failed) / sorted.length * 100), (done + failed) + '/' + sorted.length + '件');
  }

  appendLog('[完了] ' + done + '/' + sorted.length + '件 適用 (失敗: ' + failed + '件)');
  setProgress(100, '適用完了');
  isRunning = false;
  enableUI();
}

function evalScriptAsync(script) {
  return new Promise((resolve, reject) => {
    csInterface.evalScript(script, (result) => {
      if (result === 'EvalScript error.') reject(new Error(result));
      else resolve(result);
    });
  });
}

function activatePremiere() {
  return new Promise((resolve) => {
    exec("osascript -e 'tell application \"Adobe Premiere Pro 2025\" to activate' -e 'delay 0.5'", () => {
      resolve();
    });
  });
}

function cutAndSwitch(cam) {
  const keycode = CAM_KEYCODES[cam];
  if (!keycode) return Promise.reject(new Error('invalid cam: ' + cam));

  return new Promise((resolve, reject) => {
    const appleScript = `tell application "System Events"
    tell process "Adobe Premiere Pro 2025"
        key down command
        key code 40
        key up command
        delay 0.1
        key code ${keycode}
    end tell
end tell`;
    exec(`osascript << 'HEREDOC'\n${appleScript}\nHEREDOC`, (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

// ---- UI ヘルパー ----

function disableUI() {
  document.getElementById('analyzeBtn').disabled = true;
  document.getElementById('applyBtn').disabled = true;
  document.getElementById('analyzeBtn').textContent = '解析中...';
}

function enableUI() {
  document.getElementById('analyzeBtn').disabled = false;
  document.getElementById('analyzeBtn').textContent = '解析開始';
  document.getElementById('applyBtn').disabled = (cutList.length === 0);
}

function setProgress(pct, msg) {
  document.getElementById('progressBar').style.width = pct + '%';
  document.getElementById('progressMsg').textContent = msg;
}

function updateProgress(pct, msg) {
  setProgress(pct, msg);

  // ステップインジケーター更新
  const steps = ['step-loading', 'step-detecting', 'step-building'];
  let activeIdx = 0;
  if (pct >= 70) activeIdx = 2;
  else if (pct >= 30) activeIdx = 1;
  else activeIdx = 0;

  if (pct >= 100) {
    steps.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.className = 'step done';
    });
  } else {
    steps.forEach((id, i) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (i < activeIdx) el.className = 'step done';
      else if (i === activeIdx) el.className = 'step active';
      else el.className = 'step';
    });
  }
}

function resetResult() {
  document.getElementById('resultWrap').style.display = 'none';
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('progressMsg').textContent = '準備中...';
  ['step-loading', 'step-detecting', 'step-building'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = 'step';
  });
}

function showResult(msg) {
  const wrap = document.getElementById('resultWrap');
  wrap.style.display = 'block';
  wrap.className = 'result-wrap';
  document.getElementById('resultTitle').textContent = '解析完了';
  document.getElementById('resultStats').textContent =
    'カット件数: ' + (msg.count || msg.cuts.length) + '件';
  log('解析完了: ' + (msg.count || msg.cuts.length) + '件のカットポイントを生成');
  if (msg.cuts && msg.cuts.length > 0) {
    const preview = msg.cuts.slice(0, 10);
    preview.forEach(c => {
      log('  ' + formatTime(c.time_sec) + ' → CAM ' + c.cam + ' (' + c.reason + ')');
    });
    if (msg.cuts.length > 10) {
      log('  ... 残り ' + (msg.cuts.length - 10) + '件は省略');
    }
  }
}

function showError(msg) {
  appendLog('エラー: ' + msg);
  const wrap = document.getElementById('resultWrap');
  wrap.style.display = 'block';
  wrap.className = 'result-wrap error';
  document.getElementById('resultTitle').textContent = 'エラー';
  document.getElementById('resultStats').textContent = msg;
}

function log(msg) {
  appendLog(msg);
}

function appendLog(msg) {
  const box = document.getElementById('logBox');
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);
  box.value += '[' + ts + '] ' + msg + '\n';
  box.scrollTop = box.scrollHeight;
}

function formatTime(sec) {
  const totalMs = Math.round(Math.max(0, sec) * 1000);
  const ms = totalMs % 1000;
  const totalSec = Math.floor(totalMs / 1000);
  const s = totalSec % 60;
  const totalMin = Math.floor(totalSec / 60);
  const m = totalMin % 60;
  const h = Math.floor(totalMin / 60);
  return (
    String(h).padStart(2, '0') + ':' +
    String(m).padStart(2, '0') + ':' +
    String(s).padStart(2, '0') + '.' +
    String(ms).padStart(3, '0')
  );
}
