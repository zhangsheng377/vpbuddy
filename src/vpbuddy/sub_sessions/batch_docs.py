"""Batch docs sub-session (Phase 5.5 ADR-0029 Commit 3).

1 个 AIAgent 跑 5 个文档 (req/arch/tasks/api/risk). 取代老的 6 个并行 agent.

设计:
- session_id = "meeting:{mid}:batch_docs" (1 个共享 session, 5 文档历史在 LLM 上下文中)
- 1 次 LLM 调用, agent 自己 read_file state + 5 docs + 调 write_file 5 次
- 失败隔离: 验证每个文件是否真写入, 部分成功也算 trigger
- 调 realtime_server.push_event 推 doc-update SSE
- 调 ui_server_helpers.check_all_docs_stored_notify 推 docs-complete

跟 demo agent (独立 session_id + 单文档) 区别: batch_docs 5 文档共享 LLM 上下文,
保证一致性 (req 提的预算, arch 立即同步).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..storage import MeetingStorage
from ..sub_session_controller import (
    _AGENT_AVAILABLE,
    DATA_DIR,
    DOCS_DIR,
    PROMPTS_DIR,
    _get_or_create_agent,
    format_state_summary,
)

logger = logging.getLogger(__name__)

# 5 文档 kind → 输出路径
BATCH_DOC_KINDS = ["req", "arch", "tasks", "api", "risk"]


def get_batch_doc_paths(meeting_id: str, docs_dir: Path | None = None) -> dict[str, Path]:
    """5 个文档路径. docs_dir 默认 DOCS_DIR."""
    base = (docs_dir or DOCS_DIR) / meeting_id
    return {
        "req": base / "req.md",
        "arch": base / "arch.md",
        "tasks": base / "tasks.md",
        "api": base / "api.md",
        "risk": base / "risk.md",
    }


def render_batch_prompt(
    meeting_id: str,
    state_summary: str,
    last_docs: dict[str, str | None],
    docs_dir: Path | None = None,
) -> str:
    """渲染 batch_docs.md prompt, 注入 5 文档路径 + 上次内容.

    Returns:
        str: 完整 prompt.
    """
    template_path = PROMPTS_DIR / "batch_docs.md"
    template = template_path.read_text(encoding="utf-8")

    paths = get_batch_doc_paths(meeting_id, docs_dir)

    # 5 文档上次输出 block
    last_docs_block = "\n\n".join([
        f"## {kind}.md 上次输出\n```\n{(content or '(首次创建 — 空)')}\n```"
        for kind, content in last_docs.items()
    ])

    # Escape braces (跟老 render_prompt 同款 — 防止模板字符串/CSS 等被 .format() 误解析)
    safe_template = template.replace("{", "{{").replace("}", "}}")
    # 还原我们需要的变量
    for key in [
        "meeting_id", "state_summary", "last_docs_block",
        "doc_path_req", "doc_path_arch", "doc_path_tasks", "doc_path_api", "doc_path_risk",
    ]:
        safe_template = safe_template.replace("{{" + key + "}}", "{" + key + "}")

    return safe_template.format(
        meeting_id=meeting_id,
        state_summary=state_summary,
        last_docs_block=last_docs_block,
        doc_path_req=str(paths["req"]),
        doc_path_arch=str(paths["arch"]),
        doc_path_tasks=str(paths["tasks"]),
        doc_path_api=str(paths["api"]),
        doc_path_risk=str(paths["risk"]),
    )


def trigger_batch_docs(
    meeting_id: str,
    dry_run: bool = False,
    timeout: int = 300,
) -> dict[str, Any]:
    """1 次触发 batch_docs agent. 返回 trigger 结果 + 5 文件状态.

    行为:
    1. 读 state + 5 docs (如存在)
    2. 渲染 batch_docs.md prompt
    3. 调 AIAgent.chat(prompt) — 1 次 LLM 调用, 输出 5 个 write_file
    4. 等 200ms 让文件系统 sync
    5. 验证 5 文件都写入 (best-effort: 部分成功也算 triggered)
    6. 推 doc-update SSE (每个写入的文件)
    7. 推 docs-complete (调 ui_server_helpers.check_all_docs_stored_notify)

    Args:
        meeting_id: 会议 ID
        dry_run: True = 只渲染 prompt 不调 LLM (测试用)
        timeout: LLM 调用超时 (秒), 默认 300

    Returns:
        {
            "session_id": "meeting:{mid}:batch_docs",
            "triggered": bool,
            "files": {kind: {"path": str, "size": int, "written": bool, "error"?: str}},
            "any_written": bool,
            "elapsed_sec": float,
            "agent_path": "in-process" | "direct" | "subprocess",
            "error"?: str,
        }
    """
    t0 = time.time()
    sid = f"meeting:{meeting_id}:batch_docs"
    result: dict[str, Any] = {
        "session_id": sid,
        "triggered": False,
        "files": {},
        "any_written": False,
    }

    # 1. 读 state
    try:
        state = MeetingStorage(data_dir=str(DATA_DIR)).load(meeting_id)
    except Exception as e:
        result["error"] = f"load_state: {e}"
        return result
    state_summary = format_state_summary(state)

    # 2026-07-03 v0.8.4: 全新空会议 (state.facts 全空 + 5 docs 全无文件) → skip
    # 空会议没说话, 不强制 LLM 写"未产出"骨架刷 doc panel.
    # 客户端 6 docs 占位 empty, 等 state 有积累后再触发.
    state_has_facts = bool(
        state.requirements or state.goals or state.features
        or state.risks or state.open_questions
    )
    paths = get_batch_doc_paths(meeting_id)
    any_doc_exists = any(p.exists() and p.stat().st_size > 50 for p in paths.values())
    if not state_has_facts and not any_doc_exists:
        result["skip"] = "empty_state_no_prior_docs"
        result["agent_path"] = "skipped"
        result["elapsed_sec"] = time.time() - t0
        logger.info("batch_docs: skip empty meeting=%s (无 facts + 无 doc 文件)", meeting_id)
        return result

    # 2. 读 5 文档上次输出
    last_docs: dict[str, str | None] = {}
    for kind, p in paths.items():
        last_docs[kind] = p.read_text(encoding="utf-8") if p.exists() else None

    # 3. 渲染 prompt
    prompt = render_batch_prompt(meeting_id, state_summary, last_docs)
    if dry_run:
        result["prompt"] = prompt
        result["dry_run"] = True
        return result

    # 4. VPBUDDY_DIRECT=1 (主 session 跑时不调 LLM)
    if os.environ.get("VPBUDDY_DIRECT"):
        result["triggered"] = True
        result["agent_path"] = "direct"
        for kind, p in paths.items():
            result["files"][kind] = {"path": str(p), "written": False, "note": "DIRECT mode skips LLM"}
        return result

    # 5. 真触发 — 优先 in-process AIAgent (复用 cached session)
    if not _AGENT_AVAILABLE:
        result["error"] = "AIAgent not available"
        return result

    holder: dict[str, Any] = {"done": False, "result": None, "error": None}

    def _runner():
        try:
            agent = _get_or_create_agent(meeting_id, "batch_docs")
            response = agent.chat(prompt)
            holder["result"] = response
        except Exception as e:
            holder["error"] = e
        finally:
            holder["done"] = True

    t = threading.Thread(target=_runner, daemon=True, name=f"batch_docs-{meeting_id[:8]}")
    t.start()
    t.join(timeout=timeout)

    if not holder["done"]:
        result["error"] = f"timeout after {timeout}s"
        result["agent_path"] = "in-process"
        result["elapsed_sec"] = time.time() - t0
        return result
    if holder["error"]:
        result["error"] = f"{type(holder['error']).__name__}: {str(holder['error'])[:200]}"
        result["agent_path"] = "in-process"
        result["elapsed_sec"] = time.time() - t0
        return result

    # 6. 验证 5 文件 (等文件系统 sync)
    time.sleep(0.3)
    for kind, p in paths.items():
        if p.exists() and p.stat().st_size > 0:
            result["files"][kind] = {
                "path": str(p),
                "size": p.stat().st_size,
                "written": True,
            }
        else:
            result["files"][kind] = {
                "path": str(p),
                "size": 0,
                "written": False,
                "error": "file not found or empty after agent.chat()",
            }

    any_written = any(f["written"] for f in result["files"].values())
    result["triggered"] = any_written
    result["any_written"] = any_written
    result["elapsed_sec"] = time.time() - t0
    result["agent_path"] = "in-process"

    # 7. 推 SSE doc-update (每个写入的文件)
    if any_written:
        try:
            from ..realtime_server import push_event
            for kind, finfo in result["files"].items():
                if finfo["written"]:
                    content = paths[kind].read_text(encoding="utf-8", errors="replace")
                    push_event(meeting_id, "doc-update", {
                        "kind": kind,
                        "status": "stored",
                        "doc_size": finfo["size"],
                        "meeting_id": meeting_id,
                        "content": content,
                        "updated_at": datetime.now().isoformat(),
                        "is_demo": False,
                    })
        except Exception as e:
            logger.warning(f"[batch_docs] push SSE doc-update failed: {e}")

        # 8. 推 docs-complete 检查 (沿用 ui_server_helpers)
        try:
            from ..ui_server_helpers import check_all_docs_stored_notify
            check_all_docs_stored_notify(meeting_id, doc_kinds=BATCH_DOC_KINDS)
        except Exception as e:
            logger.warning(f"[batch_docs] check_all_docs_stored_notify failed: {e}")

    return result


# 老 doc_kind (req/arch/tasks/api/risk) 兼容 stub — 返警告, 引导用 batch_docs
_DEPRECATED_KINDS = {"req", "arch", "tasks", "api", "risk"}


def trigger_deprecated_kind(meeting_id: str, doc_kind: str) -> dict[str, Any]:
    """兼容老调用方 (trigger_sub_session(mid, kind)). 老 kind 引导到 batch_docs."""
    return {
        "session_id": f"meeting:{meeting_id}:{doc_kind}",
        "triggered": False,
        "deprecated": True,
        "error": (
            f"doc_kind '{doc_kind}' deprecated since v0.7 (ADR-0029). "
            f"5 文档已合并为 batch_docs agent. "
            f"Use trigger_batch_docs('{meeting_id}') instead."
        ),
        "hint": f"call trigger_batch_docs('{meeting_id}')",
    }
