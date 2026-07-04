"""e2e — 文档内容质量验收 (用户视角)

测什么:
1. docs 内容预审数据包含了实际文本 (不是 "[object Object]")
2. req.md: 包含需求分析 (用户说了"规范风险评估", 文档应体现)
3. risk.md: 包含风险条目
4. arch.md: 要么有架构分析, 要么是合法占位
5. api.md: 要么有接口定义, 要么是合法占位
6. tasks.md: 包含任务条目
7. demo HTML: 有可展示的 UI 骨架

跑法: RUN_E2E=1 pytest src/tests/e2e/test_doc_content_validation.py -v -m e2e
"""
from __future__ import annotations

import os
import re

import pytest

from .conftest import (
    http_get,
    http_get_text,
    poll_docs,
)

pytestmark = pytest.mark.e2e

GPU_URL = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")


class TestDocContentHasStructure:
    """文档内容有结构 (markdown 标题 + bullet points)."""

    @pytest.fixture(autouse=True)
    def _ensure_meeting(self):
        """为测试流程创建/获取一个已有文档的会议.

        如果 test_full_e2e_docs_generation.py 已跑过, 复用其 meeting_id.
        否则自己上传一个.
        """
        mid = getattr(pytest, "_e2e_meeting_id", None)
        if not mid:
            # 自己上传
            from .conftest import build_upload_multipart, http_post, generate_wav
            wav = generate_wav(20.0)
            body, ct = build_upload_multipart(wav, project_name="e2e-content-val",
                                              platform="e2e_content")
            resp = http_post(f"{GPU_URL}/api/meetings/upload", body, ct, timeout=180)
            mid = resp.get("meeting_id", "")
            if not mid:
                pytest.skip("upload 失败, 无法验证文档内容")
            pytest._e2e_meeting_id = mid
            # 等文档生成
            poll_docs(GPU_URL, mid, timeout=300, poll_interval=15)

        self._mid = mid
        self._url = GPU_URL

    def _fetch_doc(self, kind: str) -> str:
        """通过 API 获取文档的完整内容."""
        resp = http_get(f"{self._url}/api/meetings/{self._mid}", timeout=10)
        # content_preview 是前 1200 字符
        for doc in resp.get("docs", []):
            if doc["kind"] == kind:
                return doc.get("content_preview", "")
        return ""

    # ---
    # 每个文档的结构完整性
    # ---

    def test_req_has_title_and_bullets(self):
        """req.md: 有 # 标题 + 至少 1 个 bullet."""
        content = self._fetch_doc("req")
        assert content.startswith("#"), f"req.md 应该以 # 开头: {content[:50]}"
        assert "-" in content, f"req.md 应含 bullet points: {content[:200]}"

    def test_risk_has_risk_items(self):
        """risk.md: 含 RISK- 风险条目."""
        content = self._fetch_doc("risk")
        assert "RISK-" in content or "风险" in content, \
            f"risk.md 应含风险条目: {content[:200]}"

    def test_tasks_has_task_items(self):
        """tasks.md: 含具体任务描述."""
        content = self._fetch_doc("tasks")
        # 要么是任务 bullet, 要么是合法的"暂无"占位
        has_tasks = "-" in content and len(content) > 50
        if not has_tasks:
            # 占位也行, 但必须清楚说明
            assert "暂无" in content or "本会议" in content, \
                f"tasks.md 既无任务也无占位: {content[:200]}"

    def test_arch_has_content_or_placeholder(self):
        """arch.md: 有架构讨论或合法占位."""
        content = self._fetch_doc("arch")
        # 架构可能为空 (用户没讨论此话题) — 合法状态
        if len(content) < 30:
            assert "暂无" in content or "本会议" in content or "本报告" in content, \
                f"arch.md 短但无占位标记: {content!r}"
        else:
            # 有内容
            assert re.search(r"架构|方案|技术|系统", content), \
                f"arch.md 有内容但无架构关键字: {content[:200]}"

    def test_api_has_content_or_placeholder(self):
        """api.md: 有接口定义或合法占位."""
        content = self._fetch_doc("api")
        if len(content) < 30:
            assert "暂无" in content or "本会议" in content, \
                f"api.md 短但无占位标记: {content!r}"

    def test_demo_html_structure(self):
        """demo HTML 有基本结构."""
        versions = http_get(f"{self._url}/api/meetings/{self._mid}/demo-versions",
                            timeout=10)
        version_list = versions.get("versions", [])
        if not version_list:
            pytest.skip("无 demo 版本")
        latest = version_list[-1]
        html = http_get_text(f"{self._url}{latest.get('url', '')}", timeout=10)
        # 有效 HTML: 有 body 或 div 或 script
        has_body = bool(re.search(r"<(body|div|section|main|script)", html))
        assert has_body, f"demo HTML 无可见内容: {html[:200]}"


class TestDocContentIsNotGarbage:
    """文档内容不是垃圾 (非默认/模板/重复)."""

    @pytest.fixture(autouse=True)
    def _ensure_meeting(self):
        mid = getattr(pytest, "_e2e_meeting_id", None)
        if not mid:
            pytest.skip("需已有文档生成的会议")
        self._mid = mid
        self._url = GPU_URL

    def _fetch_doc(self, kind: str) -> str:
        resp = http_get(f"{self._url}/api/meetings/{self._mid}", timeout=10)
        for doc in resp.get("docs", []):
            if doc["kind"] == kind:
                return doc.get("content_preview", "")
        return ""

    def test_no_lorem_ipsum(self):
        """任何文档不应含 lorem ipsum / dummy / placeholder 之类."""
        for kind in ("req", "arch", "tasks", "api", "risk"):
            c = self._fetch_doc(kind)
            for bad in ("lorem", "ipsum", "dummy", "placeholder", "[object Object]"):
                assert bad not in c.lower(), \
                    f"{kind}.md 含默认占位文本 '{bad}': {c[:200]}"

    def test_docs_not_all_identical(self):
        """5 个文档不应全是同一份内容."""
        contents = {}
        for kind in ("req", "arch", "tasks", "api", "risk"):
            c = self._fetch_doc(kind)
            contents[kind] = c[:100]

        unique = set(contents.values())
        assert len(unique) >= 2, \
            f"所有文档内容相同 (LLM 没区分): {contents}"

    def test_risk_has_unique_id(self):
        """risk.md 的风险条目有唯一 RISK-XXXXXX 编号."""
        c = self._fetch_doc("risk")
        risk_ids = re.findall(r"RISK-([A-F0-9]+)", c)
        assert len(risk_ids) > 0, f"risk.md 无 RISK- 编号: {c[:200]}"
        # 编号各不相同
        assert len(set(risk_ids)) == len(risk_ids), \
            f"风险编号重复: {risk_ids}"
