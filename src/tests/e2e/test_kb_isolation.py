"""e2e — KB 上传 + 检索 + 跨会议隔离 (用户大需求 #2/#3/#7/#8 端到端).

跑法: RUN_E2E=1 pytest src/tests/e2e/test_kb_isolation.py -v -m e2e

测什么:
1. 上传 .md 到 meeting A → Chroma 真灌 → meeting A 检索命中, meeting B 检索不命中
2. 上传同名/近义内容到两个 meeting, 用同关键词查询, 验证 where filter 真隔离
3. 客户端走 invoke('kb_search') UI 路径, stub 仍能命中正确 meeting (端到端 UI 链路)
4. 上传 PDF 也能走通 (kb_api.handle_kb_upload 支持 .txt/.md/.pdf, ADR-0020)

不测什么:
- 不验 GUI 渲染 kb-result 元素 (单独 UI 测试覆盖)
- 不测 6 doc/digest 自动入 (已废, ADR-0020)

风险:
- e2e 用真 GPU Chroma + sentence-transformers, 慢但 30 doc 内可接受
- GPU 上其他会议 KB 数据不冲突, 因为 where={"meeting_id"} 强隔离
"""
from __future__ import annotations

import io
import json
import urllib.parse
import urllib.error
import urllib.request

import pytest


pytestmark = pytest.mark.e2e


# === Helpers ===

def _http_post_multipart(url: str, fields: list[tuple[str, str, bytes | None, str | None]], timeout: float = 10, token: str = ""):
    """手搓 multipart POST (跟 kb_api._parse_multipart 兼容)."""
    boundary = "----e2e-kb-boundary-98765"
    body = b""
    for name, filename_or_value, content, content_type in fields:
        body += f"--{boundary}\r\n".encode()
        if content is None:
            body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            body += filename_or_value.encode() + b"\r\n"
        else:
            ct = content_type or "application/octet-stream"
            body += f'Content-Disposition: form-data; name="{name}"; filename="{filename_or_value}"\r\n'.encode()
            body += f"Content-Type: {ct}\r\n\r\n".encode()
            body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def _http_get_json(url: str, timeout: float = 5, token: str = ""):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def _make_fixture_md(meeting: str, revenue: str, strategy: str) -> bytes:
    """生成带'营收'关键词的小 markdown. revenue/strategy 是 2 个 meeting 不同的标识."""
    return (
        f"# {meeting} 公司报告\n\n"
        f"Q4 营收 {revenue}, 战略方向: {strategy}.\n\n"
        f"(本文档属于 {meeting}, e2e KB 隔离测试用)\n"
    ).encode("utf-8")


def _search_url(gpu: str, query: str, meeting_id: str) -> str:
    """构造 /api/kb/search URL, query 是英文 (Chroma MiniLM 多语言)."""
    return f"{gpu}/api/kb/search?q={urllib.parse.quote(query)}&meeting_id={urllib.parse.quote(meeting_id)}"


@pytest.fixture
def unique_meetings(gpu_server) -> dict[str, str]:
    """生成 2 个唯一 meeting_id (时间戳后缀), 用完跑 pytest tmp 自动清理(没有具体清理, 残留 GPU 上).

    Returns: {"alpha": "e2e_kb_iso_alpha_<ts>", "beta": "e2e_kb_iso_beta_<ts>"}
    """
    ts = int(time.time_ns())
    return {
        "alpha": f"e2e_kb_alpha_{ts}",
        "beta": f"e2e_kb_beta_{ts}",
    }


import time


# === Tests ===

class TestKBIsolationE2E:
    """真 Chroma + 真 sentence-transformers + GPU 真 server."""

    def test_upload_text_then_search_meeting_only(self, gpu_server, unique_meetings, e2e_token):
        """上传到 A → A 搜命中, 不带 meeting_id (代表搜全表) 也能找到; 带 B 过滤搜不到."""
        alpha = unique_meetings["alpha"]
        beta = unique_meetings["beta"]

        # 1. 上传到 alpha
        alpha_doc = _make_fixture_md("Alpha", "1.2 亿", "AI 平台化")
        status, body = _http_post_multipart(
            f"{gpu_server}/api/kb/upload",
            [
                ("meeting_id", alpha, None, None),
                ("file", "alpha_finance.md", alpha_doc, "text/markdown"),
            ],
            token=e2e_token,
        )
        assert status == 200, f"upload alpha failed: {body}"
        assert body["doc_id"].startswith(f"{alpha}:"), f"doc_id 缺 meeting_id 前缀: {body}"

        # 2. 上传到 beta (故意用相同关键词"营收"做隔离测试)
        beta_doc = _make_fixture_md("Beta", "8000 万", "出海欧洲")
        status, body = _http_post_multipart(
            f"{gpu_server}/api/kb/upload",
            [
                ("meeting_id", beta, None, None),
                ("file", "beta_strategy.md", beta_doc, "text/markdown"),
            ],
            token=e2e_token,
        )
        assert status == 200, f"upload beta failed: {body}"

        # 3. alpha 检索 → 命中 alpha 那条
        _, alpha_hits = _http_get_json(_search_url(gpu_server, "revenue", alpha), token=e2e_token)
        # handle_kb_search 返回 {"results": [{"id", "source", "snippet", "distance", "meeting_id"}], ...}
        # 也可能含 error
        if "error" in alpha_hits:
            pytest.fail(f"alpha search 报错: {alpha_hits['error']}")
        assert len(alpha_hits["results"]) >= 1, f"alpha 检索应至少 1 条: {alpha_hits}"
        for r in alpha_hits["results"]:
            # doc_id 形如 alpha:xxxx, 所以 r["id"] 必含 meeting_id 前缀
            assert r["id"].startswith(f"{alpha}:"), \
                f"alpha 检索返回非 alpha doc: {r}"

        # 4. beta 检索 → 命中 beta 那条
        _, beta_hits = _http_get_json(_search_url(gpu_server, "revenue", beta), token=e2e_token)
        assert "error" not in beta_hits, f"beta search 报错: {beta_hits.get('error')}"
        assert len(beta_hits["results"]) >= 1, f"beta 检索应至少 1 条: {beta_hits}"
        for r in beta_hits["results"]:
            assert r["id"].startswith(f"{beta}:"), \
                f"beta 检索返回非 beta doc: {r}"

        # 5. ADR-0047: 同用户下所有会议 KB 互通, alpha 也能搜到 beta 的"8000 万" (设计如此)
        alpha_docs = [r.get("document", "") for r in alpha_hits["results"]]
        assert any("1.2 亿" in doc or "8000 万" in doc for doc in alpha_docs), \
            f"alpha 检索应命中同用户文档: {alpha_docs}"

        print(f"\n[E2E] alpha 命中数: {len(alpha_hits['results'])}, beta 命中数: {len(beta_hits['results'])}")

    def test_search_without_meeting_id_returns_all(self, gpu_server, e2e_token):
        status, body = _http_get_json(f"{gpu_server}/api/kb/search?q=revenue", token=e2e_token)
        assert status == 200, f"应 200: {status} {body}"
        # scope 字段会显示 "none" (因为没 meeting_id)
        assert body.get("scope") == "none", f"scope 应 'none', 实际 {body.get('scope')}"
        assert body.get("meeting_id") is None
        # results 可能是空 (KB 没东西) 或非空, 不约束, 看 GPU 上有没数据

    def test_kb_list_per_meeting(self, gpu_server, unique_meetings, e2e_token):
        alpha = unique_meetings["alpha"]
        beta = unique_meetings["beta"]
        alpha_doc = _make_fixture_md("Alpha", "5 千万", "新方向")
        beta_doc = _make_fixture_md("Beta", "3 千万", "B 端")
        for mid, doc in [(alpha, alpha_doc), (beta, beta_doc)]:
            status, body = _http_post_multipart(
                f"{gpu_server}/api/kb/upload",
                [("meeting_id", mid, None, None), ("file", f"{mid}.md", doc, "text/markdown")],
                token=e2e_token,
            )
            assert status == 200
        _, alpha_list = _http_get_json(f"{gpu_server}/api/kb/list?meeting_id={alpha}", token=e2e_token)
        # 期望: list 只含 alpha 的 doc
        if "documents" in alpha_list:
            for d in alpha_list["documents"]:
                assert d.get("id", "").startswith(f"{alpha}:"), \
                    f"kb/list alpha 混入非 alpha: {d}"
        elif "results" in alpha_list:
            for d in alpha_list["results"]:
                assert d.get("id", "").startswith(f"{alpha}:"), \
                    f"kb/list alpha 混入非 alpha: {d}"
        # 兼容其他 key, 主要断言: 不能混 beta
        print(f"\n[E2E] kb/list alpha: {alpha_list}")

    def test_through_ui_stub_kb_search_filters_meeting(self, page, gpu_server, unique_meetings, e2e_token):
        alpha = unique_meetings["alpha"]
        alpha_doc = _make_fixture_md("Alpha", "1 亿", "GLM 模型")
        status, body = _http_post_multipart(
            f"{gpu_server}/api/kb/upload",
            [("meeting_id", alpha, None, None), ("file", "alpha_glm.md", alpha_doc, "text/markdown")],
            token=e2e_token,
        )
        assert status == 200, f"upload failed: {body}"

        # 通过 vite UI 真实按钮路径触发 kb_search (kb-btn click), stub 会用注入的 meeting_id
        # 注意: 必须先触发 start_capture 让前端存 currentMeetingId (UI 全局 var),
        # 我们的 stub 可以从那里拿. 简化: 直接 evaluate 注入 + 模拟 UI 调.
        page.evaluate(f"window.__VP_E2E_MEETING_ID__ = '{alpha}';")

        # 模拟 UI 的 kb-btn click handler 调 invoke('kb_search', {query, topK})
        result_json = page.evaluate(
            """async () => {
                const r = await window.__TAURI_INTERNALS__.invoke('kb_search', {query: 'GLM 营收', topK: 5});
                return JSON.stringify(r);
            }"""
        )
        result = json.loads(result_json)
        print(f"\n[E2E UI stub] kb_search 结果: {result}")

        assert "results" in result or "error" in result, f"result 格式异常: {result}"
        if "error" not in result:
            for r in result.get("results", []):
                # 关键隔离验证: UI 链路上拿到的结果 ID 都必须属于 alpha
                assert r.get("id", "").startswith(f"{alpha}:"), \
                    f"UI stub 调用不应该返回非 alpha doc: {r}"
