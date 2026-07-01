"""测试 vpbuddy.tools.web_search — 不真发请求, 用 monkeypatch mock DDGS."""

from __future__ import annotations
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.tools import web_search


def _fake_ddgs_ok():
    """返回 3 条结果的 fake DDGS context manager."""

    class _DDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, region="zh-cn", max_results=5):
            return [
                {"title": f"Result {i}", "href": f"https://example.com/{i}", "body": f"snippet {i} for {query}"}
                for i in range(min(3, max_results))
            ]

    return _DDGS()


def test_search_returns_results(monkeypatch):
    # 需要保证 duckduckgo_search 模块被 import 时不报错 (mock 掉)
    import types
    fake_mod = types.ModuleType("duckduckgo_search")
    setattr(fake_mod, "DDGS", _fake_ddgs_ok)
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake_mod)

    out = web_search.search("Q4 营收", max_results=3)
    assert out["ok"] is True
    assert out["count"] == 3
    assert out["results"][0]["title"] == "Result 0"
    assert out["results"][0]["url"].startswith("https://")


def test_search_rejects_empty_query():
    out = web_search.search("", max_results=5)
    assert out["ok"] is False
    assert "query" in out["error"]


def test_search_rejects_whitespace_only():
    out = web_search.search("   ", max_results=5)
    assert out["ok"] is False


def test_search_clamps_max_results():
    """max_results 越界要 clamp."""
    # 不真发请求, 用 fake DDGS 验证传给它的 max_results 被 clamp
    captured = {}

    class _DDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, region="zh-cn", max_results=5):
            captured["k"] = max_results
            return []

    import types
    fake_mod = types.ModuleType("duckduckgo_search")
    setattr(fake_mod, "DDGS", _DDGS)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake_mod)

    web_search.search("x", max_results=999)
    assert captured["k"] == 20

    web_search.search("x", max_results=0)
    assert captured["k"] == 1
    monkeypatch.undo()


def test_search_handles_ddg_exception(monkeypatch):
    """DDG 抛异常 → 返回 ok=False, 不 raise."""

    class _DDGBoom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, region="zh-cn", max_results=5):
            raise RuntimeError("rate limit")

    import types
    fake_mod = types.ModuleType("duckduckgo_search")
    setattr(fake_mod, "DDGS", _DDGBoom)
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake_mod)

    out = web_search.search("x")
    assert out["ok"] is False
    assert "rate limit" in out["error"]
    assert out["results"] == []


def test_search_handles_missing_package(monkeypatch):
    """duckduckgo_search 未装 → 返回 ok=False + 友好提示."""
    # 删 sys.modules 让 ImportError 触发
    monkeypatch.delitem(sys.modules, "duckduckgo_search", raising=False)

    # 用 sys.meta_path 拦截 import
    import builtins as _b
    real_import = _b.__import__

    def fake_import(name, *args, **kwargs):
        if name == "duckduckgo_search":
            raise ImportError("No module named 'duckduckgo_search'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(_b, "__import__", fake_import)

    out = web_search.search("x")
    assert out["ok"] is False
    assert "duckduckgo-search" in out["error"]


def test_snippet_truncated_to_500(monkeypatch):
    """snippet 不超过 500 字符."""

    class _DDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, region="zh-cn", max_results=5):
            return [{"title": "T", "href": "u", "body": "x" * 9999}]

    import types
    fake_mod = types.ModuleType("duckduckgo_search")
    setattr(fake_mod, "DDGS", _DDGS)
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake_mod)

    out = web_search.search("x")
    assert out["ok"] is True
    assert len(out["results"][0]["snippet"]) == 500