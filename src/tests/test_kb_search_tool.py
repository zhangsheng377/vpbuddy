"""测试 vpbuddy.tools.kb_search — 纯函数接口, 不调 HTTP, 不调真实 Chroma (mocked)."""

from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

# 让 src/ 可导入 (tests 不一定装包)
sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.tools import kb_search
from vpbuddy import rag_backend


class _FakeRAG:
    """替换 get_rag() 返回的 ChromaRAG, 不真起 embedding."""

    def __init__(self):
        self.calls = []

    def query(self, query_text: str, top_k: int = 5, where: dict | None = None):
        self.calls.append({"q": query_text, "k": top_k, "where": where})
        return [
            {
                "id": "mtg1:abc",
                "metadata": {"meeting_id": "mtg1", "source": "upload:report.pdf"},
                "document": "Q4 营收 1.2 亿, 同比 +18%",
                "distance": 0.12,
            },
            {
                "id": "mtg1:def",
                "metadata": {"meeting_id": "mtg1", "source": "upload:notes.md"},
                "document": "客户问毛利率提升路径",
                "distance": 0.34,
            },
        ]


@pytest.fixture
def fake_rag(monkeypatch):
    """注入 fake RAG, 不真起 chromadb."""
    fake = _FakeRAG()
    monkeypatch.setattr(rag_backend, "_rag", fake)
    return fake


def test_search_returns_results(fake_rag):
    out = kb_search.search("mtg1", "Q4 营收", user_id="user_abc")
    assert out["ok"] is True
    assert out["count"] == 2
    assert out["results"][0]["source"] == "upload:report.pdf"
    assert "Q4 营收" in out["results"][0]["snippet"]


def test_search_forces_user_id_filter(fake_rag):
    """ADR-0047: where 必须带 user_id (按用户隔离, 不再按 meeting_id)."""
    kb_search.search("mtg1", "anything", user_id="user_abc")
    assert "user_id" in fake_rag.calls[0]["where"]
    assert fake_rag.calls[0]["where"]["user_id"] == "user_abc"


def test_search_rejects_empty_meeting_id(fake_rag):
    out = kb_search.search("", "anything", user_id="user_abc")
    assert out["ok"] is False
    assert "meeting_id" in out["error"]
    assert fake_rag.calls == []  # 没真去查


def test_search_rejects_empty_query(fake_rag):
    out = kb_search.search("mtg1", "   ", user_id="user_abc")
    assert out["ok"] is False
    assert "query" in out["error"]
    assert fake_rag.calls == []


def test_search_clamps_top_k(fake_rag):
    """top_k 越界要 clamp 到 [1, 20]."""
    kb_search.search("mtg1", "x", user_id="user_abc", top_k=999)
    assert fake_rag.calls[0]["k"] == 20
    kb_search.search("mtg1", "x", user_id="user_abc", top_k=0)
    assert fake_rag.calls[1]["k"] == 1


def test_search_handles_rag_exception(monkeypatch):
    """RAG 抛异常 → 返回 ok=False + error, 不 raise."""
    class _Boom:
        def query(self, **_kw):
            raise RuntimeError("chroma 连不上")

    monkeypatch.setattr(rag_backend, "_rag", _Boom())
    out = kb_search.search("mtg1", "x", user_id="user_abc")
    assert out["ok"] is False
    assert "chroma 连不上" in out["error"]


def test_snippet_truncated_to_500(fake_rag):
    """snippet 不超过 500 字符 (防 LLM context 爆炸)."""
    out = kb_search.search("mtg1", "x", user_id="user_abc")
    for r in out["results"]:
        assert len(r["snippet"]) <= 500


def test_search_falls_back_to_owner_id(monkeypatch, tmp_path):
    """v0.22.6: 不传 user_id 时从 MeetingState.owner_id 回退."""
    from vpbuddy.storage import MeetingStorage
    from vpbuddy.state import MeetingState

    st = MeetingStorage(data_dir=str(tmp_path))
    state = MeetingState(meeting_id="mtg_back")
    state.owner_id = "owner_xyz"
    st.save(state)

    original_load = MeetingStorage.load

    class _PatchedStorage:
        def load(self, mid):
            if mid == "mtg_back":
                return state
            raise FileNotFoundError

    monkeypatch.setattr(MeetingStorage, "load", _PatchedStorage().load)

    out = kb_search.search("mtg_back", "anything")
    assert out["ok"] is False
    assert "无法确定用户身份" not in out.get("error", "")


def test_search_no_user_id_no_meeting_state(monkeypatch):
    """v0.22.6: 不传 user_id 且 MeetingState 不存在 → 返错误."""
    from vpbuddy.storage import MeetingStorage

    def _fail_load(self, mid):
        raise FileNotFoundError

    monkeypatch.setattr(MeetingStorage, "load", _fail_load)

    out = kb_search.search("nonexistent_mtg", "x")
    assert out["ok"] is False
    assert "用户身份" in out["error"]


def test_search_user_id_not_in_where_for_empty_string(monkeypatch):
    """v0.22.6: user_id='' 且 load 失败 → 返错误信息."""
    from vpbuddy.storage import MeetingStorage

    def _fail_load(self, mid):
        raise FileNotFoundError

    monkeypatch.setattr(MeetingStorage, "load", _fail_load)
    monkeypatch.setattr(rag_backend, "_rag", _FakeRAG())

    out = kb_search.search("no_such_meeting", "x")
    assert out["ok"] is False
    assert "用户身份" in out["error"]