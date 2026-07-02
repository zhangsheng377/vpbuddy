"""UI server 辅助模块 — 2026-06-28 ADR-0018, 2026-07-01 ADR-0022

放一些跨 handler 复用的小函数, 避免 ui_server.py 越改越大。

⚠️ 2026-07-01 ADR-0022 重要语义变更:
    6 docs 全 generated **不再** 触发 close_meeting (用户拍板).
    只推 docs-complete SSE, 会议继续 — 用户切会议 / 关客户端 / 手动 [结束会议] 才真正 close.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def check_all_docs_stored_notify(meeting_id: str, doc_kinds: list[str] | None = None) -> bool:
    """检查 6 个文档是否全部 stored (文件存在且非空).

    Returns: True if all 6 docs exist, False otherwise.

    2026-07-01 重命名 + 语义改 (前: check_all_docs_stored_and_close):
    - 前: 6 docs 全 stored → push meeting-complete + close_meeting (SSE 退出)
    - 新: 6 docs 全 stored → 静默返 True, 不推 SSE, 不关会议
            (客户端通过 doc-status 事件已实时看到 6 块文档填充, 不需要重复通知)

    会议真正结束走 close_meeting_endpoint (POST /api/meetings/{id}/close).

    2026-07-02 进一步简化: 不再 push_event("docs-complete", ...) — 该事件无客户端消费,
    e2e 实测前端 main.js 0 引用, Tauri Rust 后端 SSE 也不透传. UI 靠 docs 面板实时
    渲染即可知道完成状态, 不需要额外 banner.
    """
    from .ui_server import _doc_path
    if doc_kinds is None:
        doc_kinds = ["req", "arch", "tasks", "api", "risk", "demo"]
    all_stored = True
    sizes = {}
    for kind in doc_kinds:
        # 2026-07-01 fix: 之前用 meeting_dir / f"{kind}.md" 找 demo 文件,
        # 但 demo 在 meeting_dir/demo/demo.html (ui_server._doc_path 的逻辑).
        # 现在用 _doc_path (DRY — 唯一真相源).
        path = _doc_path(meeting_id, kind)
        if not path.exists() or path.stat().st_size == 0:
            all_stored = False
            sizes[kind] = 0
        else:
            sizes[kind] = path.stat().st_size
    if not all_stored:
        return False
    logger.info(
        f"[{meeting_id}] 6 docs 全部 stored ({sum(sizes.values())} bytes), "
        f"返 True 不推 docs-complete SSE 不关会议 (ADR-0022 + 2026-07-02 删 docs-complete 死代码)"
    )
    # 2026-07-01 ADR-0023 Phase 5: 6 docs 全部生成 → agent 主动 chat 通知
    # (保留 — 主动 chat 是 _append_chat_message + push_event("chat-message", ...) 走的另一条路,
    #  跟 docs-complete 死代码无关, 不在本轮清理范围)
    try:
        from .agent_proactive import trigger as _proactive_trigger
        state_summary = ""
        try:
            from .storage import MeetingStorage
            from .sub_session_controller import format_state_summary
            st = MeetingStorage().load(meeting_id)
            state_summary = format_state_summary(st)
        except Exception:
            pass
        _proactive_trigger(meeting_id, "docs_complete", state_summary=state_summary)
    except Exception as e:
        logger.warning(f"[{meeting_id}] proactive docs_complete trigger failed: {e}")
    return True


# ── 2026-07-01 ADR-0022: 兼容别名 ──
# 老代码引用 check_all_docs_stored_and_close, 旧语义 = 推 meeting-complete + close_meeting
# 现在拆成 notify + 独立 close endpoint, 但保留别名只做 notify 部分(防 silent semantic break).
def check_all_docs_stored_and_close(meeting_id: str, doc_kinds: list[str] | None = None) -> bool:
    """⚠️ DEPRECATED 2026-07-01 (ADR-0022). 用 check_all_docs_stored_notify 替代.

    老逻辑 (推 meeting-complete + close_meeting) 已废弃 — 6 docs 完成不再触发会议关闭.
    保留 stub: 只 notify, 不 close. 调用方应迁移到 check_all_docs_stored_notify.
    """
    logger.warning(
        f"[{meeting_id}] check_all_docs_stored_and_close 已废弃 (ADR-0022), "
        f"改用 check_all_docs_stored_notify. 旧 close_meeting 行为已拆到 POST /api/meetings/{meeting_id}/close."
    )
    return check_all_docs_stored_notify(meeting_id, doc_kinds)
