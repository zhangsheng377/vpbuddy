"""UI server 辅助模块 — 2026-06-28 ADR-0018, 2026-07-01 ADR-0022

放一些跨 handler 复用的小函数, 避免 ui_server.py 越改越大。

⚠️ 2026-07-01 ADR-0022 重要语义变更:
    6 docs 全 generated **不再** 触发 close_meeting (用户拍板).
    只推 docs-complete SSE, 会议继续 — 用户切会议 / 关客户端 / 手动 [结束会议] 才真正 close.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def check_all_docs_stored_notify(meeting_id: str, doc_kinds: List[str] | None = None) -> bool:
    """检查 6 个文档是否全部 stored (文件存在且非空), 是则:

    1. push_event("docs-complete", {...}) — 让客户端收到信号, UI 显示"✅ 6 文档已生成"
    2. **不** 再调 close_meeting (ADR-0022 — 6 docs 完成 ≠ 会议结束)

    Returns: True if all 6 docs exist, False otherwise.

    2026-07-01 重命名 + 语义改 (前: check_all_docs_stored_and_close):
    - 前: 6 docs 全 stored → push meeting-complete + close_meeting (SSE 退出)
    - 新: 6 docs 全 stored → push docs-complete (新事件), SSE 保持, 会议继续

    会议真正结束走 close_meeting_endpoint (POST /api/meetings/{id}/close).
    """
    from .ui_server import DOCS_DIR, _doc_path
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
    # 全部 stored → 推 docs-complete (新事件, 不关会议)
    try:
        from .realtime_server import push_event
        push_event(meeting_id, "docs-complete", {
            "meeting_id": meeting_id,
            "status": "all_docs_stored",
            "doc_sizes": sizes,
            "note": "6 docs 已生成, 会议继续 (ADR-0022). 切会议/关客户端/手动结束才真正关闭.",
        })
        logger.info(
            f"[{meeting_id}] 6 docs 全部 stored ({sum(sizes.values())} bytes), "
            f"推 docs-complete (不关会议, ADR-0022)"
        )
    except Exception as e:
        logger.warning(f"[{meeting_id}] docs-complete push failed: {e}")
    return True


# ── 2026-07-01 ADR-0022: 兼容别名 ──
# 老代码引用 check_all_docs_stored_and_close, 旧语义 = 推 meeting-complete + close_meeting
# 现在拆成 notify + 独立 close endpoint, 但保留别名只做 notify 部分(防 silent semantic break).
def check_all_docs_stored_and_close(meeting_id: str, doc_kinds: List[str] | None = None) -> bool:
    """⚠️ DEPRECATED 2026-07-01 (ADR-0022). 用 check_all_docs_stored_notify 替代.

    老逻辑 (推 meeting-complete + close_meeting) 已废弃 — 6 docs 完成不再触发会议关闭.
    保留 stub: 只 notify, 不 close. 调用方应迁移到 check_all_docs_stored_notify.
    """
    logger.warning(
        f"[{meeting_id}] check_all_docs_stored_and_close 已废弃 (ADR-0022), "
        f"改用 check_all_docs_stored_notify. 旧 close_meeting 行为已拆到 POST /api/meetings/{meeting_id}/close."
    )
    return check_all_docs_stored_notify(meeting_id, doc_kinds)