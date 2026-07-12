"""e2e — 客户端全面用户行为测试 (直接模拟用户操作, 连接 GPU 真服务器).

跑法: RUN_E2E=1 pytest src/tests/e2e/test_client_user_flows.py -v -m e2e

设计理念:
1. 每个测试模拟一个真实用户操作路径 (不是测试一个个孤立函数)
2. 依赖 Playwright headless Chrome + vite preview (同份 bundle)
3. GPU 分离: 不依赖 GPU 的 UI 测试用 `page` fixture; 需要 GPU 的用 `page_with_gpu` fixture
4. Tauri stub 替换 Rust 端, fetch API 直连 GPU 真服务器

覆盖的用户场景:
1. 设置页: GPU URL 输入框 + 保存 + 优先级提示
2. 音频源切换: 麦克风/内录/双轨
3. Tab 导航: 6 个面板切换
4. 录音全生命周期: 开始 → recording 状态 → 停止 → idle
5. 自动上传复选框
6. Chat: 文件附件按钮 + 输入框
7. 6 文档空状态
8. 演示面板: 版本选择器 + iframe
9. 协作疑问: 折叠/展开 + 提问 UI
10. KB 搜索 UI: 输入框 + 检索按钮
---
需要 GPU 服务器:
11. 会议下拉从 GPU 加载
12. GPU 连接状态 pill
13. KB 上传按钮
"""
from __future__ import annotations

import time

import pytest


pytestmark = pytest.mark.e2e


# ============================================================
# 场景 1: 设置页
# ============================================================

def test_settings_panel_gpu_url(page):
    """用户路径: 点设置页签 → GPU URL 输入框 → 保存按钮 → 优先级说明."""
    # 切到设置面板
    _nav_to_page(page, "settings")
    page.wait_for_timeout(300)

    # GPU URL 输入框
    url_input = page.locator("#gpu-url")
    assert url_input.count() == 1
    assert url_input.is_visible()

    # 保存按钮
    save_btn = page.locator("#btn-save-url")
    assert save_btn.count() == 1
    assert save_btn.is_visible()

    # 优先级说明
    note = page.locator(".settings-note")
    assert note.count() >= 1

    # 设置面板可见
    assert page.locator("#panel-settings").is_visible()
    assert page.locator("h2").filter(has_text="设置").count() == 1


def test_settings_log_path(page):
    """用户路径: 设置页 → 日志路径区域."""
    _nav_to_page(page, "settings")
    labels = page.locator(".settings-card label").all_text_contents()
    log_labels = [l for l in labels if "日志" in l]
    assert len(log_labels) >= 1, f"缺日志设置: {labels}"


# ============================================================
# 场景 2: 音频源切换
# ============================================================

def test_audio_source_kind_dropdown(page):
    """用户路径: 下拉 → 3 个 option 可选."""
    sel = page.locator("#audio-source-kind")
    assert sel.count() == 1

    options = sel.locator("option").all_text_contents()
    assert len(options) == 3, f"期望 3 音频源, 实际 {len(options)}"

    for val in ["microphone", "loopback", "both"]:
        sel.select_option(val)
        assert sel.input_value() == val


def test_audio_devices_list(page):
    """用户路径: 音频设备列表自动填充 (从 Tauri stub)."""
    device_sel = page.locator("#audio-device")
    assert device_sel.count() == 1

    page.wait_for_function(
        """() => {
            const sel = document.getElementById('audio-device');
            return sel && sel.options.length >= 2 && sel.options[1].value !== '';
        }""",
        timeout=3000,
    )
    opts = device_sel.locator("option").all_text_contents()
    assert len(opts) >= 2, f"设备列表不足: {opts}"


# ============================================================
# 场景 3: Tab 导航
# ============================================================

def test_tab_navigation(page):
    """用户路径: 依次点击每个 tab → 面板正确激活."""
    for panel_id in ["stream", "docs", "demo", "kb", "chat", "settings"]:
        _nav_to_page(page, panel_id)
        panel = page.locator(f"#panel-{panel_id}")
        assert panel.count() == 1
        assert panel.is_visible()


# ============================================================
# 场景 4: 录音全生命周期
# ============================================================

def test_recording_lifecycle(page):
    """用户路径: 输入会议名 → 录音 → recording 状态 → 停止 → idle."""
    # 输入会议名
    page.locator("#meeting-new").fill("e2e_lifecycle_test")
    page.locator("#meeting-new").dispatch_event("input")

    # 点开始录音
    page.locator("#btn-rec").click()
    page.wait_for_timeout(500)

    # 检查 recording 状态
    page.wait_for_function(
        """() => document.getElementById('btn-rec')?.dataset.state === 'recording'""",
        timeout=5000,
    )
    btn_text = (page.locator("#btn-rec").text_content() or "").strip()
    assert "停止录音" in btn_text or "recording" in btn_text, f"按钮文本: {btn_text!r}"

    # 停止录音
    page.locator("#btn-rec").click()
    page.wait_for_timeout(500)

    # 检查 idle 状态
    page.wait_for_function(
        """() => document.getElementById('btn-rec')?.dataset.state === 'idle'""",
        timeout=5000,
    )
    btn_text = (page.locator("#btn-rec").text_content() or "").strip()
    assert "开始录音" in btn_text or "idle" in btn_text, f"按钮文本: {btn_text!r}"


def test_rec_btn_toggle_text(page):
    """用户路径: 录音中按钮变'停止录音'."""
    _start_recording(page, "e2e_toggle_test")
    btn = page.locator("#btn-rec")
    page.wait_for_function(
        """() => document.getElementById('btn-rec').dataset.state === 'recording'""",
        timeout=5000,
    )
    assert "停止录音" in (btn.text_content() or "")

    # 结束会议按钮可见
    end_btn = page.locator("#btn-end-meeting")
    assert end_btn.is_visible()

    _stop_recording(page)

    # 停止后结束按钮隐藏
    end_btn = page.locator("#btn-end-meeting")
    assert not end_btn.is_visible()


def test_rec_btn_disabled_without_meeting(page):
    """用户路径: 没会议时录音按钮 disabled."""
    btn = page.locator("#btn-rec")
    page.locator("#meeting-new").fill("")
    page.locator("#meeting-new").dispatch_event("input")
    page.locator("#meeting-select").evaluate(
        "el => { el.value = ''; el.dispatchEvent(new Event('change')); }"
    )
    assert btn.is_disabled()
    title = btn.get_attribute("title") or ""
    assert "选择" in title or "输入" in title


# ============================================================
# 场景 5: 自动上传复选框
# ============================================================

def test_auto_upload_checkbox(page):
    """用户路径: 默认 checked → 取消 → 重新勾选."""
    cb = page.locator("#auto-upload")
    assert cb.count() == 1
    assert cb.is_checked(), "默认应勾选"

    cb.click()
    assert not cb.is_checked()

    cb.click()
    assert cb.is_checked()


# ============================================================
# 场景 6: Chat 附件
# ============================================================

def test_chat_attach_button(page):
    """用户路径: 📎 按钮 + 隐藏 file input."""
    _nav_to_page(page, "chat")

    attach_btn = page.locator("#chat-attach")
    assert attach_btn.count() == 1
    assert attach_btn.is_visible()

    file_input = page.locator("#chat-file")
    assert file_input.count() == 1
    accept = file_input.get_attribute("accept") or ""
    for ext in [".txt", ".md", ".pdf", "image/png", "image/jpeg"]:
        assert ext in accept, f"accept 缺 {ext}: {accept}"


def test_chat_input_and_send(page):
    """用户路径: 输入框 + 发送按钮."""
    _nav_to_page(page, "chat")

    chat_input = page.locator("#chat-input")
    assert chat_input.count() == 1
    assert chat_input.is_visible()

    send_btn = page.locator("#chat-send")
    assert send_btn.count() == 1

    chat_input.fill("测试消息")
    assert chat_input.input_value() == "测试消息"


# ============================================================
# 场景 7: 6 文档空状态
# ============================================================

def test_docs_grid_empty_state(page):
    """用户路径: 文档面板 → 6 块初始 placeholder."""
    _nav_to_page(page, "docs")

    doc_blocks = page.locator(".doc-block")
    assert doc_blocks.count() >= 5

    for i in range(doc_blocks.count()):
        body = doc_blocks.nth(i).locator(".doc-body")
        text = body.text_content() or ""
        assert "暂无" in text or "点击" in text or "抽到" in text, \
            f"block {i} 缺 placeholder: {text[:30]}"


# ============================================================
# 场景 8: 演示面板
# ============================================================

def test_demo_panel(page):
    """用户路径: 版本选择器 + iframe 存在."""
    _nav_to_page(page, "demo")

    assert page.locator("#demo-version-select").count() == 1
    assert page.locator("#demo-iframe").count() == 1


# ============================================================
# 场景 9: 协作疑问面板
# ============================================================

def test_collab_panel(page):
    """用户路径: 展开 → 提问输入 → 选择 section → 折叠."""
    _nav_to_page(page, "chat")

    collab = page.locator("#collab-panel")
    assert collab.count() == 1

    # 默认折叠
    assert not collab.get_attribute("open")

    # 展开
    collab_summary = collab.locator("summary").first
    collab_summary.click()
    page.wait_for_timeout(300)

    # 提问输入框可见
    q_input = page.locator("#collab-q-input")
    assert q_input.is_visible()

    # section 选择器含 6 个
    section_sel = page.locator("#collab-section")
    options = section_sel.locator("option").all_text_contents()
    assert len(options) >= 5

    # 提问按钮
    ask_btn = page.locator("#collab-ask-btn")
    assert ask_btn.is_visible()


# ============================================================
# 场景 10: KB 搜索 UI
# ============================================================

def test_kb_search_ui(page):
    """用户路径: KB 面板 → 搜索输入 + 检索按钮 + 结果区域."""
    _nav_to_page(page, "kb")

    assert page.locator("#kb-q").count() == 1
    assert page.locator("#kb-btn").count() == 1
    assert page.locator("#kb-results").count() == 1


def test_kb_upload_ui(page):
    """用户路径: KB 上传按钮 + file input."""
    _nav_to_page(page, "kb")

    kb_file = page.locator("#kb-file")
    assert kb_file.count() == 1
    accept = kb_file.get_attribute("accept") or ""
    for ext in [".txt", ".md", ".pdf"]:
        assert ext in accept, f"accept 缺 {ext}"

    kb_upload = page.locator("#kb-upload-btn")
    assert kb_upload.count() == 1


# ============================================================
# 场景 11: 录音状态 pill (不依赖 GPU)
# ============================================================

def test_recording_pill(page):
    """用户路径: 页面加载 → pill 显示'录音就绪'."""
    assert page.locator("#rec-pill").count() == 1
    assert "录音就绪" in (page.locator("#rec-status").text_content() or ""), \
        f"pill 应含'录音就绪': {page.locator('#rec-status').text_content()!r}"


# ============================================================
# 场景 12+: 需要 GPU 服务器的测试
# ============================================================

@pytest.mark.skip(reason="GPU 公网不稳定, 手动运行")
def test_meeting_select_from_gpu(page, gpu_server):
    """用户路径: 下拉从 GPU 加载会议列表 → 选中 → 按钮可点."""
    import json
    import urllib.request

    with urllib.request.urlopen(f"{gpu_server}/api/meetings", timeout=5) as r:
        body = json.loads(r.read())

    if not body["meetings"]:
        pytest.skip("GPU 没会议")

    page.wait_for_function(
        """() => {
            const sel = document.getElementById('meeting-select');
            return sel && sel.options.length >= 2;
        }""",
        timeout=5000,
    )

    first = body["meetings"][0]["meeting_id"]
    sel = page.locator("#meeting-select")
    sel.select_option(first)
    assert sel.input_value() == first

    btn = page.locator("#btn-rec")
    assert not btn.is_disabled()


@pytest.mark.skip(reason="GPU 公网不稳定, 手动运行")
def test_gpu_pill_connected(page, gpu_server):
    """用户路径: GPU 状态 pill 显示已连接."""
    pill = page.locator("#gpu-pill")
    assert pill.count() == 1

    page.wait_for_function(
        """() => {
            const s = document.getElementById('gpu-status');
            return s && (s.textContent || '').includes('已连接');
        }""",
        timeout=10000,
    )
    status = page.locator("#gpu-status").text_content() or ""
    assert "已连接" in status


# ============================================================
# 辅助函数
# ============================================================

def _nav_to_page(page, panel_id: str) -> None:
    """模拟用户切 tab."""
    tab = page.locator(f"button[data-panel=\"{panel_id}\"]")
    if tab.count() == 1:
        tab.click()
        page.wait_for_timeout(200)
    else:
        page.evaluate(
            """(pid) => {
                const t = document.querySelector(`button[data-panel="${pid}"]`);
                if (t) t.click();
                else {
                    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
                    const target = document.getElementById('panel-' + pid);
                    if (target) target.classList.add('active');
                }
            }""",
            panel_id,
        )
        page.wait_for_timeout(200)


def _start_recording(page, meeting_id: str) -> None:
    """模拟: 输入会议名 → 点开始录音."""
    page.locator("#meeting-new").fill(meeting_id)
    page.locator("#meeting-new").dispatch_event("input")
    page.wait_for_function(
        """() => !document.getElementById('btn-rec').disabled""",
        timeout=3000,
    )
    page.locator("#btn-rec").click()


def _stop_recording(page) -> None:
    """模拟: 点停止录音."""
    page.locator("#btn-rec").click()


def _assert_recording_state(page, recording: bool) -> None:
    """断言录音状态."""
    expected_state = "recording" if recording else "idle"
    page.wait_for_function(
        f"""() => document.getElementById('btn-rec').dataset.state === '{expected_state}'""",
        timeout=5000,
    )
    btn_text = (page.locator("#btn-rec").text_content() or "").strip()
    expected_text = "停止录音" if recording else "开始录音"
    assert expected_text in btn_text, \
        f"按钮文本期望'{expected_text}', 实际: {btn_text!r}"

    rec_status = page.locator("#rec-status").text_content() or ""
    if recording:
        assert "录音" in rec_status, f"pill 应含'录音': {rec_status!r}"
    else:
        assert "未录音" in rec_status, f"pill 应为'未录音': {rec_status!r}"
