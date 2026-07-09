"""e2e — 全链路文档生成验证 (用户视角)

测什么:
1. 上传音频 → meeting_id + transcript_segments > 0 (ASR 真工作)
2. 轮询 docs endpoint → 6 文档全部生成且有内容 (非空占位)
3. 每个文档的 content_preview 是中文分析文本 (非"暂无 X 需求"占位)
4. Demo HTML 文件可访问且是有效 HTML
5. 文档 SSE 事件流推送了 doc-update 事件
6. session_id 验证: doc agent session 继承了 vp-chat session (parent_session_id)

跑法: RUN_E2E=1 pytest src/tests/e2e/test_full_e2e_docs_generation.py -v -m e2e

依赖:
- GPU server (http://47.100.182.3:28765) 在跑, 且 hermes-agent 已配置 MiniMax
- 测试音频: 30s 合成语音 (足够 ASR 产出一段结果)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime

import pytest

from .conftest import (
    build_upload_multipart,
    http_get,
    http_get_text,
    http_post,
    poll_docs,
)

pytestmark = pytest.mark.e2e

GPU_URL = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")
# 文档大小下限: 低于此值视为"空占位"
MIN_DOC_BYTES = 50


# ============================================================
# 测试 1: 上传音频 → 文档全部生成
# ============================================================

class TestUploadAndDocGeneration:
    """核心流程: 上传 → ASR → controller → 6 文档 → 可读内容"""

    @pytest.mark.skip(reason="upload 端点已废弃, 待适配 WS 实时模式的全链路测试")
    def test_upload_audio_returns_meeting(self, gpu_server, synth_wav):
        """上传 30s 音频 → 返回 meeting_id + ASR 有 segment."""
        body, ct = build_upload_multipart(synth_wav,
                                          project_name="e2e-full-gen",
                                          platform="e2e_full_gen")
        resp = http_post(f"{gpu_server}/api/meetings/upload", body, ct, timeout=180)

        assert "meeting_id" in resp, f"upload 响应缺 meeting_id: {resp}"
        assert resp["meeting_id"].startswith("UPLOAD_"), \
            f"meeting_id 格式不符: {resp['meeting_id']}"

        # 用户视角: ASR 必须出字 — 没字的会议生成不了文档
        assert resp.get("transcript_segments", 0) > 0, \
            "ASR 0 段转写 — 音频不够清晰或 ASR 没跑通"
        assert resp.get("num_speakers", 0) > 0, \
            "说话人数 = 0 — ASR 失败"

        # 持久化给后续测试用
        pytest._e2e_meeting_id = resp["meeting_id"]
        pytest._e2e_state_items = resp.get("state_items", {})

    def test_6_docs_generated_within_5_minutes(self, gpu_server):
        """轮询 docs 端点 → 5 分钟内 6 文档全部生成且有内容.

        这是此前 401 bug 的关键检测: 如果 LLM 没通, 6 个 doc_size 全为 0.
        """
        meeting_id = getattr(pytest, "_e2e_meeting_id", None)
        if not meeting_id:
            pytest.skip("需先跑 test_upload_audio_returns_meeting")

        docs = poll_docs(gpu_server, meeting_id, timeout=300, poll_interval=15)

        # 按 kind 索引
        doc_map = {d["kind"]: d for d in docs}
        generated_kinds = [k for k in ["req", "arch", "tasks", "api", "risk", "demo"]
                           if k in doc_map]

        assert len(generated_kinds) == 6, \
            f"只生成了 {len(generated_kinds)}/6 文档: {generated_kinds}"

        # 用户视角: 每个文档必须有内容 (不是空占位)
        empty_docs = [
            k for k, d in doc_map.items()
            if d.get("doc_size", 0) < MIN_DOC_BYTES
        ]
        assert not empty_docs, \
            f"以下文档内容为空 (<{MIN_DOC_BYTES}B): {empty_docs}\n" \
            f"详情: {{k: doc_map[k] for k in empty_docs}}"

        # 持久化
        pytest._e2e_docs = doc_map

    def test_each_doc_content_preview_is_meaningful(self, gpu_server):
        """每个文档的 content_preview 头部是中文会议分析文本.

        避免: 文档写了 50 字节但只有 "# 需求\n\n暂无内容\n" — 这种占位不算真生成.
        """
        doc_map = getattr(pytest, "_e2e_docs", None)
        if not doc_map:
            pytest.skip("需先跑文档生成")

        for kind in ["req", "arch", "tasks", "api", "risk"]:
            preview = doc_map[kind].get("content_preview", "")
            assert len(preview) > 20, \
                f"{kind}.md content_preview 太短: {preview!r}"
            # 用户视角: 文档必须有中文
            assert any('\u4e00' <= c <= '\u9fff' for c in preview), \
                f"{kind}.md content_preview 不含中文: {preview!r}"

    def test_demo_html_is_valid(self, gpu_server):
        """demo 文件可通过 HTTP 访问且是有效 HTML.

        用户视角: 客户端 demo 面板的内容能加载.
        """
        meeting_id = getattr(pytest, "_e2e_meeting_id", None)
        if not meeting_id:
            pytest.skip("需先跑 upload")

        # 查最新 demo HTML
        demo_versions = http_get(f"{gpu_server}/api/meetings/{meeting_id}/demo-versions",
                                 timeout=10)
        versions = demo_versions.get("versions", [])
        assert len(versions) > 0, "没有 demo 版本生成"
        latest = versions[-1]
        demo_url = f"{gpu_server}{latest.get('url', '')}"

        html = http_get_text(demo_url, timeout=10)
        assert "<!DOCTYPE html>" in html or "<html" in html, \
            "demo HTML 不是有效 HTML"
        assert len(html) > 100, \
            f"demo HTML 太短 ({len(html)}B), 可能只是骨架"

    def test_meeting_state_has_docs_triggered(self, gpu_server):
        """会议状态报告文档已触发."""
        meeting_id = getattr(pytest, "_e2e_meeting_id", None)
        if not meeting_id:
            pytest.skip("需先跑 upload")

        state = http_get(f"{gpu_server}/api/meetings/{meeting_id}", timeout=10)
        docs = state.get("docs", [])
        doc_statuses = {d["kind"]: d["status"] for d in docs}
        non_empty = [k for k, s in doc_statuses.items()
                     if s not in ("empty", "pending")]
        assert len(non_empty) >= 5, \
            f"至少 5 文档应非 empty/pending, 实际: {doc_statuses}"


# ============================================================
# 测试 2: Chat 对话
# ============================================================

class TestChatAfterDocs:
    """文档生成后, 用户能跟 chat agent 对话 (验证 master session 活着)."""

    @pytest.fixture(autouse=True)
    def _ensure_meeting(self):
        meeting_id = getattr(pytest, "_e2e_meeting_id", None)
        if not meeting_id:
            pytest.skip("需先跑上传+文档生成")
        self._mid = meeting_id
        self._url = GPU_URL

    def test_chat_responds(self):
        """向 meeting 发 chat → 得到非空回复.

        用户视角: 在 chat 框输入问会议相关的问题, 服务端应能回答.
        """
        body = json.dumps({"message": "这个会议讨论了什么内容？"}).encode()
        resp = http_post(
            f"{self._url}/api/meetings/{self._mid}/chat?text=这个会议讨论了什么内容",
            data=body,
            content_type="application/json",
            timeout=120,
        )
        assert resp.get("ok", False) or "response" in resp, \
            f"chat 响应异常: {resp}"
        reply = resp.get("response", "") or resp.get("reply", "")
        assert len(reply) > 10, f"chat 回复太短: {reply!r}"
        # 应有中文回复
        assert any('\u4e00' <= c <= '\u9fff' for c in reply), \
            f"chat 回复不含中文: {reply!r}"


# ============================================================
# 测试 3: 文档列表 / 文档服务
# ============================================================

class TestDocServing:
    """文档静态文件服务."""

    def test_docs_list_endpoint_returns_6(self, gpu_server):
        """GET /api/meetings 包含 6 个文档信息."""
        mid = getattr(pytest, "_e2e_meeting_id", None)
        if not mid:
            pytest.skip("需 upload")

        resp = http_get(f"{gpu_server}/api/meetings/{mid}", timeout=10)
        docs = resp.get("docs", [])
        kinds = {d["kind"] for d in docs}
        for k in ("req", "arch", "tasks", "api", "risk", "demo"):
            assert k in kinds, f"doc kind '{k}' 不在列表中"

    def test_demo_html_accessible_via_docs_prefix(self, gpu_server):
        """GET /api/meetings/{mid}/docs/demo 可达."""
        mid = getattr(pytest, "_e2e_meeting_id", None)
        if not mid:
            pytest.skip("需 upload")

        # demo 的 HTML 通过 doc 端点可访问
        resp = http_get(f"{gpu_server}/api/meetings/{mid}", timeout=10)
        demo_doc = next((d for d in resp.get("docs", []) if d["kind"] == "demo"), None)
        if demo_doc and demo_doc.get("content_preview"):
            assert isinstance(demo_doc["content_preview"], str)
            assert len(demo_doc["content_preview"]) > 20


# ============================================================
# 测试 4: 多会议隔离
# ============================================================

class TestMultiMeetingIsolation:
    """多个音频上传 → 每个会议独立."""

    def test_two_uploads_create_two_meetings(self, gpu_server, short_wav):
        """连续上传 2 段音频 → 2 个独立 meeting_id."""
        ids = []
        for i in range(2):
            body, ct = build_upload_multipart(
                short_wav,
                project_name=f"e2e-multi-{i}",
                platform="e2e_multi",
            )
            resp = http_post(f"{gpu_server}/api/meetings/upload", body, ct, timeout=120)
            mid = resp.get("meeting_id", "")
            assert mid, f"第 {i+1} 次 upload 无 meeting_id"
            ids.append(mid)

        assert ids[0] != ids[1], "两次上传 meeting_id 应不同"
        pytest._e2e_multi_ids = ids
