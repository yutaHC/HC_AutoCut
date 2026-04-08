/**
 * hostscript.jsx
 * MultiCam Switcher — ExtendScript ホストスクリプト
 * Premiere Pro 側で実行されるスクリプト。
 */

/**
 * CTI（Current Time Indicator）を指定ティック位置に移動する。
 * @param {string} ticks — タイムライン上の絶対位置（ティック数の文字列）
 * @returns {string} JSON文字列 {success: boolean, error?: string}
 */
function setPlayheadPosition(ticks) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) return JSON.stringify({success: false, error: "no active sequence"});
        seq.setPlayerPosition(ticks);
        return JSON.stringify({success: true});
    } catch (e) {
        return JSON.stringify({success: false, error: e.message});
    }
}

/**
 * アクティブシーケンスの情報を返す。
 * @returns {string} JSON文字列 {name, fps, duration} または {error}
 */
function getActiveSequenceInfo() {
    try {
        if (!app.project || !app.project.activeSequence) {
            return JSON.stringify({error: "no active sequence"});
        }
        var seq = app.project.activeSequence;
        var settings = seq.getSettings();
        var fps = Math.round(1.0 / settings.videoFrameRate.seconds);
        var duration = seq.end / seq.timebase;
        return JSON.stringify({
            name: seq.name,
            fps: fps,
            duration: duration
        });
    } catch (e) {
        return JSON.stringify({error: e.message});
    }
}
