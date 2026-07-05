"""GitHub Issues Monitor — 检查新 issue 或新回复.

用法:
    python scripts/check_github_issues.py

行为:
    1. 从 .github_monitor_token 读 GH_TOKEN
    2. 抓取所有 open issues + 每个 issue 的最新评论
    3. 跟 data/.github_monitor_state.json 对比 last_seen
    4. 发现新内容 → 写 data/.github_issue_alerts.json
    5. 更新 state

输出:
    - 有新增: 打印 alert JSON (供调度器消费)
    - 无新增: 静默退出 (exit 0)
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "zhangsheng377/vpbuddy"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "data" / ".github_monitor_state.json"
ALERTS_PATH = PROJECT_ROOT / "data" / ".github_issue_alerts.json"
TOKEN_PATH = PROJECT_ROOT / ".github_monitor_token"


def get_token() -> str:
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def api_get(path: str, token: str) -> list | dict:
    url = f"https://api.github.com/repos/{REPO}/{path}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "vpbuddy-monitor")
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[monitor] HTTP {e.code}: {e.reason}", file=sys.stderr)
        body = e.read().decode()
        if body:
            print(f"  body: {body[:300]}", file=sys.stderr)
        return []


def fetch_all_issues(token: str) -> list[dict]:
    """Fetch all open issues."""
    return api_get("issues?state=open&per_page=30&sort=updated&direction=desc", token)


def fetch_latest_comment(issue_number: int, token: str) -> dict | None:
    """Fetch most recent comment on an issue."""
    comments = api_get(f"issues/{issue_number}/comments?per_page=1&sort=created&direction=desc", token)
    if isinstance(comments, list) and comments:
        return comments[0]
    return None


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            pass
    return {}


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def save_alerts(alerts: list[dict]):
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_PATH.write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    token = get_token()
    if not token:
        print("[monitor] ERROR: No GH_TOKEN found. Create .github_monitor_token file.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    issues = fetch_all_issues(token)
    if not isinstance(issues, list):
        print(f"[monitor] Failed to fetch issues (got {type(issues).__name__})", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc).isoformat()
    alerts = []

    for issue in issues:
        num = issue["number"]
        title = issue["title"]
        updated_at = issue["updated_at"]
        created_at = issue["created_at"]
        comments_count = issue["comments"]
        html_url = issue["html_url"]

        # 上次看到的状态
        last = state.get(str(num), {})
        last_updated = last.get("updated_at", "")
        last_comments = last.get("comments_count", 0)

        # 新 issue (之前没有记录)
        is_new_issue = str(num) not in state

        # 新回复 (comments 数量增加了)
        has_new_reply = comments_count > last_comments

        # updated_at 变了 (内容被编辑/标签变动)
        has_update = not is_new_issue and updated_at != last_updated

        if is_new_issue or has_new_reply or has_update:
            alert = {
                "number": num,
                "title": title,
                "url": html_url,
                "comments_count": comments_count,
                "updated_at": updated_at,
            }
            if is_new_issue:
                alert["type"] = "new_issue"
                alert["created_at"] = created_at
                # 获取最新评论预览 (如果有)
                if comments_count > 0:
                    comment = fetch_latest_comment(num, token)
                    if comment:
                        alert["last_comment"] = {
                            "author": comment["user"]["login"],
                            "body_preview": comment["body"][:200] if comment.get("body") else "",
                            "created_at": comment["created_at"],
                        }
            elif has_new_reply:
                alert["type"] = "new_reply"
                alert["previous_comments"] = last_comments
                # 获取新回复内容
                comment = fetch_latest_comment(num, token)
                if comment:
                    alert["last_comment"] = {
                        "author": comment["user"]["login"],
                        "body_preview": comment["body"][:300] if comment.get("body") else "",
                        "created_at": comment["created_at"],
                    }
            else:
                alert["type"] = "updated"

            alerts.append(alert)

        # 更新 state
        state[str(num)] = {
            "updated_at": updated_at,
            "comments_count": comments_count,
            "title": title,
            "last_checked": now,
        }

    save_state(state)

    if alerts:
        save_alerts(alerts)
        # Print as JSON summary for the scheduler to consume
        summary_lines = []
        for a in alerts:
            prefix = {"new_issue": "🆕 新 Issue", "new_reply": "💬 新回复", "updated": "🔄 更新"}.get(a["type"], a["type"])
            line = f"  {prefix} #{a['number']}: {a['title'][:60]}"
            if "last_comment" in a:
                c = a["last_comment"]
                line += f"\n    └─ {c['author']}: {c['body_preview'][:100]}"
            summary_lines.append(line)

        print("[monitor] 发现新内容:")
        print("\n".join(summary_lines))
        print(f"\n[monitor] 共 {len(alerts)} 条新动态, 已写入 {ALERTS_PATH}")
        print(f"\n详细完整数据已写入: {ALERTS_PATH}")
        print("请在本地用: python c:\\Users\\43587\\.trae-cn\\work\\6a47ea42ec28000e81350cc2\\check_github_issues.py 查看详情")
    else:
        print("[monitor] 无新动态")
        # 清空 alerts 文件
        if ALERTS_PATH.exists():
            ALERTS_PATH.unlink()


if __name__ == "__main__":
    main()
