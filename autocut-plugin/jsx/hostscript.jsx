/**
 * hostscript.jsx
 * AutoCut CEPパネル用 ExtendScript
 * Premiere Pro との通信を担当する
 */

/**
 * サブシーケンス（マルチカメラソースシーケンス等）から
 * ミュートされていない音声トラックのメディアパスを取得する。
 * ミュートされていないトラックを優先し、見つからない場合は全トラックからフォールバック。
 * @param {Sequence} subSeq - 検索対象のシーケンス
 * @returns {string|null} メディアパス（見つからない場合はnull）
 */
function getUnmutedAudioFromSequence(subSeq) {
    if (!subSeq) return null;
    var audioTracks = subSeq.audioTracks;
    if (!audioTracks) return null;
    var numAudio = audioTracks.numTracks;

    // Pass 1: ミュートされていないトラックを優先
    for (var ai = 0; ai < numAudio; ai++) {
        var aTrack = audioTracks[ai];
        var isMuted = false;
        try { isMuted = !!aTrack.isMute; } catch(e) {}
        if (isMuted) continue;
        for (var ac = 0; ac < aTrack.clips.numItems; ac++) {
            var aClip = aTrack.clips[ac];
            if (!aClip.projectItem) continue;
            try {
                var aPath = aClip.projectItem.getMediaPath();
                if (aPath && aPath.length > 0) return aPath;
            } catch(e2) {}
        }
    }

    // Pass 2: ミュート状態を問わず最初に見つかったパスを返す
    for (var ai2 = 0; ai2 < numAudio; ai2++) {
        var aTrack2 = audioTracks[ai2];
        for (var ac2 = 0; ac2 < aTrack2.clips.numItems; ac2++) {
            var aClip2 = aTrack2.clips[ac2];
            if (!aClip2.projectItem) continue;
            try {
                var aPath2 = aClip2.projectItem.getMediaPath();
                if (aPath2 && aPath2.length > 0) return aPath2;
            } catch(e3) {}
        }
    }

    // Pass 3: ビデオトラックにも映像+音声複合ファイルがある場合に対応
    var videoTracks = subSeq.videoTracks;
    if (!videoTracks) return null;
    var numVideo = videoTracks.numTracks;
    for (var vi = 0; vi < numVideo; vi++) {
        var vTrack = videoTracks[vi];
        for (var vc = 0; vc < vTrack.clips.numItems; vc++) {
            var vClip = vTrack.clips[vc];
            if (!vClip.projectItem) continue;
            try {
                var vPath = vClip.projectItem.getMediaPath();
                if (vPath && vPath.length > 0) return vPath;
            } catch(e4) {}
        }
    }

    return null;
}

/**
 * アクティブシーケンスの情報を取得する。
 * 通常クリップとマルチカメラシーケンスの両方に対応。
 * @returns {string} JSON文字列 { sequenceName, fps, totalDuration, clipPath, isMultiCam }
 */
function getActiveSequenceInfo() {
    try {
        if (!app.project || !app.project.activeSequence) {
            return JSON.stringify({ error: "アクティブなシーケンスがありません" });
        }

        var seq = app.project.activeSequence;
        var settings = seq.getSettings();

        // フレームレートを計算（Premiereはticks/secondで保持）
        // settings.videoFrameRate は1フレームあたりの秒数
        var fps = Math.round(1.0 / settings.videoFrameRate.seconds);

        // シーケンスの総フレーム数から尺を秒で計算
        var totalDuration = seq.end / seq.timebase;

        // ---- Pass 1: トラック0から全クリップを時系列順に収集 ----
        var clips = [];
        var unresolvedClips = []; // Pass 1でメディアパスが取得できなかったクリップ（マルチカム候補）
        var isMultiCam = false;
        var videoTracks = seq.videoTracks;
        if (videoTracks.numTracks > 0) {
            var track = videoTracks[0];
            for (var j = 0; j < track.clips.numItems; j++) {
                var clip = track.clips[j];
                if (!clip.projectItem) continue;
                var resolved = false;
                try {
                    var mediaPath = clip.projectItem.getMediaPath();
                    if (mediaPath && mediaPath.length > 0) {
                        clips.push({
                            path: mediaPath,
                            timelineStart: clip.start.seconds,
                            timelineEnd: clip.end.seconds,
                            mediaInPoint: clip.inPoint.seconds,
                            mediaOutPoint: clip.outPoint.seconds
                        });
                        resolved = true;
                    }
                } catch (e) {}
                if (!resolved) {
                    unresolvedClips.push(clip);
                }
            }
            // 時系列順にソート
            clips.sort(function(a, b) { return a.timelineStart - b.timelineStart; });
        }

        // ---- Pass 2: マルチカメラシーケンス対応（未解決クリップ、または全クリップ未解決の場合）----
        var numSeqs = app.project.sequences.numSequences;
        for (var ui = 0; ui < unresolvedClips.length; ui++) {
            var mClip = unresolvedClips[ui];
            if (!mClip.projectItem) continue;
            var mcItemName = mClip.projectItem.name;
            for (var ms = 0; ms < numSeqs; ms++) {
                var subSeq = app.project.sequences[ms];
                if (subSeq.sequenceID === seq.sequenceID) continue;
                if (subSeq.name === mcItemName) {
                    var mcPath = getUnmutedAudioFromSequence(subSeq);
                    if (mcPath) {
                        clips.push({
                            path: mcPath,
                            timelineStart: mClip.start.seconds,
                            timelineEnd: mClip.end.seconds,
                            mediaInPoint: mClip.inPoint.seconds,
                            mediaOutPoint: mClip.outPoint.seconds
                        });
                        isMultiCam = true;
                        break;
                    }
                }
            }
        }

        // 再ソート（Pass 2で追加されたクリップを含む）
        clips.sort(function(a, b) { return a.timelineStart - b.timelineStart; })

        if (clips.length === 0) {
            return JSON.stringify({ error: "シーケンス内にビデオ・音声クリップが見つかりません（マルチカメラソースシーケンスも検索済み）" });
        }

        return JSON.stringify({
            sequenceName: seq.name,
            fps: fps,
            totalDuration: totalDuration,
            clips: clips,
            clipPath: clips[0].path,  // 後方互換
            isMultiCam: isMultiCam
        });

    } catch (e) {
        return JSON.stringify({ error: "エラー: " + e.message });
    }
}

/**
 * JSONファイルを読み込んでアクティブシーケンスを複製し、QE DOMで直接カットを適用する
 * @param {string} jsonPath - カット情報JSONファイルのパス
 * @returns {string} JSON文字列 { success: bool, message: string, newName: string }
 */
function applyAutoCutFromFile(jsonPath) {
    var lastOp = "init";
    var debugInfo = "";
    try {
        // JSON ファイルを読む
        lastOp = "file-open";
        var f = new File(jsonPath);
        if (!f.exists) {
            return JSON.stringify({ success: false, message: "JSONファイルが見つかりません: " + jsonPath });
        }
        f.encoding = "UTF-8";
        f.open("r");
        lastOp = "file-read";
        var jsonStr = f.read();
        f.close();

        var data;
        try {
            data = eval("(" + jsonStr + ")");
        } catch (e) {
            return JSON.stringify({ success: false, message: "JSON解析エラー: " + e.message });
        }

        var cuts = data.cuts || [];
        var sequenceName = data.sequence_name || "AutoCut";

        // アクティブシーケンスを取得
        lastOp = "get-seq";
        var seq = app.project.activeSequence;
        if (!seq) {
            return JSON.stringify({ success: false, message: "アクティブなシーケンスがありません" });
        }

        // シーケンスを複製
        lastOp = "clone-seq";
        var clonedName = sequenceName + "_AutoCut";
        var beforeCount = app.project.sequences.numSequences;
        var newSeq = seq.clone();
        if (!newSeq) {
            return JSON.stringify({ success: false, message: "シーケンスの複製に失敗しました" });
        }

        // clone()後にプロジェクトに追加されたシーケンスを末尾から取得
        lastOp = "find-cloned-seq";
        var afterCount = app.project.sequences.numSequences;
        debugInfo = "before=" + beforeCount + " after=" + afterCount;
        if (afterCount <= beforeCount) {
            return JSON.stringify({ success: false, message: "シーケンスの複製に失敗しました（count変化なし）" });
        }
        // 末尾が複製されたシーケンス
        newSeq = app.project.sequences[afterCount - 1];
        newSeq.name = clonedName;

        if (cuts.length === 0) {
            return JSON.stringify({ success: true, message: "カットなし。シーケンスを複製しました", newName: clonedName });
        }

        // 複製したシーケンスをアクティブにする
        lastOp = "open-seq";
        var foundSeqId = newSeq.sequenceID;
        debugInfo = "foundSeqId=" + foundSeqId + " clonedName=" + clonedName;
        if (!foundSeqId) {
            return JSON.stringify({ success: false, message: "複製シーケンスのIDが取得できません" });
        }
        app.project.openSequence(foundSeqId);

        // QE DOM を有効化
        lastOp = "enable-qe";
        app.enableQE();
        lastOp = "get-qe-seq";
        var qeSeq = qe.project.getActiveSequence();
        if (!qeSeq) {
            return JSON.stringify({ success: false, message: "QEシーケンスの取得に失敗しました" });
        }

        // FPS 計算（timebase = ticks/frame, 1秒 = 254016000000 ticks）
        lastOp = "calc-fps";
        var timebase = parseInt(newSeq.timebase, 10);
        var fps = 254016000000 / timebase;
        var numVideo = newSeq.videoTracks.numTracks;
        var numAudio = newSeq.audioTracks.numTracks;
        debugInfo = "fps=" + fps + " tb=" + timebase + " nV=" + numVideo + " nA=" + numAudio;

        function secToTC(sec) {
            var totalFrames = Math.round(sec * fps);
            var h = Math.floor(totalFrames / (fps * 3600));
            var m = Math.floor((totalFrames % (fps * 3600)) / (fps * 60));
            var s = Math.floor((totalFrames % (fps * 60)) / fps);
            var fr = Math.round(totalFrames % fps);
            function pad(n) { return n < 10 ? "0" + n : "" + n; }
            return pad(h) + ":" + pad(m) + ":" + pad(s) + ":" + pad(fr);
        }

        // カットを後ろから前に処理（ripple削除でタイムラインがずれるため）
        var sortedCuts = cuts.slice().sort(function(a, b) { return b.start - a.start; });
        var cutCount = 0;

        for (var i = 0; i < sortedCuts.length; i++) {
            var cut = sortedCuts[i];
            var startTC = secToTC(cut.start);
            var endTC = secToTC(cut.end);

            // デュレーション0のカットはスキップ
            if (startTC === endTC) { continue; }

            // QEシーケンスを毎回再取得（clip.remove後に参照が無効になるため）
            lastOp = "re-get-qe[" + i + "]";
            app.enableQE();
            var qeSeqCur = qe.project.getActiveSequence();
            if (!qeSeqCur) { continue; }

            // 全トラックにrazor
            lastOp = "razor[" + i + "]:" + startTC + "-" + endTC;
            for (var vi = 0; vi < numVideo; vi++) {
                var vt = qeSeqCur.getVideoTrackAt(vi);
                if (vt) { vt.razor(endTC); vt.razor(startTC); }
            }
            for (var ai = 0; ai < numAudio; ai++) {
                var at = qeSeqCur.getAudioTrackAt(ai);
                if (at) { at.razor(endTC); at.razor(startTC); }
            }

            // カット範囲内のクリップを削除
            lastOp = "remove[" + i + "]:" + startTC + "-" + endTC;
            for (var v = 0; v < numVideo; v++) {
                var vTrack = newSeq.videoTracks[v];
                for (var c = vTrack.clips.numItems - 1; c >= 0; c--) {
                    var clip = vTrack.clips[c];
                    var clipStart = clip.start.seconds;
                    var clipEnd = clip.end.seconds;
                    if (clipStart >= cut.start - 0.02 && clipEnd <= cut.end + 0.02) {
                        clip.remove(true, true);
                    }
                }
            }
            for (var a = 0; a < numAudio; a++) {
                var aTrack = newSeq.audioTracks[a];
                for (var ac = aTrack.clips.numItems - 1; ac >= 0; ac--) {
                    var aClip = aTrack.clips[ac];
                    var aStart = aClip.start.seconds;
                    var aEnd = aClip.end.seconds;
                    if (aStart >= cut.start - 0.02 && aEnd <= cut.end + 0.02) {
                        aClip.remove(true, true);
                    }
                }
            }
            cutCount++;
        }

        return JSON.stringify({
            success: true,
            message: cutCount + "箇所カット完了",
            newName: clonedName
        });

    } catch (e) {
        return JSON.stringify({ success: false, message: "エラー(" + lastOp + ")[" + debugInfo + "]: " + e.message });
    }
}

/**
 * FCP XMLをプロジェクトにインポートして新シーケンスとして展開する
 * @param {string} xmlPath - FCP XMLファイルのパス
 * @returns {string} JSON文字列 { success: bool, message: string }
 */
function importFCPXML(xmlPath) {
    try {
        if (!app.project) {
            return JSON.stringify({ success: false, message: "プロジェクトが開かれていません" });
        }

        // importFiles でFCP XMLを読み込む
        // Premiere ProはFCP XMLをシーケンスとして自動展開する
        // suppressUI: true でPremiere側のダイアログを抑制する
        var importResult = app.project.importFiles(
            [xmlPath],
            true,                     // suppressUI（エラーダイアログを抑制）
            app.project.rootItem,     // 読み込み先（ルートビン）
            false                     // importAsNumberedStills
        );

        if (importResult) {
            return JSON.stringify({ success: true, message: "インポート完了" });
        } else {
            return JSON.stringify({ success: false, message: "インポートに失敗しました" });
        }

    } catch (e) {
        return JSON.stringify({ success: false, message: "エラー: " + e.message });
    }
}
