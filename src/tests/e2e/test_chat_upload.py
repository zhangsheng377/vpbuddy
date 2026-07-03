"""e2e — Chat 上传 (用户需求 #2: chat 页面加文件/图片上传按钮 + 端到端链路).

跑法: RUN_E2E=1 pytest src/tests/e2e/test_chat_upload.py -v -m e2e

测什么:
- UI: 📎 按钮存在, file input accept 包含 .txt/.md/.pdf + 4 image 格式
- UI: 选文件后 chip 渲染 (file name + size)
- 端到端: 走 multipart fetch POST /api/meetings/{id}/chat (kb_api.handle_chat_upload)
  - .txt/.md/.pdf → 入 KB (Chroma 灌库, meeting_id metadata 隔离)
  - 图片 → 转 base64 data URI, 不入 KB
  - 响应: 每文件 status, KB 灌库 doc_id, 图片 data_uri_length
- 端到端: 客户端 chat-send 触发后, 附件真到 server 真灌库

不测什么:
- chat 文字对话 (走 invoke 路径, 跟 LLM 强相关, e2e stub 不测)
- 主动提问 UI (单独 test_agent_proactive.py)
- 已答 chat 历史渲染 (跟当前测试不冲突, 但单独测)
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pytest


pytestmark = pytest.mark.e2e


# === Helpers (跟 test_kb_isolation 共享) ===

def _http_post_multipart(url, fields, timeout: float = 10):
    """手搓 multipart POST. fields: [(name, filename_or_value, content, content_type), ...]"""
    boundary = "----e2e-chat-boundary-54321"
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
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def _http_get_json(url, timeout: float = 5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read())


# === UI Tests ===

class TestChatUploadUI:
    """只验 UI 元素存在 + 接受类型正确, 不触发真上传."""

    def test_chat_panel_has_attach_button(self, page):
        """切到 chat panel, 看见 📎 按钮 + hidden file input + 文本框 + 发送按钮."""
        # 切到 chat panel
        page.locator('.bottom-nav button[data-panel="chat"]').click()
        # wait 一下让 panel.active 切完
        page.wait_for_function(
            "() => document.getElementById('panel-chat').classList.contains('active')",
            timeout=3000,
        )

        # 关键元素
        assert page.locator("#chat-attach").count() == 1
        assert page.locator("#chat-attach").is_visible()
        # title 提示
        title = page.locator("#chat-attach").get_attribute("title") or ""
        assert "上传" in title or "attach" in title.lower() or "文件" in title

        # 隐藏的 file input
        file_input = page.locator("#chat-file")
        assert file_input.count() == 1
        accept = file_input.get_attribute("accept") or ""
        # 用户原话: 上传文件/图片
        assert ".txt" in accept
        assert ".md" in accept
        assert ".pdf" in accept
        assert "image/png" in accept
        assert "image/jpeg" in accept

        # 文本框 + 发送
        assert page.locator("#chat-input").count() == 1
        assert page.locator("#chat-send").count() == 1
        # 多个文件支持
        assert "multiple" in (file_input.get_attribute("multiple") or "").lower() or \
               file_input.get_attribute("multiple") is not None

    def test_chat_attach_button_triggers_file_input(self, page):
        """📎 按钮 click → 触发 hidden file input click (用 evaluate 验, 因为 file picker 在 headless 不会真打开)."""
        # 切到 chat panel (chat-attach 在 panel-chat 内, 默认 display:none)
        page.locator('.bottom-nav button[data-panel="chat"]').click()
        page.wait_for_function(
            "() => document.getElementById('panel-chat').classList.contains('active')",
            timeout=3000,
        )
        page.wait_for_selector("#chat-attach", state="visible")

        # inject spy: 拦截 #chat-file.click() 验证
        page.evaluate("""
            () => {
                const fi = document.getElementById('chat-file');
                window.__E2E_FILE_INPUT_CLICKED__ = false;
                fi.addEventListener('click', () => {
                    window.__E2E_FILE_INPUT_CLICKED__ = true;
                }, { capture: true });
            }
        """)

        # 点 📎
        page.locator("#chat-attach").click()
        # 验证 file input 真被 click
        clicked = page.evaluate("() => window.__E2E_FILE_INPUT_CLICKED__")
        assert clicked is True, "📎 按钮没触发 file input click"

    def test_chat_input_and_send_present(self, page):
        """chat panel: 文本输入 + 发送按钮 (用户能打字问 Hermes)."""
        page.locator('.bottom-nav button[data-panel="chat"]').click()
        page.wait_for_function(
            "() => document.getElementById('panel-chat').classList.contains('active')",
            timeout=3000,
        )
        assert page.locator("#chat-input").is_visible()
        assert page.locator("#chat-send").is_visible()

        # 文本框可输入
        page.locator("#chat-input").fill("e2e 测试问题")
        val = page.locator("#chat-input").input_value()
        assert val == "e2e 测试问题"


# === End-to-End (UI + 真 GPU server) ===

class TestChatUploadE2E:
    """真 GPU server (kb_api.handle_chat_upload) 链路."""

    def test_upload_text_file_via_ui_then_search_meeting(self, page, gpu_server):
        """端到端: UI 点 📎 (实际我们用 evaluate 模拟, 因为 headless 不能弹 file picker) →
        set chatAttachments + click chat-send → 真 multipart POST 到 GPU server →
        KB 真灌库 → server 检索能找到.

        因为 headless chromium 不能弹 native file picker, 我们用 evaluate 注入
        FileList 到 chatAttachments 全局 var, 然后调 sendChat.
        """
        ts = int(time.time_ns())
        meeting_id = f"e2e_chat_{ts}"

        # 1. 选会议 (前 e2e 验证过的路径)
        page.locator("#meeting-new").fill(meeting_id)
        page.locator("#meeting-new").dispatch_event("input")
        page.locator("#btn-rec").click()
        page.wait_for_function(
            "() => document.getElementById('btn-rec').dataset.state === 'recording'",
            timeout=5000,
        )

        # 2. 切到 chat panel
        page.locator('.bottom-nav button[data-panel="chat"]').click()
        page.wait_for_function(
            "() => document.getElementById('panel-chat').classList.contains('active')",
            timeout=3000,
        )

        # 3. 填文本问题
        page.locator("#chat-input").fill("帮我总结这个文件")

        # 4. 注入 mock File 到 chatAttachments (绕过 file picker)
        # 我们用 setInputFiles 直接给 file input 喂文件, 这是 Playwright 真正的 e2e 路径
        # 先准备一个 fixture 文件
        import tempfile, os
        tmp_dir = tempfile.mkdtemp(prefix="e2e_chat_")
        text_file = os.path.join(tmp_dir, "report.md")
        with open(text_file, "w", encoding="utf-8") as f:
            f.write(
                f"# 季度报告 (e2e chat upload {ts})\n\n"
                f"Q4 营收 2.5 亿, 战略方向: GPU RAG 优化.\n\n"
                f"(本文件通过 chat 上传, e2e 验证: meeting={meeting_id})\n"
            )

        # 5. 触发 file input 设置 (Playwright API, 真走 file input change)
        page.locator("#chat-file").set_input_files(text_file)
        # 等 chip 渲染
        page.wait_for_function(
            "() => document.querySelectorAll('.chat-attach-chip').length >= 1",
            timeout=3000,
        )
        chip_count = page.locator(".chat-attach-chip").count()
        assert chip_count >= 1, f"chip 应至少 1 个, 实际 {chip_count}"

        # 6. 点发送 — 真 multipart fetch 到 GPU server
        page.locator("#chat-send").click()
        # 等 chat status 变 "Hermes 已回复" 或 "Chat 失败" (LLM stub 走 invoke 但没返回,
        # 所以会 fail; 关键是 server 真收到 multipart 并入库)
        # 实际: file 入库 + LLM 调用 invoke stub 返 { ok: true, messages: [] }
        # 所以 status 应是 "Hermes 已回复" 或 "Chat 失败: ..."
        # 关键验证: KB 灌库成功
        # 等待 5s 让 server 处理 (含 Chroma 灌库)
        time.sleep(3)

        # 7. 端到端验证: GPU server 检索 meeting_id 看到我灌的内容
        search_url = f"{gpu_server}/api/kb/search?q={urllib.parse.quote('revenue')}&meeting_id={urllib.parse.quote(meeting_id)}"
        status, body = _http_get_json(search_url)
        # 隔离验证: 真检索能命中
        assert status == 200, f"search 失败: {status} {body}"
        if "error" in body:
            pytest.fail(f"search 报错: {body['error']}")
        # 至少 1 条命中
        assert len(body.get("results", [])) >= 1, f"KB 检索应至少 1 条: {body}"
        # 命中 ID 必须属 meeting_id
        for r in body["results"]:
            assert r["id"].startswith(f"{meeting_id}:"), \
                f"chat 上传后 KB 命中应属 meeting {meeting_id}: {r['id']}"
        # 命中内容应含 "营收 2.5 亿"
        hit_docs = [r.get("document", "") for r in body["results"]]
        assert any("2.5 亿" in d for d in hit_docs), \
            f"chat 上传文件应含 '2.5 亿' 数字: {hit_docs}"

        # 8. UI 端: 清理验证 (chat-attachments 区为空)
        # 因为 server 路径可能成功, 也可能 invoke stub 出错, 这里不强求 attachments 清空
        # 但: 至少验证 chat-status 文案被更新过 (证明 sendChat 跑了真路径)
        chat_status = page.locator("#chat-status").text_content()
        # '上传 1 个附件 + 问 Hermes...' 或 'Hermes 已回复' 或 'Chat 失败: ...'
        assert any(s in (chat_status or "") for s in (
            "上传 1 个附件", "Hermes 已回复", "Chat 失败", "Hermes 正在思考"
        )), f"chat_status 应被 sendChat 更新, 实际: {chat_status!r}"

    def test_upload_rejected_file_type_via_server(self, gpu_server):
        """端到端: server 端拒不支持的文件类型 (.exe).

        这是 handle_chat_upload 防线测试, 跟 UI 无关, 但能验证 server 真在过滤.
        """
        ts = int(time.time_ns())
        meeting_id = f"e2e_chat_bad_{ts}"

        # 拼一个 .exe 文件 (跟 chat-attach UI 的 accept 列表无关, 直接 server 测)
        status, body = _http_post_multipart(
            f"{gpu_server}/api/meetings/{urllib.parse.quote(meeting_id)}/chat",
            [
                ("text", "test question", None, None),
                ("files", "evil.exe", b"MZ\x90\x00\x03\x00", "application/octet-stream"),
            ],
        )
        # 应 200 (handle_chat_upload 返 200 + 每文件 status, 不是 4xx)
        # 但 files[i].status 应是 "rejected", 带 error
        assert status == 200, f"应 200: {status} {body}"
        # 真实 _handle_chat multipart 路径: files 在 body["upload"]["files"] 里
        upload = body.get("upload", {})
        files = upload.get("files", [])
        rejected = [f for f in files if f.get("filename") == "evil.exe"]
        assert len(rejected) == 1, f".exe 应被拒: {files}"
        assert rejected[0].get("status") == "rejected", \
            f".exe status 应 'rejected', 实际: {rejected[0]}"
        # error 信息应说明只支持某几种
        assert "只支持" in (rejected[0].get("error") or ""), \
            f"error 应说 '只支持', 实际: {rejected[0].get('error')}"

    def test_upload_text_and_image_together(self, gpu_server):
        """端到端: 文本 + 图片一起传, 文本入 KB, 图片转 base64 (kb_api 不入 Chroma).

        handle_chat_upload 行为: 文件类 (.txt/.md/.pdf) → 入库; 图片 → data URI 列表.
        """
        ts = int(time.time_ns())
        meeting_id = f"e2e_chat_mix_{ts}"

        # 1x1 pixel PNG (最小合法 PNG, 67 字节)
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000d49444154789c636000000000050001a5f645400000000049454e44ae426082"
        )
        assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n"), "fixture PNG 头错"

        status, body = _http_post_multipart(
            f"{gpu_server}/api/meetings/{urllib.parse.quote(meeting_id)}/chat",
            [
                ("text", "look at this image", None, None),
                ("files", "notes.md", "# notes (e2e mix test)\n\nThis is a text attachment for KB ingest.".encode("utf-8"), "text/markdown"),
                ("files", "pixel.png", png_bytes, "image/png"),
            ],
        )
        assert status == 200, f"应 200: {status} {body}"

        # 真实 _handle_chat multipart 路径: files 在 body["upload"]["files"] 里
        upload = body.get("upload", {})
        files = upload.get("files", [])
        files_by_name = {f["filename"]: f for f in files}

        # 顶层 image_count / kb_doc_ids 也都在 upload 块里
        image_count = upload.get("image_count", 0)
        kb_doc_ids = upload.get("kb_doc_ids", [])

        notes = files_by_name.get("notes.md")
        assert notes is not None, f"notes.md 缺: {files_by_name}"
        # handle_chat_upload 返 status "kb-stored" (跟 handle_kb_upload 不一样)
        assert notes.get("status") == "kb-stored", \
            f"notes.md 应 'kb-stored', 实际: {notes}"
        # 灌库应有 doc_id
        assert notes.get("doc_id", "").startswith(f"{meeting_id}:"), \
            f"doc_id 应属 {meeting_id}, 实际: {notes.get('doc_id')}"

        png = files_by_name.get("pixel.png")
        assert png is not None, f"pixel.png 缺: {files_by_name}"
        assert png.get("status") == "image", \
            f"png 应走 image 路径, 实际: {png}"
        # data_uri_length 应有 (有 data URI)
        assert png.get("data_uri_length", 0) > 0, \
            f"png 应有 data URI, 实际: {png}"

        # 顶层 image_count 应 = 1
        assert image_count == 1, \
            f"image_count 应 1, 实际: {image_count}"
        # 顶层 kb_doc_ids 应含 notes 的 doc_id
        assert notes["doc_id"] in kb_doc_ids, \
            f"kb_doc_ids 应含 notes 的 doc_id: {kb_doc_ids}"
