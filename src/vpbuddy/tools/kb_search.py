"""KB 检索工具 — doc/demo agent 调 RAG 拉当前会议用户上传资料 (ADR-0025 Step B)

不接 LLM function calling 协议, 是纯函数模块, doc agent 通过 terminal 调:
    python -c "from vpbuddy.tools.kb_search import search; print(search('mtg1', 'Q4 营收'))"
设计: KISS — agent 已经能调 terminal, 我们只暴露薄薄一层.

接口:
    search(meeting_id: str, query: str, top_k: int = 5) -> dict
        返回 {"ok": bool, "results": [...], "error"?: str}
"""

from __future__ import annotations
import json
import logging
from typing import Any

from ..rag_backend import get_rag

logger = logging.getLogger(__name__)


def search(meeting_id: str, query: str, top_k: int = 5) -> dict[str, Any]:
    """检索当前会议 KB (Chroma RAG, 强制 meeting_id 过滤).

    Args:
        meeting_id: 当前会议 ID (强制带 where 过滤)
        query: 检索关键词
        top_k: 返回前 N 条 (1-20)

    Returns:
        {"ok": True, "results": [{"id", "source", "snippet", "distance"}, ...], "count": N}
        或 {"ok": False, "error": "..."}
    """
    if not meeting_id:
        return {"ok": False, "error": "meeting_id 必填"}
    if not query or not query.strip():
        return {"ok": False, "error": "query 必填"}
    top_k = max(1, min(20, int(top_k)))

    try:
        rag = get_rag()
        raw = rag.query(query_text=query, top_k=top_k, where={"meeting_id": meeting_id})
    except Exception as e:
        logger.warning("kb_search 失败: meeting=%s err=%s", meeting_id, e)
        return {"ok": False, "error": f"KB 检索失败: {str(e)[:200]}"}

    results = [
        {
            "id": r.get("id", ""),
            "source": (r.get("metadata") or {}).get("source", ""),
            "snippet": (r.get("document") or "")[:500],
            "distance": r.get("distance", 0.0),
        }
        for r in raw
    ]
    logger.info("kb_search: meeting=%s q=%r hits=%d", meeting_id, query[:30], len(results))
    return {"ok": True, "results": results, "count": len(results)}


def main() -> None:
    """CLI: python -m vpbuddy.tools.kb_search <meeting_id> <query> [top_k]"""
    import sys
    if len(sys.argv) < 3:
        print("用法: python -m vpbuddy.tools.kb_search <meeting_id> <query> [top_k]", file=sys.stderr)
        sys.exit(2)
    meeting_id = sys.argv[1]
    query = sys.argv[2]
    top_k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    out = search(meeting_id, query, top_k)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()