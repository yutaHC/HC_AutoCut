#!/usr/bin/env python3
"""
propose.py - チーム共有エージェント
my_rules.md の内容をチームテンプレートに統合し、GitHub PRを自動作成・マージする。

Usage:
    python3 propose.py --api-key <key> --github-token <token> --username <user>
"""

import argparse
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


# ============================================================
# プログレス出力（pipeline.py と同じ形式）
# ============================================================

def emit(obj: dict):
    print(json.dumps(obj, ensure_ascii=False), flush=True)

def progress(step: str, pct: int, msg: str):
    emit({"type": "progress", "step": step, "pct": pct, "msg": msg})

def done(pr_url: str, new_version: str):
    emit({"type": "done", "pr_url": pr_url, "new_version": new_version})

def error(msg: str):
    emit({"type": "error", "message": msg})


# ============================================================
# プロンプトファイル操作
# ============================================================

def load_latest_template(prompts_dir: Path) -> tuple:
    """(version_str, content) を返す。例: ('v1.0', '...')"""
    files = sorted(prompts_dir.glob("cut_logic_v*.md"))
    if not files:
        raise FileNotFoundError(f"テンプレートが見つかりません: {prompts_dir}")
    latest = files[-1]
    m = re.search(r'cut_logic_(v[\d.]+)\.md', latest.name)
    if not m:
        raise ValueError(f"バージョン番号を解析できません: {latest.name}")
    return m.group(1), latest.read_text(encoding="utf-8")


def load_my_rules(prompts_dir: Path) -> str:
    """my_rules.md を読む。実質的な内容がない場合は ValueError を投げる。"""
    my_rules_path = prompts_dir / "my_rules.md"
    if not my_rules_path.exists():
        raise FileNotFoundError(
            "my_rules.md が見つかりません。パネルの「my_rules.md を開く」ボタンで作成してください。"
        )
    content = my_rules_path.read_text(encoding="utf-8")
    meaningful = [l for l in content.splitlines()
                  if l.strip() and not l.strip().startswith('#')]
    if not meaningful:
        raise ValueError(
            "my_rules.md にルールが記述されていません（コメント行のみです）。追加ルールを書いてください。"
        )
    return content


def increment_version(v: str) -> str:
    """'v1.0' → 'v1.1'、'v1.9' → 'v1.10'"""
    m = re.match(r'^v(\d+)\.(\d+)$', v.strip())
    if not m:
        raise ValueError(f"バージョン形式が不正: {v}")
    return f"v{int(m.group(1))}.{int(m.group(2)) + 1}"


def strip_code_fences(text: str) -> str:
    """Claude APIが``` で囲んで返す場合に除去する。"""
    text = re.sub(r'^```[a-z]*\n?', '', text.strip())
    return re.sub(r'\n?```$', '', text).strip()


# ============================================================
# Claude API（テンプレート統合）
# ============================================================

PROPOSE_SYSTEM = """あなたはヘアキャンプ動画制作チームの編集スペシャリストです。
チームのカットロジックテンプレートと個人の追加ルールを受け取り、
テンプレートを改善した新バージョンを作成してください。

ルール:
- 既存テンプレートの構造・スタイル・フォーマットを完全に維持する
- 個人ルールの意図を読み取り、最も適切なセクションに統合する
- すでにテンプレートに含まれているルールは追加しない（重複回避）
- 追加した部分以外のセクションは変更しない
- 出力は統合済みの完全なMarkdownテキストのみ（```などのコードブロック記法は不要）"""


def call_claude_merge(api_key: str, template: str, my_rules: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    user_content = (
        f"## 現在のチームテンプレート\n{template}\n\n"
        f"## 統合したい個人ルール\n{my_rules}\n\n"
        "上記の個人ルールをテンプレートの適切な箇所に統合した新しい完全なテンプレートを返してください。"
    )
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=8096,
        system=PROPOSE_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = response.content[0].text
    return strip_code_fences(raw)


# ============================================================
# GitHub REST API
# ============================================================

REPO = "yutaHC/HC_AutoCut"
API_BASE = "https://api.github.com"


def github_request(method: str, path: str, token: str, body=None) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def create_and_merge_pr(token: str, username: str, new_version: str, new_template: str) -> str:
    """新テンプレートをGitHubにコミットしてPRを作成・即時マージする。PR URLを返す。"""
    branch = f"propose/{username}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    tmpl_path = f"autocut-plugin/prompts/cut_logic_{new_version}.md"
    ver_path = "autocut-plugin/prompts/versions.txt"

    # a. mainブランチのSHA取得
    ref = github_request("GET", f"/repos/{REPO}/git/ref/heads/main", token)
    main_sha = ref["object"]["sha"]

    # b. 新ブランチ作成
    github_request("POST", f"/repos/{REPO}/git/refs", token, {
        "ref": f"refs/heads/{branch}",
        "sha": main_sha,
    })

    # c. versions.txt の現在のSHAと内容を取得（PUT時に必要）
    ver_data = github_request(
        "GET", f"/repos/{REPO}/contents/{ver_path}?ref={branch}", token
    )
    ver_sha = ver_data["sha"]
    current_versions = base64.b64decode(ver_data["content"]).decode("utf-8")

    # d. 新テンプレートをコミット（新規ファイルのためSHA不要）
    github_request("PUT", f"/repos/{REPO}/contents/{tmpl_path}", token, {
        "message": f"feat(autocut): add {new_version} proposed by {username}",
        "content": base64.b64encode(new_template.encode()).decode(),
        "branch": branch,
    })

    # e. versions.txt を更新（末尾に新バージョンを追記）
    new_versions = current_versions.rstrip() + f"\n{new_version}\n"
    github_request("PUT", f"/repos/{REPO}/contents/{ver_path}", token, {
        "message": f"feat(autocut): update versions.txt for {new_version}",
        "content": base64.b64encode(new_versions.encode()).decode(),
        "sha": ver_sha,
        "branch": branch,
    })

    # f. PR作成
    pr = github_request("POST", f"/repos/{REPO}/pulls", token, {
        "title": f"[AutoCut] {new_version} — {username} からの提案",
        "body": (
            f"## 概要\n`{username}` の `my_rules.md` をチームテンプレートに統合した提案です。\n\n"
            f"## 変更内容\n- `{tmpl_path}` を新規追加\n- `{ver_path}` にバージョンを追記\n\n"
            f"*Claudeエージェントが自動生成・自動マージしました。*"
        ),
        "head": branch,
        "base": "main",
    })
    pr_url = pr["html_url"]
    pr_number = pr["number"]

    # g. PR即時マージ
    github_request("PUT", f"/repos/{REPO}/pulls/{pr_number}/merge", token, {
        "merge_method": "squash",
        "commit_title": f"[AutoCut] {new_version} — {username} からの提案",
    })

    return pr_url


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="チーム共有エージェント")
    parser.add_argument("--api-key",       required=True, help="Anthropic API Key")
    parser.add_argument("--github-token",  required=True, help="GitHub Personal Access Token")
    parser.add_argument("--username",      default="anonymous", help="GitHubユーザー名（PR作成者識別用）")
    args = parser.parse_args()

    prompts_dir = Path(__file__).parent.parent / "prompts"

    try:
        # Step 1: ファイル読み込み
        progress("loading", 5, "ルールファイルを読み込み中...")
        current_version, template = load_latest_template(prompts_dir)
        my_rules = load_my_rules(prompts_dir)

        # Step 2: Claude APIで統合
        progress("merging", 20, f"Claude APIでルールを統合中（現在: {current_version}）...")
        new_template = call_claude_merge(args.api_key, template, my_rules)

        # 重複チェック（変更なし = ルールがすでに含まれている）
        if new_template.strip() == template.strip():
            error("my_rules.md の内容はすでにテンプレートに含まれています。新しいルールを追記してください。")
            sys.exit(1)

        new_version = increment_version(current_version)
        progress("merging", 60, f"新バージョン {new_version} を生成しました")

        # Step 3: GitHub PR作成・マージ
        progress("creating_pr", 70, "GitHubブランチを作成中...")
        progress("creating_pr", 80, "テンプレートをコミット中...")
        progress("creating_pr", 90, "PRを作成・マージ中...")
        pr_url = create_and_merge_pr(args.github_token, args.username, new_version, new_template)

        progress("done", 100, f"{new_version} をチームテンプレートに反映しました")
        done(pr_url, new_version)

    except FileNotFoundError as e:
        error(str(e))
        sys.exit(1)
    except ValueError as e:
        error(str(e))
        sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        error(f"GitHub APIエラー ({e.code}): {body[:300]}")
        sys.exit(1)
    except Exception as e:
        error(f"予期しないエラー: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
