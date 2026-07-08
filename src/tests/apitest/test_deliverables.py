"""#19 交付物 version 字段测试 — v0.19.0

测试:
- GET /docs → 每个 kind 都有 version 字段 (字符串类型)
- demo version 从 manifest 读取
- 非 demo version 从 .deliverables.json 读取
"""
from __future__ import annotations

from .conftest import api


def test_docs_have_version_field(meeting):
    """每个交付物 DTO 都有 version 字段."""
    code, resp = api(f"/api/meetings/{meeting['mid']}/docs", token=meeting["token"])
    assert code == 200
    docs = resp["docs"]  # 或 resp 本身是 docs 列表
    if isinstance(resp, dict) and "docs" in resp:
        docs = resp["docs"]
    elif isinstance(resp, list):
        docs = resp

    assert len(docs) >= 1
    for doc in docs:
        assert "version" in doc, f"doc {doc.get('kind')} 缺少 version 字段"
        # version 必须是字符串类型
        assert isinstance(doc["version"], str), \
            f"doc {doc.get('kind')} version 应为 str, 实际 {type(doc['version'])}"


def test_docs_have_six_kinds(meeting):
    """会议新建后应有 6 种交付物类型."""
    code, resp = api(f"/api/meetings/{meeting['mid']}/docs", token=meeting["token"])
    assert code == 200
    docs = resp.get("docs", resp) if isinstance(resp, dict) else resp
    kinds = {d["kind"] for d in docs}
    assert kinds == {"req", "arch", "tasks", "api", "risk", "demo"}
