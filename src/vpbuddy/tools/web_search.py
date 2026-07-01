"""网络搜索工具 — doc/demo agent 公开 web 搜索 (ADR-0025 Step B)

后端: DuckDuckGo (无 API key, pip 包 duckduckgo-search)
不接 LLM function calling 协议, 纯函数, agent 通过 terminal 调:
    python -c "from vpbuddy.tools.web_search import search; print(search('Q4 营收'))"
设计: KISS — 不引 MCP, 不引工具调用协议.

接口:
    search(query: str, max_results: int = 5, region: str = "zh-cn") -> dict
        返回 {"ok": bool, "results": [...], "error"?: str}

降级: DDG 失败 / 未装 → 返回 ok=False + 空结果, agent 走训练知识.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def search(query: str, max_results: int = 5, region: str = "zh-cn") -> dict[str, Any]:
    """公开 web 搜索 (DuckDuckGo).

    Args:
        query: 搜索关键词
        max_results: 返回条数 (1-20)
        region: 区域代码 wt-wt / us-en / zh-cn

    Returns:
        {"ok": True, "results": [{"title", "url", "snippet"}, ...], "count": N}
        或 {"ok": False, "error": "...", "results": []}
    """
    if not query or not query.strip():
        return {"ok": False, "error": "query 必填", "results": []}
    max_results = max(1, min(20, int(max_results)))

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return {
            "ok": False,
            "error": "duckduckgo-search 未装 (pip install duckduckgo-search)",
            "results": [],
        }

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, region=region, max_results=max_results))
    except Exception as e:
        logger.warning("web_search 失败: q=%r err=%s", query[:30], e)
        return {"ok": False, "error": f"网络搜索失败: {str(e)[:200]}", "results": []}

    results = [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")[:500]}
        for r in raw
    ]
    logger.info("web_search: q=%r region=%s hits=%d", query[:30], region, len(results))
    return {"ok": True, "results": results, "count": len(results)}


def main() -> None:
    """CLI: python -m vpbuddy.tools.web_search <query> [max_results] [region]"""
    import sys
    if len(sys.argv) < 2:
        print("用法: python -m vpbuddy.tools.web_search <query> [max_results] [region]", file=sys.stderr)
        sys.exit(2)
    query = sys.argv[1]
    max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    region = sys.argv[3] if len(sys.argv) > 3 else "zh-cn"
    out = search(query, max_results, region)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
