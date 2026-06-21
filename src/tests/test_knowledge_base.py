"""KnowledgeBase 单元测试

覆盖:
- init + 跨平台(NFS) 路径
- add_document(中文/英文)
- search(返回排序正确)
- meeting_id 过滤
- list_documents / delete_meeting
- 异常处理(空 query / 空 content)
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 关键:src/ 加进 path,且不下载模型(HF_HUB_OFFLINE)
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# 测试用临时 DB
TEST_DB = Path(tempfile.mkdtemp(prefix="vpbuddy_kb_test_")) / "kb.db"
os.environ["VPBUDDY_KB_DB"] = str(TEST_DB)

from vpbuddy.knowledge_base import KnowledgeBase, EMBED_DIM, get_kb


@pytest.fixture
def kb(tmp_path):
    """每个测试用全新 DB(避免测试间污染)"""
    db_path = tmp_path / "kb.db"
    k = KnowledgeBase(db_path=str(db_path))
    yield k
    k.close()


def test_init_creates_tables(kb):
    """初始化应该创建 documents + vec_documents 两表"""
    cur = kb._conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    names = {r[0] for r in cur.fetchall()}
    assert "documents" in names
    assert "vec_documents" in names


def test_add_document_returns_id(kb):
    """添加文档应该返回 id"""
    doc_id = kb.add_document("MTG01", "req", "支持 SSO 登录")
    assert isinstance(doc_id, int)
    assert doc_id > 0


def test_add_empty_content_returns_neg1(kb):
    """空内容应该返回 -1,不报错"""
    assert kb.add_document("MTG01", "req", "") == -1
    assert kb.add_document("MTG01", "req", "   ") == -1


def test_search_returns_relevant(kb):
    """检索应该返回语义相关的文档"""
    kb.add_document("MTG01", "req", "支持 SSO 单点登录,对接企业 AD 域控")
    kb.add_document("MTG02", "req", "微信扫码登录,降低注册门槛")
    kb.add_document("MTG03", "api", "返回 JSON 数据格式")

    # "微信" 应该最匹配 MTG02
    results = kb.search("微信扫码", top_k=2)
    assert len(results) > 0
    assert "微信" in results[0]["full_content"] or "wechat" in results[0]["full_content"].lower()


def test_search_with_meeting_filter(kb):
    """meeting_id 过滤应该工作"""
    kb.add_document("MTG01", "req", "SSO 登录")
    kb.add_document("MTG02", "req", "SSO 登录")
    results = kb.search("SSO", top_k=5, meeting_id="MTG01")
    assert all(r["meeting_id"] == "MTG01" for r in results)


def test_search_empty_query(kb):
    """空 query 应该返回空列表"""
    kb.add_document("MTG01", "req", "test")
    assert kb.search("", top_k=5) == []
    assert kb.search("  ", top_k=5) == []


def test_list_documents(kb):
    """list_documents 应该返回所有文档"""
    kb.add_document("MTG01", "req", "a")
    kb.add_document("MTG01", "api", "b")
    kb.add_document("MTG02", "req", "c")

    all_docs = kb.list_documents()
    assert len(all_docs) == 3

    mtg01_docs = kb.list_documents(meeting_id="MTG01")
    assert len(mtg01_docs) == 2
    assert all(d["meeting_id"] == "MTG01" for d in mtg01_docs)


def test_delete_meeting(kb):
    """delete_meeting 应该删除该会议所有文档"""
    kb.add_document("MTG01", "req", "a")
    kb.add_document("MTG01", "api", "b")
    kb.add_document("MTG02", "req", "c")

    n = kb.delete_meeting("MTG01")
    assert n == 2
    assert len(kb.list_documents(meeting_id="MTG01")) == 0
    assert len(kb.list_documents(meeting_id="MTG02")) == 1


def test_upsert_same_meeting_kind(kb):
    """同 meeting_id + doc_kind 应该覆盖"""
    kb.add_document("MTG01", "req", "v1 内容")
    kb.add_document("MTG01", "req", "v2 更新内容")

    docs = kb.list_documents(meeting_id="MTG01")
    assert len(docs) == 1
    assert "v2" in kb._conn.execute(
        "SELECT content FROM documents WHERE meeting_id='MTG01'"
    ).fetchone()[0]


def test_embed_dim_is_384(kb):
    """embedding 维度应该是 384(MiniLM-L12)"""
    assert EMBED_DIM == 384
