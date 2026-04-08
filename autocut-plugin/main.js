/**
 * main.js
 * AutoCut CEPパネル メインロジック
 * Node.js (child_process) でPythonパイプラインを起動し、
 * ExtendScript経由でPremiere Proと通信する。
 */

'use strict';

const csInterface = new CSInterface();
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

// プラグインディレクトリ（__dirname はCEP環境では使えないため CSInterface で取得）
const PLUGIN_DIR = csInterface.getSystemPath(SystemPath.EXTENSION);
const PYTHON_BIN = path.join(PLUGIN_DIR, 'venv', 'bin', 'python3');
const PIPELINE_SCRIPT = path.join(PLUGIN_DIR, 'python', 'pipeline.py');
const PROPOSE_SCRIPT = path.join(PLUGIN_DIR, 'python', 'propose.py');
const MY_RULES_PATH = path.join(PLUGIN_DIR, 'prompts', 'my_rules.md');
const os = require('os');
const OUTPUT_XML = path.join(os.homedir(), 'Desktop', 'autocut_result.xml');

const VERSION = '0.5.0';

let selectedMode = 'standard';
let selectedProvider = 'claude';
let isRunning = false;
let isProposing = false;

// ---- 初期化 ----

window.addEventListener('load', () => {
  document.getElementById('versionLabel').textContent = 'v' + VERSION;
  initApiKey();
  initGithubToken();
  loadMyRulesToTextarea();
  log('AutoCut パネルが起動しました (v' + VERSION + ')');
  log('Python: ' + PYTHON_BIN);

  // Python環境が存在するか確認
  if (!fs.existsSync(PYTHON_BIN)) {
    log('⚠️  setup.sh を先に実行してください（venv が見つかりません）');
  }
});

// ---- APIキー管理 ----

function initApiKey() {
  ['claude', 'openai'].forEach(provider => {
    const storageKey = 'autocut_api_key_' + provider;
    const saved = localStorage.getItem(storageKey) || '';
    const input  = document.getElementById('apiKeyInput-' + provider);
    const toggle = document.getElementById('apiKeyToggle-' + provider);
    if (saved) {
      input.value = saved;
      input.closest('.section').style.display = 'none';
      toggle.style.display = 'block';
    } else {
      toggle.style.display = 'none';
    }
  });
}

function toggleApiKeySection(provider) {
  const section = document.getElementById('apiKeySection-' + provider);
  const toggle  = document.getElementById('apiKeyToggle-' + provider);
  section.style.display = section.style.display === 'none' ? '' : 'none';
  toggle.textContent = section.style.display === 'none' ? '設定済み（変更する）' : '閉じる';
}

function getApiKey() {
  const input      = document.getElementById('apiKeyInput-' + selectedProvider);
  const storageKey = 'autocut_api_key_' + selectedProvider;
  const key = input.value.trim();
  if (key) {
    localStorage.setItem(storageKey, key);
    document.getElementById('apiKeySection-' + selectedProvider).style.display = 'none';
    document.getElementById('apiKeyToggle-' + selectedProvider).style.display = 'block';
    document.getElementById('apiKeyToggle-' + selectedProvider).textContent = '設定済み（変更する）';
  }
  return key || localStorage.getItem(storageKey) || '';
}

// ---- GitHubトークン管理 ----

function initGithubToken() {
  const savedToken    = localStorage.getItem('autocut_github_token') || '';
  const savedUsername = localStorage.getItem('autocut_github_username') || '';
  const tokenInput    = document.getElementById('githubTokenInput');
  const tokenToggle   = document.getElementById('githubTokenToggle');
  const usernameInput = document.getElementById('githubUsernameInput');

  if (savedToken) {
    tokenInput.value = savedToken;
    document.getElementById('githubTokenSection').style.display = 'none';
    tokenToggle.style.display = 'block';
  }
  if (savedUsername) {
    usernameInput.value = savedUsername;
  }
}

function toggleGithubTokenSection() {
  const section = document.getElementById('githubTokenSection');
  const toggle  = document.getElementById('githubTokenToggle');
  const isHidden = section.style.display === 'none';
  section.style.display = isHidden ? '' : 'none';
  toggle.textContent = isHidden ? '閉じる' : '設定済み（変更する）';
}

function getGithubToken() {
  const input = document.getElementById('githubTokenInput');
  const key   = input.value.trim();
  if (key) {
    localStorage.setItem('autocut_github_token', key);
    document.getElementById('githubTokenSection').style.display = 'none';
    document.getElementById('githubTokenToggle').style.display = 'block';
    document.getElementById('githubTokenToggle').textContent = '設定済み（変更する）';
  }
  return key || localStorage.getItem('autocut_github_token') || '';
}

function getGithubUsername() {
  const input = document.getElementById('githubUsernameInput');
  const name  = input.value.trim();
  if (name) {
    localStorage.setItem('autocut_github_username', name);
  }
  return name || localStorage.getItem('autocut_github_username') || 'anonymous';
}

// ---- チーム共有エージェント ----

function loadMyRulesToTextarea() {
  const textarea = document.getElementById('myRulesInput');
  if (!textarea) return;
  if (!fs.existsSync(MY_RULES_PATH)) return;

  const raw = fs.readFileSync(MY_RULES_PATH, 'utf8');
  // コメント行を除いた実質行だけをテキストエリアに表示
  const meaningful = raw.split('\n')
    .filter(l => l.trim() && !l.trim().startsWith('#'))
    .join('\n');
  if (meaningful) textarea.value = meaningful;
}

function saveTextareaToMyRules() {
  const textarea = document.getElementById('myRulesInput');
  const content = textarea ? textarea.value.trim() : '';
  const fileContent = [
    '# 自分の追加ルール（パネルから自動保存）',
    '#',
    content,
  ].join('\n');
  fs.writeFileSync(MY_RULES_PATH, fileContent, 'utf8');
}

function startPropose() {
  if (isProposing || isRunning) return;

  const apiKey = getApiKey();
  if (!apiKey) {
    alert('Anthropic APIキーを入力してください');
    return;
  }

  const githubToken = getGithubToken();
  if (!githubToken) {
    alert('GitHub Personal Access Token を入力してください（repo 権限が必要です）');
    return;
  }

  const username = getGithubUsername();
  if (!username || username === 'anonymous') {
    alert('GitHubユーザー名を入力してください');
    return;
  }

  const rulesText = document.getElementById('myRulesInput')
    ? document.getElementById('myRulesInput').value.trim() : '';
  if (!rulesText) {
    alert('追加ルールを入力してください');
    return;
  }

  // テキストエリアの内容をmy_rules.mdに保存してからproposeを実行
  saveTextareaToMyRules();

  isProposing = true;
  const btn = document.getElementById('proposeBtn');
  btn.disabled = true;
  btn.textContent = '提案中...';
  document.getElementById('prLink').style.display = 'none';

  log('--- チーム共有エージェント 開始 ---');

  const args = [
    PROPOSE_SCRIPT,
    '--api-key',      apiKey,
    '--github-token', githubToken,
    '--username',     username,
  ];

  const pyProcess = spawn(PYTHON_BIN, args, {
    env: Object.assign({}, process.env)
  });

  let buffer = '';

  pyProcess.stdout.on('data', (data) => {
    buffer += data.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop();

    lines.forEach(line => {
      line = line.trim();
      if (!line) return;
      try {
        const msg = JSON.parse(line);
        handleProposeMessage(msg);
      } catch (e) {
        log('[propose] ' + line);
      }
    });
  });

  pyProcess.stderr.on('data', (data) => {
    log('[propose/stderr] ' + data.toString().trim());
  });

  pyProcess.on('close', (code) => {
    isProposing = false;
    btn.disabled = false;
    btn.textContent = 'チームに共有（自動PR）';
    if (code !== 0) {
      log('❌ propose.py がエラーで終了しました (code: ' + code + ')');
    }
  });

  pyProcess.on('error', (err) => {
    isProposing = false;
    btn.disabled = false;
    btn.textContent = 'チームに共有（自動PR）';
    log('❌ 起動エラー: ' + err.message);
  });
}

function handleProposeMessage(msg) {
  if (msg.type === 'progress') {
    log('[共有] ' + (msg.msg || msg.step));
  } else if (msg.type === 'done') {
    log('✅ チームテンプレートを更新しました（' + msg.new_version + '）');
    log('PR: ' + msg.pr_url);
    const prLink = document.getElementById('prLink');
    prLink.style.display = 'block';
    prLink.innerHTML = '✅ ' + msg.new_version + ' — <a href="#" onclick="openURL(\'' + msg.pr_url + '\');return false;">PR を確認</a>';
  } else if (msg.type === 'error') {
    log('❌ [共有エラー] ' + msg.message);
  }
}

function openURL(url) {
  csInterface.openURLInDefaultBrowser(url);
}

// ---- モード / プロバイダ選択 ----

function selectMode(el) {
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  selectedMode = el.dataset.mode;
}

function selectProvider(el) {
  document.querySelectorAll('[data-provider]').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  selectedProvider = el.dataset.provider;

  // APIキー欄を切り替え
  ['claude', 'openai'].forEach(p => {
    const storageKey = 'autocut_api_key_' + p;
    const saved = localStorage.getItem(storageKey);
    const section = document.getElementById('apiKeySection-' + p);
    const toggle  = document.getElementById('apiKeyToggle-' + p);
    if (p === selectedProvider) {
      section.style.display = saved ? 'none' : '';
      toggle.style.display  = saved ? 'block' : 'none';
    } else {
      section.style.display = 'none';
      toggle.style.display  = 'none';
    }
  });
}

// ---- 解析開始 ----

function startAnalysis() {
  if (isRunning) return;

  const apiKey = getApiKey();
  if (!apiKey) {
    alert('APIキーを入力してください');
    return;
  }

  if (!fs.existsSync(PYTHON_BIN)) {
    alert('setup.sh を先に実行してください。\n\nターミナルで:\n  cd ' + PLUGIN_DIR + '\n  sh setup.sh');
    return;
  }

  // Premiereからシーケンス情報を取得
  log('シーケンス情報を取得中...');
  csInterface.evalScript('getActiveSequenceInfo()', (result) => {
    let info;
    try {
      info = JSON.parse(result);
    } catch (e) {
      showError('シーケンス情報の取得に失敗しました: ' + result);
      return;
    }

    if (info.error) {
      showError(info.error);
      return;
    }

    const clips = info.clips || [{ path: info.clipPath, timelineStart: 0, timelineEnd: 0, mediaInPoint: 0, mediaOutPoint: 0 }];
    log('シーケンス: ' + info.sequenceName);
    log('クリップ数: ' + clips.length + (info.isMultiCam ? ' [マルチカメラ]' : ''));
    log('FPS: ' + info.fps);

    runPipeline(clips, apiKey);
  });
}

// ---- Pythonパイプライン実行 ----

function runPipeline(clips, apiKey) {
  isRunning = true;
  setRunningState(true);
  resetResult();

  const args = [
    PIPELINE_SCRIPT,
    '--clips-json', JSON.stringify(clips),
    '--mode', selectedMode,
    '--api-key', apiKey,
    '--llm-provider', selectedProvider,
    '--output', OUTPUT_XML,
  ];

  log('Python パイプラインを起動中...');

  const pyProcess = spawn(PYTHON_BIN, args, {
    env: Object.assign({}, process.env, {
      KMP_DUPLICATE_LIB_OK: 'TRUE',
    })
  });

  let buffer = '';

  pyProcess.stdout.on('data', (data) => {
    buffer += data.toString();
    const lines = buffer.split('\n');
    buffer = lines.pop(); // 最後の不完全な行はバッファに残す

    lines.forEach(line => {
      line = line.trim();
      if (!line) return;
      try {
        const msg = JSON.parse(line);
        handlePipelineMessage(msg);
      } catch (e) {
        log(line); // JSONでなければそのままログ
      }
    });
  });

  pyProcess.stderr.on('data', (data) => {
    log('[stderr] ' + data.toString().trim());
  });

  pyProcess.on('close', (code) => {
    isRunning = false;
    setRunningState(false);

    if (code !== 0) {
      showError('パイプラインがエラーで終了しました (code: ' + code + ')');
    }
  });

  pyProcess.on('error', (err) => {
    isRunning = false;
    setRunningState(false);
    showError('起動エラー: ' + err.message);
  });
}

// ---- パイプラインメッセージハンドラ ----

const STEP_PCT = {
  extracting:   10,
  transcribing: 35,
  detecting:    55,
  analyzing:    72,
  generating:   90,
};

const STEP_LABELS = {
  extracting:   'step-extracting',
  transcribing: 'step-transcribing',
  analyzing:    'step-analyzing',
  generating:   'step-generating',
};

let lastStepKey = null;

function handlePipelineMessage(msg) {
  if (msg.type === 'progress') {
    const pct = msg.pct !== undefined ? msg.pct : (STEP_PCT[msg.step] || 0);
    setProgress(pct, msg.msg || msg.step);
    updateStepIndicators(msg.step);
    log(msg.msg || msg.step);

  } else if (msg.type === 'done') {
    setProgress(100, '完了');
    markAllStepsDone();
    log('✅ 完了: ' + msg.cuts + '箇所カット / ' + formatSec(msg.saved_sec) + '削減');
    showResult(msg);
    importXML(msg.xml_path);

  } else if (msg.type === 'error') {
    showError(msg.message);
  }
}

// ---- QE DOM で直接シーケンスを複製してカットを適用 ----

function importXML(xmlPath) {
  const jsonPath = xmlPath.replace(/\.xml$/, '.json');
  log('Premiere Pro にシーケンスを作成中...');
  csInterface.evalScript('applyAutoCutFromFile("' + jsonPath.replace(/\\/g, '\\\\') + '")', (result) => {
    let res;
    try {
      res = JSON.parse(result);
    } catch (e) {
      log('結果を解析できませんでした: ' + result);
      return;
    }

    if (res.success) {
      log('✅ ' + res.newName + ' を作成しました');
    } else {
      log('⚠️  エラー: ' + res.message);
    }
  });
}

// ---- UI ヘルパー ----

function setRunningState(running) {
  const btn = document.getElementById('analyzeBtn');
  const progressWrap = document.getElementById('progressWrap');
  btn.disabled = running;
  btn.textContent = running ? '解析中...' : '解析開始';
  progressWrap.style.display = running ? 'block' : 'block';
}

function setProgress(pct, msg) {
  document.getElementById('progressBar').style.width = pct + '%';
  document.getElementById('progressMsg').textContent = msg;
}

function updateStepIndicators(step) {
  const stepOrder = ['extracting', 'transcribing', 'analyzing', 'generating'];
  const currentIdx = stepOrder.indexOf(step);

  stepOrder.forEach((s, idx) => {
    const elId = 'step-' + s;
    const el = document.getElementById(elId);
    if (!el) return;
    if (idx < currentIdx) {
      el.className = 'step done';
    } else if (idx === currentIdx) {
      el.className = 'step active';
    } else {
      el.className = 'step';
    }
  });
}

function markAllStepsDone() {
  ['step-extracting', 'step-transcribing', 'step-analyzing', 'step-generating'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = 'step done';
  });
}

function showResult(msg) {
  const wrap = document.getElementById('resultWrap');
  wrap.style.display = 'block';
  wrap.className = 'result-wrap';
  document.getElementById('resultTitle').textContent = '✅ 完了';
  document.getElementById('resultStats').textContent =
    msg.cuts + '箇所カット｜' + formatSec(msg.saved_sec) + '削減';
}

function showError(msg) {
  log('❌ ' + msg);
  const wrap = document.getElementById('resultWrap');
  wrap.style.display = 'block';
  wrap.className = 'result-wrap error';
  document.getElementById('resultTitle').textContent = '❌ エラー';
  document.getElementById('resultStats').textContent = msg;
}

function resetResult() {
  document.getElementById('resultWrap').style.display = 'none';
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('progressMsg').textContent = '準備中...';
  ['step-extracting', 'step-transcribing', 'step-analyzing', 'step-generating'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = 'step';
  });
}

function log(msg) {
  const box = document.getElementById('logBox');
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);
  box.value += '[' + ts + '] ' + msg + '\n';
  box.scrollTop = box.scrollHeight;
}

function formatSec(sec) {
  const s = Math.round(sec);
  if (s < 60) return s + '秒';
  return Math.floor(s / 60) + '分' + (s % 60) + '秒';
}
