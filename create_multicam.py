#!/usr/bin/env python3
"""
ATEM Mini Pro ISO → Premiere Pro マルチカムシーケンス自動作成

プロジェクトパネル内の cam1/2/3/4 NN.mp4 を録画セット単位で
マルチカムシーケンス（MultiCam_NN）に自動組み立てする。

同期方法: インポイント（全クリップを time=0 に配置）
シーケンス設定: 1080p 59.94 (AVCHD プリセット)

使い方:
  python3 create_multicam.py
  python3 create_multicam.py --dry-run
  python3 create_multicam.py --bridge-dir /tmp/premiere-mcp-bridge
"""

import sys
import os
import json
import time
import uuid
import re
import argparse

# =============================================================================
# 設定
# =============================================================================

BRIDGE_DIR = "/tmp/premiere-mcp-bridge"
POLL_INTERVAL = 0.15
TIMEOUT = 60

# ATEM形式: "L01_S01 CAM 1 01.mp4"
PATTERN_ATEM   = re.compile(r'^(.+?)\s+CAM\s+([1-4])\s+(\d+)\.mp4$', re.IGNORECASE)
# シンプル形式: "cam1 01.mp4"
PATTERN_SIMPLE = re.compile(r'^cam([1-4])\s+(\d+)\.mp4$', re.IGNORECASE)

# ExtendScript ヘルパー（apply_cuts.py と同じ構成）
EXTENDSCRIPT_HELPERS = r"""
function __secondsToTicks(s) { return String(Math.round(s * 254016000000)); }
function __ticksToSeconds(t) { return parseInt(t, 10) / 254016000000; }
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
"""

# =============================================================================
# ブリッジ通信（apply_cuts.py と同じ実装）
# =============================================================================

def execute_script(script_body, timeout=TIMEOUT):
    cmd_id = str(uuid.uuid4())
    cmd_file = os.path.join(BRIDGE_DIR, f"command-{cmd_id}.json")
    res_file = os.path.join(BRIDGE_DIR, f"response-{cmd_id}.json")

    full_script = EXTENDSCRIPT_HELPERS + '(function(){\n' + script_body + '\n})();'
    payload = {
        'id': cmd_id,
        'script': full_script,
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    }

    try:
        with open(cmd_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
    except OSError as e:
        raise RuntimeError(f"ブリッジに書き込めません: {BRIDGE_DIR}\n{e}")

    start = time.time()
    while time.time() - start < timeout:
        try:
            with open(res_file, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            for path in (res_file, cmd_file):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            result = raw.get('result', raw)
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except json.JSONDecodeError:
                    return {'success': False, 'error': result}
            return result
        except (OSError, json.JSONDecodeError):
            time.sleep(POLL_INTERVAL)

    try:
        os.unlink(cmd_file)
    except OSError:
        pass
    raise TimeoutError(
        f"タイムアウト ({timeout}s)。\n"
        "Premiere Pro が開いていて MCP Bridge (CEP) が Start 状態か確認してください。\n"
        f"Temp Directory: {BRIDGE_DIR}"
    )

# =============================================================================
# ExtendScript ラッパー
# =============================================================================

def get_all_project_items():
    """プロジェクトパネルの全クリップ一覧を取得"""
    script = """
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
"""
    return execute_script(script)


def get_existing_sequence_names():
    """既存シーケンス名の一覧を取得"""
    script = """
try {
  var names = [];
  for (var i = 0; i < app.project.sequences.numSequences; i++) {
    names.push(app.project.sequences[i].name);
  }
  return JSON.stringify({ success: true, names: names });
} catch(e) {
  return JSON.stringify({ success: false, error: e.toString() });
}
"""
    return execute_script(script)


def create_multicam_sequence(seq_name, cam_items):
    """
    cam_items: [{name, nodeId}, ...] cam番号昇順
    全クリップを time=0 に配置（インポイント同期）
    """
    node_ids_js = json.dumps([item['nodeId'] for item in cam_items])

    script = f"""
try {{
  var seqName = {json.dumps(seq_name)};
  var nodeIds = {node_ids_js};

  // ── プリセット検索 ──
  var appPath = "";
  try {{
    appPath = (app.path instanceof File) ? app.path.fsName : String(app.path);
  }} catch(pe) {{}}
  while (appPath.length > 1 && appPath.charAt(appPath.length - 1) === "/") {{
    appPath = appPath.slice(0, -1);
  }}

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
  outer: for (var ai = 0; ai < appRoots.length; ai++) {{
    if (!appRoots[ai]) continue;
    for (var ci = 0; ci < presetCandidates.length; ci++) {{
      var ff = new File(appRoots[ai] + presetCandidates[ci]);
      if (ff.exists) {{ presetPath = ff.fsName; break outer; }}
    }}
  }}

  if (!presetPath) {{
    return JSON.stringify({{
      success: false,
      error: "1080p59.94 プリセットが見つかりません (appPath=" + appPath + ")"
    }});
  }}

  // ── シーケンス作成 ──
  app.project.createNewSequence(seqName, presetPath);

  // 作成したシーケンスを取得
  var seq = null;
  for (var si = 0; si < app.project.sequences.numSequences; si++) {{
    if (app.project.sequences[si].name === seqName) {{
      seq = app.project.sequences[si];
      break;
    }}
  }}
  if (!seq) {{
    return JSON.stringify({{ success: false, error: "シーケンス作成後に取得できませんでした" }});
  }}

  // ── トラック追加（QE DOM 使用 / TrackCollection.addTrack は PP2026 で非対応）──
  app.project.openSequence(seq.sequenceID);
  app.enableQE();
  var qeSeq = qe.project.getActiveSequence();
  if (!qeSeq) {{
    return JSON.stringify({{ success: false, error: "QE active sequence を取得できませんでした" }});
  }}

  function refreshSequence() {{
    for (var rsi = 0; rsi < app.project.sequences.numSequences; rsi++) {{
      if (app.project.sequences[rsi].name === seqName) {{
        seq = app.project.sequences[rsi];
        app.project.openSequence(seq.sequenceID);
        qeSeq = qe.project.getActiveSequence();
        return;
      }}
    }}
  }}

  function ensureTracks(trackType, neededCount) {{
    var isVideo = trackType === "video";
    var tracks = isVideo ? seq.videoTracks : seq.audioTracks;
    var guard = 0;
    while (tracks.numTracks < neededCount && guard < 32) {{
      var before = tracks.numTracks;
      if (isVideo && qeSeq.addVideoTrack) {{
        qeSeq.addVideoTrack();
      }} else if (!isVideo && qeSeq.addAudioTrack) {{
        qeSeq.addAudioTrack();
      }} else if (qeSeq.addTracks) {{
        qeSeq.addTracks(isVideo ? 1 : 0, isVideo ? 0 : 1, 0);
      }} else {{
        return "QE sequence に " + trackType + " トラック追加APIがありません";
      }}

      refreshSequence();
      tracks = isVideo ? seq.videoTracks : seq.audioTracks;
      if (tracks.numTracks <= before) {{
        return trackType + " トラック数が増えませんでした";
      }}
      guard++;
    }}
    return tracks.numTracks >= neededCount ? "" : trackType + " トラック確保に失敗しました";
  }}

  var needed = nodeIds.length;
  var videoTrackError = ensureTracks("video", needed);
  if (videoTrackError) {{
    return JSON.stringify({{ success: false, error: videoTrackError }});
  }}
  var audioTrackError = ensureTracks("audio", needed);
  if (audioTrackError) {{
    return JSON.stringify({{ success: false, error: audioTrackError }});
  }}

  // ── 各カメラをトラックに配置（time=0 = インポイント同期）──
  var placed = 0;
  for (var ci = 0; ci < nodeIds.length; ci++) {{
    var camItem = __findItemByNodeId(nodeIds[ci]);
    if (!camItem) continue;
    try {{
      seq.videoTracks[ci].overwriteClip(camItem, 0);
      placed++;
    }} catch(ce) {{
      // overwriteClip が使えない場合は insertClip にフォールバック
      try {{
        seq.videoTracks[ci].insertClip(camItem, 0);
        placed++;
      }} catch(ie) {{ /* skip */ }}
    }}
  }}

  return JSON.stringify({{
    success: true,
    sequenceId: seq.sequenceID,
    name: seq.name,
    placedClips: placed,
    preset: presetPath
  }});
}} catch(e) {{
  return JSON.stringify({{ success: false, error: e.toString() }});
}}
"""
    return execute_script(script)


def save_project():
    script = """
try {
  app.project.save();
  return JSON.stringify({ success: true });
} catch(e) {
  return JSON.stringify({ success: false, error: e.toString() });
}
"""
    return execute_script(script)

# =============================================================================
# グルーピング
# =============================================================================

def group_cam_items(items):
    """
    対応形式:
      ATEM形式  : "L01_S01 CAM 1 01.mp4" → グループキー "L01_S01_01"
      シンプル形式: "cam1 01.mp4"          → グループキー "01"
    戻り値: { group_key: {cam_num: item, ...}, ... }
    """
    groups = {}
    for item in items:
        m = PATTERN_ATEM.match(item['name'])
        if m:
            prefix  = m.group(1)
            cam_num = int(m.group(2))
            rec_num = m.group(3)
            group_key = f"{prefix}_{rec_num}"
        else:
            m = PATTERN_SIMPLE.match(item['name'])
            if not m:
                continue
            cam_num   = int(m.group(1))
            rec_num   = m.group(2)
            group_key = rec_num

        if group_key not in groups:
            groups[group_key] = {}
        groups[group_key][cam_num] = item
    return groups

# =============================================================================
# メイン
# =============================================================================

def main():
    global BRIDGE_DIR

    parser = argparse.ArgumentParser(
        description='ATEM Mini Pro ISO → Premiere Pro マルチカムシーケンス作成'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='作成予定のシーケンス一覧を表示するだけ（Premiereは操作しない）')
    parser.add_argument('--bridge-dir', default=BRIDGE_DIR,
                        help=f'ブリッジディレクトリ（デフォルト: {BRIDGE_DIR}）')
    args = parser.parse_args()

    BRIDGE_DIR = args.bridge_dir

    # ブリッジ確認
    if not os.path.isdir(BRIDGE_DIR):
        print(f"❌ ブリッジディレクトリが見つかりません: {BRIDGE_DIR}")
        print("   Premiere Pro + MCP Bridge (CEP) を起動してください。")
        sys.exit(1)

    # ── Step 1: プロジェクトアイテム取得 ──
    print("📋 プロジェクトアイテムを取得中...")
    result = get_all_project_items()
    if not result.get('success'):
        print(f"❌ 取得失敗: {result.get('error')}")
        sys.exit(1)

    groups = group_cam_items(result['items'])

    if not groups:
        print("⚠️  cam[1-4] NN.mp4 パターンのファイルが見つかりません。")
        print("   期待するファイル名: 'cam1 01.mp4', 'cam2 01.mp4' など")
        sys.exit(0)

    # 検出結果を表示
    print(f"\n✅ {len(groups)} 録画セット検出:")
    for group_key in sorted(groups.keys()):
        cams = sorted(groups[group_key].keys())
        names = [groups[group_key][c]['name'] for c in cams]
        missing = [c for c in [1, 2, 3, 4] if c not in groups[group_key]]
        missing_str = f"  ⚠️ cam{missing} 不足" if missing else ""
        print(f"   セット {group_key}: {', '.join(names)}{missing_str}")

    if args.dry_run:
        print("\n[dry-run] 作成予定シーケンス:")
        for group_key in sorted(groups.keys()):
            print(f"   MultiCam_{group_key}")
        return

    # ── Step 2: 既存シーケンス名を取得 ──
    existing = get_existing_sequence_names()
    existing_names = set(existing.get('names', []))

    created, skipped, failed = [], [], []

    # ── Step 3: 各セットを処理 ──
    print()
    for group_key in sorted(groups.keys()):
        seq_name = f"MultiCam_{group_key}"

        if seq_name in existing_names:
            print(f"⏭️  スキップ: {seq_name}（既存）")
            skipped.append(seq_name)
            continue

        group = groups[group_key]
        cam_items = [group[c] for c in sorted(group.keys())]

        print(f"🎬 作成中: {seq_name} ({len(cam_items)}カメラ)...", end='', flush=True)
        res = create_multicam_sequence(seq_name, cam_items)

        if res.get('success'):
            placed = res.get('placedClips', '?')
            print(f" ✅ ({placed}クリップ配置)")
            created.append(seq_name)
        else:
            print(f" ❌\n   エラー: {res.get('error')}")
            failed.append(seq_name)

    # ── Step 4: 保存 ──
    if created:
        print("\n💾 プロジェクト保存中...", end='', flush=True)
        save_result = save_project()
        print(" ✅" if save_result.get('success') else f" ⚠️ {save_result.get('error')}")

    # ── サマリー ──
    print(f"\n{'='*40}")
    print(f"作成: {len(created)}件 / スキップ: {len(skipped)}件 / 失敗: {len(failed)}件")
    if created:
        print(f"作成済み: {', '.join(created)}")
    if failed:
        print(f"失敗: {', '.join(failed)}")
        print("\n💡 ヒント: プリセットが見つからない場合は Premiere Pro のバージョスを確認してください。")


if __name__ == '__main__':
    main()
