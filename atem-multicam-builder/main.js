'use strict';

const csInterface = new CSInterface();

// ── 状態 ──
let scannedGroups = {};      // { recNum: { camNum: {name, nodeId} } }
let existingNames = new Set();
let existingSequences = {};   // { sequenceName: { videoClipCount, audioClipCount } }
let isRunning = false;

// ── DOM ──
const btnScan  = document.getElementById('btn-scan');
const btnBuild = document.getElementById('btn-build');
const badge    = document.getElementById('badge');
const setList  = document.getElementById('set-list');
const logEl    = document.getElementById('log');
const progress = document.getElementById('progress-bar');

btnScan.addEventListener('click', doScan);
btnBuild.addEventListener('click', doBuild);

// =============================================================================
// ユーティリティ
// =============================================================================

function log(msg, type = '') {
  const line = document.createElement('div');
  line.className = 'log-line' + (type ? ' ' + type : '');
  line.textContent = msg;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function setProgress(pct) {
  progress.style.width = pct + '%';
}

function setRunning(val) {
  isRunning = val;
  btnScan.disabled  = val;
  btnBuild.disabled = val
    || Object.keys(scannedGroups).length === 0
    || Object.keys(scannedGroups).every(r => !needsBuild(`MultiCam_${r}`));
}

// evalScript をPromise化
function evalScript(script) {
  return new Promise((resolve, reject) => {
    csInterface.evalScript(script, (result) => {
      if (result === 'EvalScript error.') {
        reject(new Error('ExtendScript error'));
        return;
      }
      try {
        resolve(JSON.parse(result));
      } catch (_) {
        resolve({ success: false, error: result });
      }
    });
  });
}

// =============================================================================
// ExtendScript
// =============================================================================

const ES_HELPERS = `
function __findItemByNodeId(nodeId) {
  var result = null;
  function walk(parent) {
    for (var i = 0; i < parent.children.numItems; i++) {
      var child = parent.children[i];
      if (child.nodeId === nodeId) { result = child; return; }
      if (child.type === ProjectItemType.BIN) { walk(child); }
      if (result) return;
    }
  }
  walk(app.project.rootItem);
  return result;
}
`;

function scriptGetItems() {
  return `(function() {
    try {
      var items = [];
      function walk(parent) {
        for (var i = 0; i < parent.children.numItems; i++) {
          var child = parent.children[i];
          if (child.type === ProjectItemType.CLIP) {
            items.push({ name: child.name, nodeId: child.nodeId });
          } else if (child.type === ProjectItemType.BIN) {
            walk(child);
          }
        }
      }
      walk(app.project.rootItem);
      return JSON.stringify({ success: true, items: items });
    } catch(e) {
      return JSON.stringify({ success: false, error: e.toString() });
    }
  })()`;
}

function scriptGetSequenceInfo() {
  return `(function() {
    try {
      var sequences = [];
      for (var i = 0; i < app.project.sequences.numSequences; i++) {
        var seq = app.project.sequences[i];
        var videoClipCount = 0;
        var audioClipCount = 0;
        for (var v = 0; v < seq.videoTracks.numTracks; v++) {
          videoClipCount += seq.videoTracks[v].clips.numItems;
        }
        for (var a = 0; a < seq.audioTracks.numTracks; a++) {
          audioClipCount += seq.audioTracks[a].clips.numItems;
        }
        sequences.push({
          name: seq.name,
          sequenceID: seq.sequenceID,
          videoTracks: seq.videoTracks.numTracks,
          audioTracks: seq.audioTracks.numTracks,
          videoClipCount: videoClipCount,
          audioClipCount: audioClipCount
        });
      }
      return JSON.stringify({ success: true, sequences: sequences });
    } catch(e) {
      return JSON.stringify({ success: false, error: e.toString() });
    }
  })()`;
}

function scriptCreateMulticam(seqName, nodeIds) {
  const nodeIdsJson = JSON.stringify(nodeIds);
  const seqNameJson = JSON.stringify(seqName);
  return ES_HELPERS + `(function() {
    try {
      var seqName   = ${seqNameJson};
      var nodeIds   = ${nodeIdsJson};

      // ── プリセット検索 ──
      var appPath = "";
      try {
        appPath = (app.path instanceof File) ? app.path.fsName : String(app.path);
      } catch(pe) {}
      while (appPath.length > 1 && appPath.charAt(appPath.length - 1) === "/") {
        appPath = appPath.slice(0, -1);
      }

      // PP2026以降はパス・ファイル名が変更された
      var presetCandidates = [
        "/Contents/Settings/SequencePresets/HD 1080p/HD 1080p 59.94 fps.sqpreset",
        "/Contents/Settings/SequencePresets/AVCHD/1080p/AVCHD 1080p59.94.sqpreset",
        "/Contents/Settings/Sequence Presets/AVCHD/1080p/AVCHD 1080p59.94.sqpreset"
      ];
      var appRoots = [
        appPath,
        "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app",
        "/Applications/Adobe Premiere Pro 2025/Adobe Premiere Pro 2025.app",
        "/Applications/Adobe Premiere Pro 2024/Adobe Premiere Pro 2024.app",
        "/Applications/Adobe Premiere Pro 2023/Adobe Premiere Pro 2023.app"
      ];

      var presetPath = "";
      outer: for (var ai = 0; ai < appRoots.length; ai++) {
        if (!appRoots[ai]) continue;
        for (var ci = 0; ci < presetCandidates.length; ci++) {
          var ff = new File(appRoots[ai] + presetCandidates[ci]);
          if (ff.exists) { presetPath = ff.fsName; break outer; }
        }
      }

      if (!presetPath) {
        return JSON.stringify({
          success: false,
          error: "1080p59.94 プリセットが見つかりません (appPath=" + appPath + ")"
        });
      }

      var seq = null;
      for (var si = 0; si < app.project.sequences.numSequences; si++) {
        if (app.project.sequences[si].name === seqName) {
          seq = app.project.sequences[si];
          break;
        }
      }

      var reusedExisting = false;
      if (seq) {
        var existingVideoClips = 0;
        var existingAudioClips = 0;
        for (var ev = 0; ev < seq.videoTracks.numTracks; ev++) {
          existingVideoClips += seq.videoTracks[ev].clips.numItems;
        }
        for (var ea = 0; ea < seq.audioTracks.numTracks; ea++) {
          existingAudioClips += seq.audioTracks[ea].clips.numItems;
        }
        if (existingVideoClips > 0 || existingAudioClips > 0) {
          return JSON.stringify({
            success: true,
            sequenceId: seq.sequenceID,
            placed: 0,
            skipped: true,
            reason: "既存シーケンスにクリップあり"
          });
        }
        reusedExisting = true;
      } else {
        // ── シーケンス作成 ──
        app.project.createNewSequence(seqName, presetPath);

        for (var si2 = 0; si2 < app.project.sequences.numSequences; si2++) {
          if (app.project.sequences[si2].name === seqName) {
            seq = app.project.sequences[si2];
            break;
          }
        }
        if (!seq) return JSON.stringify({ success: false, error: "シーケンス作成後に取得失敗" });
      }

      // ── トラック追加（QE DOM 使用 / TrackCollection.addTrack は PP2026 で非対応）──
      app.project.openSequence(seq.sequenceID);
      app.enableQE();
      var qeSeq = qe.project.getActiveSequence();
      if (!qeSeq) return JSON.stringify({ success: false, error: "QE active sequence を取得失敗" });

      function refreshSequence() {
        for (var rsi = 0; rsi < app.project.sequences.numSequences; rsi++) {
          if (app.project.sequences[rsi].name === seqName) {
            seq = app.project.sequences[rsi];
            app.project.openSequence(seq.sequenceID);
            qeSeq = qe.project.getActiveSequence();
            return;
          }
        }
      }

      function ensureTracks(trackType, neededCount) {
        var isVideo = trackType === "video";
        var tracks = isVideo ? seq.videoTracks : seq.audioTracks;
        var guard = 0;
        while (tracks.numTracks < neededCount && guard < 32) {
          var before = tracks.numTracks;
          if (isVideo && qeSeq.addVideoTrack) {
            qeSeq.addVideoTrack();
          } else if (!isVideo && qeSeq.addAudioTrack) {
            qeSeq.addAudioTrack();
          } else if (qeSeq.addTracks) {
            qeSeq.addTracks(isVideo ? 1 : 0, isVideo ? 0 : 1, 0);
          } else {
            return "QE sequence に " + trackType + " トラック追加APIがありません";
          }

          refreshSequence();
          tracks = isVideo ? seq.videoTracks : seq.audioTracks;
          if (tracks.numTracks <= before) return trackType + " トラック数が増えませんでした";
          guard++;
        }
        return tracks.numTracks >= neededCount ? "" : trackType + " トラック確保に失敗しました";
      }

      var needed = nodeIds.length;
      var videoTrackError = ensureTracks("video", needed);
      if (videoTrackError) return JSON.stringify({ success: false, error: videoTrackError });
      var audioTrackError = ensureTracks("audio", needed);
      if (audioTrackError) return JSON.stringify({ success: false, error: audioTrackError });

      // ── クリップ配置（time=0 = インポイント同期）──
      var placed = 0;
      for (var ci = 0; ci < nodeIds.length; ci++) {
        var item = __findItemByNodeId(nodeIds[ci]);
        if (!item) continue;
        try {
          seq.videoTracks[ci].overwriteClip(item, 0);
          placed++;
        } catch(e1) {
          try {
            seq.videoTracks[ci].insertClip(item, 0);
            placed++;
          } catch(e2) {}
        }
      }

      return JSON.stringify({
        success: true,
        sequenceId: seq.sequenceID,
        placed: placed,
        repaired: reusedExisting
      });
    } catch(e) {
      return JSON.stringify({ success: false, error: e.toString() });
    }
  })()`;
}

function scriptSave() {
  return `(function() {
    try { app.project.save(); return JSON.stringify({ success: true }); }
    catch(e) { return JSON.stringify({ success: false, error: e.toString() }); }
  })()`;
}

// =============================================================================
// グルーピング
// =============================================================================

// ATEM形式: "L01_S01 CAM 1 01.mp4"
const PATTERN_ATEM   = /^(.+?)\s+CAM\s+([1-4])\s+(\d+)\.mp4$/i;
// シンプル形式: "cam1 01.mp4"
const PATTERN_SIMPLE = /^cam([1-4])\s+(\d+)\.mp4$/i;

function groupCamItems(items) {
  const groups = {};
  for (const item of items) {
    let camNum, groupKey;
    let m = item.name.match(PATTERN_ATEM);
    if (m) {
      const prefix = m[1];   // e.g. "L01_S01"
      camNum   = parseInt(m[2]);
      groupKey = `${prefix}_${m[3]}`;  // e.g. "L01_S01_01"
    } else {
      m = item.name.match(PATTERN_SIMPLE);
      if (!m) continue;
      camNum   = parseInt(m[1]);
      groupKey = m[2];  // e.g. "01"
    }
    if (!groups[groupKey]) groups[groupKey] = {};
    groups[groupKey][camNum] = item;
  }
  return groups;
}

function indexSequences(sequences) {
  const byName = {};
  for (const seq of sequences || []) {
    byName[seq.name] = seq;
  }
  return byName;
}

function needsBuild(seqName) {
  const seq = existingSequences[seqName];
  if (!seq) return true;
  return (seq.videoClipCount || 0) === 0 && (seq.audioClipCount || 0) === 0;
}

// =============================================================================
// UI 更新
// =============================================================================

function renderSetList(groups, existingNameSet) {
  setList.innerHTML = '';

  const recNums = Object.keys(groups).sort();
  if (recNums.length === 0) {
    setList.innerHTML = '<li><span class="placeholder">cam[1-4] NN.mp4 が見つかりません</span></li>';
    badge.textContent = '0';
    badge.className = 'badge none';
    return;
  }

  badge.textContent = recNums.length + ' セット';
  badge.className = 'badge';

  for (const recNum of recNums) {
    const group = groups[recNum];
    const seqName = `MultiCam_${recNum}`;
    const seqInfo = existingSequences[seqName];
    const isExisting = existingNameSet.has(seqName);
    const shouldRepair = isExisting && needsBuild(seqName);
    const cams = Object.keys(group).sort().map(c => `cam${c}`).join(' / ');
    const missing = [1,2,3,4].filter(c => !group[c]);

    const li = document.createElement('li');

    const numEl = document.createElement('span');
    numEl.className = 'set-num';
    numEl.textContent = recNum;

    const camEl = document.createElement('span');
    camEl.className = 'set-cams';
    camEl.textContent = cams + (missing.length ? ` ⚠️ cam${missing.join(',')}欠損` : '');

    const statusEl = document.createElement('span');
    if (shouldRepair) {
      statusEl.className = 'set-status new';
      statusEl.textContent = '修復';
    } else if (isExisting) {
      statusEl.className = 'set-status skip';
      statusEl.textContent = `既存 ${seqInfo.videoClipCount || 0}`;
    } else {
      statusEl.className = 'set-status new';
      statusEl.textContent = '新規';
    }

    li.appendChild(numEl);
    li.appendChild(camEl);
    li.appendChild(statusEl);
    setList.appendChild(li);
  }
}

// =============================================================================
// スキャン
// =============================================================================

async function doScan() {
  if (isRunning) return;
  setRunning(true);
  logEl.innerHTML = '';
  setProgress(10);
  log('スキャン中...', 'info');

  try {
    const [itemsResult, seqResult] = await Promise.all([
      evalScript(scriptGetItems()),
      evalScript(scriptGetSequenceInfo())
    ]);

    if (!itemsResult.success) {
      log('❌ ' + itemsResult.error, 'err');
      return;
    }

    scannedGroups = groupCamItems(itemsResult.items);
    existingSequences = seqResult.success ? indexSequences(seqResult.sequences) : {};
    existingNames = new Set(Object.keys(existingSequences));

    renderSetList(scannedGroups, existingNames);

    const total = Object.keys(scannedGroups).length;
    const buildCount = Object.keys(scannedGroups).filter(r => needsBuild(`MultiCam_${r}`)).length;

    setProgress(100);
    log(`✅ ${total} セット検出（作成/修復 ${buildCount}件）`, 'ok');

    btnBuild.disabled = buildCount === 0;
    if (buildCount === 0) log('作成/修復するセットなし', 'skip');

  } catch(e) {
    log('❌ ' + e.message, 'err');
  } finally {
    setRunning(false);
    setTimeout(() => setProgress(0), 800);
  }
}

// =============================================================================
// マルチカム作成
// =============================================================================

async function doBuild() {
  if (isRunning) return;

  const recNums = Object.keys(scannedGroups)
    .sort()
    .filter(r => needsBuild(`MultiCam_${r}`));

  if (recNums.length === 0) {
    log('作成対象なし', 'warn');
    return;
  }

  setRunning(true);
  log(`\n🎬 ${recNums.length} セットを作成/修復します...`, 'info');

  let created = 0;
  let failed  = 0;

  for (let i = 0; i < recNums.length; i++) {
    const recNum = recNums[i];
    const seqName = `MultiCam_${recNum}`;
    const group   = scannedGroups[recNum];
    const nodeIds = Object.keys(group).sort().map(c => group[c].nodeId);

    setProgress(Math.round((i / recNums.length) * 90));
    const isRepair = existingNames.has(seqName);
    log(`  ${isRepair ? '修復中' : '作成中'}: ${seqName}...`);

    try {
      const result = await evalScript(scriptCreateMulticam(seqName, nodeIds));
      if (result.success) {
        const label = result.repaired ? '修復' : '作成';
        log(`  ✅ ${seqName}（${label}: ${result.placed}クリップ配置）`, 'ok');
        existingNames.add(seqName);
        existingSequences[seqName] = {
          name: seqName,
          videoClipCount: result.placed,
          audioClipCount: result.placed
        };
        created++;
      } else {
        log(`  ❌ ${seqName}: ${result.error}`, 'err');
        failed++;
      }
    } catch(e) {
      log(`  ❌ ${seqName}: ${e.message}`, 'err');
      failed++;
    }
  }

  // 保存
  if (created > 0) {
    log('💾 プロジェクト保存中...');
    try {
      await evalScript(scriptSave());
      log('✅ 保存完了', 'ok');
    } catch(e) {
      log('⚠️ 保存失敗: ' + e.message, 'warn');
    }
  }

  setProgress(100);
  log(`\n作成: ${created}件 / 失敗: ${failed}件`, created > 0 ? 'ok' : 'warn');

  // セットリストを再レンダリング（スキップ表示を更新）
  renderSetList(scannedGroups, existingNames);
  btnBuild.disabled = Object.keys(scannedGroups).every(r => !needsBuild(`MultiCam_${r}`));

  setRunning(false);
  setTimeout(() => setProgress(0), 1000);
}
