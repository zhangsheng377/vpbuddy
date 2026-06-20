"""Sub-session controller — 后台循环触发 6 个子 session

设计原则(ADR-0006):
- 每种交付物一个独立子 session(session_id 固定 = 复用 Hermes 历史)
- 子 session 自己判断、自己写文件(我们不写 JSON 不做中介)
- prompt 不指定具体工具名(让 LLM 自己选合适的)
- YAGNI:跑起来再说,有问题再调

典型用法:
    python -m vpbuddy.sub_session_controller                # 主循环
    python -m vpbuddy.sub_session_controller --once         # 跑一轮
    python -m vpbuddy.sub_session_controller --meeting abc  # 单会议 6 doc
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .storage import MeetingStorage

# 默认路径(可通过环境变量覆盖)
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", "/home/zsd/vpbuddy/data/meetings"))
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", "/home/zsd/vpbuddy/docs"))
PROMPTS_DIR = Path(__file__).parent / "prompts"
POLL_INTERVAL = int(os.environ.get("VPBUDDY_POLL_INTERVAL", "30"))

# 6 个子 session 对应 6 种 doc_kind
DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]


def list_active_meetings() -> List[str]:
    """列出活跃会议(有 MeetingState JSON 文件就算活跃)"""
    if not DATA_DIR.exists():
        return []
    return [p.stem for p in DATA_DIR.glob("*.json")]


def get_doc_path(meeting_id: str, doc_kind: str) -> Path:
    """获取某 doc_kind 的输出文件路径"""
    base = DOCS_DIR / meeting_id
    if doc_kind == "demo":
        # demo 是目录,主输出 demo.html
        return base / "demo" / "demo.html"
    return base / f"{doc_kind}.md"


def format_state_summary(state) -> str:
    """把 MeetingState 格式化成 LLM 友好的摘要(全文,不截断——YAGNI)"""
    parts = [f"# 会议 {state.meeting_id} 累积摘要", ""]
    parts.append(f"- 平台: {state.platform.value}")
    parts.append(f"- 最后更新: {state.last_updated}")
    parts.append("")

    if state.requirements:
        parts.append(f"## 需求 ({len(state.requirements)} 条)")
        for r in state.requirements:
            parts.append(f"- [{r.priority.value.upper()}] {r.id}: {r.text}"
                         + (f" (来自: {r.speaker_name})" if r.speaker_name else ""))
        parts.append("")

    if state.goals:
        parts.append(f"## 目标 ({len(state.goals)} 条)")
        for g in state.goals:
            parts.append(f"- {g.id}: {g.text}")
        parts.append("")

    if state.features:
        parts.append(f"## 功能 ({len(state.features)} 条)")
        for f in state.features:
            parts.append(f"- {f.id}: {f.text}")
        parts.append("")

    if state.risks:
        parts.append(f"## 风险 ({len(state.risks)} 条)")
        for r in state.risks:
            parts.append(f"- [{r.severity.value.upper()}] {r.id}: {r.text}")
        parts.append("")

    if state.open_questions:
        parts.append(f"## 开放问题 ({len(state.open_questions)} 条)")
        for q in state.open_questions:
            parts.append(f"- {q.id}: {q.text}")
        parts.append("")

    if state.speaker_map:
        parts.append(f"## 说话人映射")
        for sid, name in state.speaker_map.items():
            parts.append(f"- {sid} → {name}")

    return "\n".join(parts)


def render_prompt(doc_kind: str, meeting_id: str, state_summary: str, last_doc: Optional[str]) -> str:
    """渲染子 session 的 prompt(优先用专属模板,fallback 通用模板)"""
    template_path = PROMPTS_DIR / f"{doc_kind}.md"
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else _generic_template()

    doc_path = get_doc_path(meeting_id, doc_kind)
    return template.format(
        meeting_id=meeting_id,
        doc_kind=doc_kind,
        state_summary=state_summary,
        last_doc=last_doc or "(无 — 首次创建)",
        doc_path=str(doc_path),
    )


def _generic_template() -> str:
    """通用 prompt(各 doc_kind 没有专属模板时用)"""
    return """你是 VPBuddy 的 {doc_kind} 子 session。
session_id 固定: meeting:{meeting_id}:{doc_kind}
文档输出路径: {doc_path}

【职责】
持续维护本次会议的 {doc_kind} 文档。

【当前累积】
{state_summary}

【你之前的输出】
{last_doc}

【判断】
1. 累积有变化?→ 改文档
2. V 显式说"更新 {doc_kind}"?→ 立即改
3. 否则?→ 输出"无变化",退出

【YAGNI】
- 不主动加"可能需要"的章节
- 跑起来再说,有问题再调
- 用你手头能用的工具读写文件(具体工具名我不指定,Hermes 会告诉你)
"""


def trigger_sub_session(meeting_id: str, doc_kind: str, dry_run: bool = False) -> dict:
    """触发一个子 session

    Args:
        meeting_id: 会议 ID
        doc_kind: req/arch/tasks/api/risk/demo
        dry_run: True = 只渲染 prompt 不调 hermes(用于测试)

    Returns:
        {"session_id": ..., "prompt": ..., "triggered": bool, "error": str?}
    """
    result = {"session_id": f"meeting:{meeting_id}:{doc_kind}", "triggered": False, "error": None}

    # 1. 读累积
    try:
        state = MeetingStorage(data_dir=str(DATA_DIR)).load(meeting_id)
    except Exception as e:
        result["error"] = f"load_state: {e}"
        return result

    # 2. 读上次输出
    doc_path = get_doc_path(meeting_id, doc_kind)
    last_doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else None

    # 3. 渲染 prompt
    prompt = render_prompt(doc_kind, meeting_id, format_state_summary(state), last_doc)
    result["prompt"] = prompt

    # 4. 触发 hermes 子 session
    if dry_run:
        result["triggered"] = False
        result["dry_run"] = True
        return result

    try:
        # 注:不传 --resume(不需要历史),全 context 在 prompt 里
        # 这样:LLM 每次拿到最新 meeting_state + 上次 doc 输出,自己判断要不要更新
        # 避免维护 session_id 映射的复杂度(YAGNI)
        cmd = [
            "hermes", "chat",
            "-q", prompt,
            "-Q",  # quiet
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 分钟超时
        )
        result["triggered"] = (proc.returncode == 0)
        if proc.returncode != 0:
            result["error"] = f"hermes exit {proc.returncode}: {proc.stderr[:200]}"
        result["hermes_output"] = proc.stdout[-500:]  # 最后 500 字符
    except subprocess.TimeoutExpired:
        result["error"] = "hermes timeout (300s)"
    except FileNotFoundError:
        result["error"] = "hermes CLI not found in PATH"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    return result


def run_one_round(meeting_ids: Optional[List[str]] = None, dry_run: bool = False) -> List[dict]:
    """跑一轮:对每个会议 + 每个 doc_kind 触发子 session"""
    meetings = meeting_ids or list_active_meetings()
    results = []
    print(f"[{datetime.now().isoformat()}] {len(meetings)} active meetings × {len(DOC_KINDS)} doc_kinds = {len(meetings)*len(DOC_KINDS)} subs")
    for mid in meetings:
        for kind in DOC_KINDS:
            r = trigger_sub_session(mid, kind, dry_run=dry_run)
            status = "✓" if r.get("triggered") or r.get("dry_run") else "✗"
            err = f" [{r['error']}]" if r.get("error") else ""
            print(f"  {status} {r['session_id']}{err}")
            results.append(r)
    return results


def main_loop():
    """主循环:每 POLL_INTERVAL 秒跑一轮"""
    print(f"VPBuddy sub-session controller started")
    print(f"  DATA_DIR: {DATA_DIR}")
    print(f"  DOCS_DIR: {DOCS_DIR}")
    print(f"  PROMPTS_DIR: {PROMPTS_DIR}")
    print(f"  POLL_INTERVAL: {POLL_INTERVAL}s")
    print(f"  DOC_KINDS: {DOC_KINDS}")
    print()
    while True:
        run_one_round()
        print(f"  sleep {POLL_INTERVAL}s...\n")
        time.sleep(POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="VPBuddy sub-session controller")
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出")
    parser.add_argument("--meeting", help="只跑指定会议 ID")
    parser.add_argument("--dry-run", action="store_true", help="只渲染 prompt,不真触发 hermes")
    parser.add_argument("--list-meetings", action="store_true", help="列出活跃会议并退出")
    args = parser.parse_args()

    if args.list_meetings:
        meetings = list_active_meetings()
        print(f"Active meetings ({len(meetings)}):")
        for m in meetings:
            print(f"  - {m}")
        return

    if args.once:
        meetings = [args.meeting] if args.meeting else None
        run_one_round(meeting_ids=meetings, dry_run=args.dry_run)
        return

    # 默认主循环
    main_loop()


if __name__ == "__main__":
    main()
