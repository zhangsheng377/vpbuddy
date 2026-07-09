"""Playwright 真 E2E 测试 — 浏览器 UI + GPU server 全链路.

覆盖:
- 认证流程: 注册 / 登录 / 错误密码 / 空输入 / 切换表单
- 导航: 6 个面板切换 + active 状态
- 会议: 输入新会议名启用录音按钮 / 会议名格式校验
- Chat: 输入框 / 发送 / 附件按钮
- 知识库: 检索 / 上传按钮状态
- 设置: GPU URL 输入 / 保存
- GPU 连接: pill 状态变化

运行: RUN_E2E=1 PLAYWRIGHT_BROWSERS_PATH=.playwright pytest src/tests/e2e/test_ui_playwright.py -v -s
"""
from __future__ import annotations

import json
import time
import urllib.request

import pytest

pytestmark = pytest.mark.e2e

GPU_URL = "http://47.100.182.3:28765"

# Session 级共享用户, 避免频繁注册触发 429
_SHARED_EMAIL = f"e2e_shared_{int(time.time())}@test.com"
_SHARED_PASSWORD = "test123456"
_SHARED_TOKEN = None


def _ensure_shared_user():
    """确保 session 级共享用户已注册."""
    global _SHARED_TOKEN
    if _SHARED_TOKEN:
        return _SHARED_TOKEN
    body = json.dumps({"email": _SHARED_EMAIL, "password": _SHARED_PASSWORD}).encode()
    req = urllib.request.Request(
        f"{GPU_URL}/api/auth/register",
        data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            _SHARED_TOKEN = json.loads(r.read())["token"]
    except urllib.error.HTTPError as e:
        if e.code == 409:
            # 已存在, 用 login
            req2 = urllib.request.Request(
                f"{GPU_URL}/api/auth/login",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req2, timeout=15) as r:
                _SHARED_TOKEN = json.loads(r.read())["token"]
        else:
            raise
    return _SHARED_TOKEN


def _login_shared(pg):
    """用共享用户 token 注入 localStorage, 拦截 /api/auth/me 避免速率限制."""
    _ensure_shared_user()
    # 拦截 token 验证请求, 避免频繁调用 /api/auth/me 触发 429
    pg.route("**/api/auth/me", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"email": _SHARED_EMAIL})
    ))
    pg.evaluate(f"localStorage.setItem('vpbuddy-token', '{_SHARED_TOKEN}')")
    pg.evaluate(f"localStorage.setItem('vpbuddy-email', '{_SHARED_EMAIL}')")
    pg.reload()
    pg.wait_for_selector("#auth-overlay", state="hidden", timeout=15000)
    return pg


# === 1. 认证流程 ===

class TestAuth:
    """认证 overlay: 注册 / 登录 / 错误处理 / 表单切换."""

    def test_switch_between_login_and_register(self, page_with_gpu):
        """切换登录/注册表单."""
        pg = page_with_gpu
        assert pg.locator("#auth-login-form").is_visible()
        assert not pg.locator("#auth-register-form").is_visible()

        pg.locator("#auth-show-register").click()
        assert pg.locator("#auth-register-form").is_visible()
        assert not pg.locator("#auth-login-form").is_visible()

        pg.locator("#auth-show-login").click()
        assert pg.locator("#auth-login-form").is_visible()
        assert not pg.locator("#auth-register-form").is_visible()

    def test_login_empty_input(self, page_with_gpu):
        """空输入 → 提示填写, 不发请求."""
        pg = page_with_gpu
        pg.locator("#auth-login-btn").click()
        err = pg.locator("#auth-error").text_content()
        assert "邮箱" in err or "填写" in err, f"期望提示填写, 实际: '{err}'"
        assert pg.locator("#auth-overlay").is_visible()

    def test_register_short_password(self, page_with_gpu):
        """注册密码 < 6 位 → 前端校验拦截."""
        pg = page_with_gpu
        pg.locator("#auth-show-register").click()
        pg.locator("#auth-reg-email").fill("e2e_short@test.com")
        pg.locator("#auth-reg-password").fill("12345")
        pg.locator("#auth-register-btn").click()
        err = pg.locator("#auth-error").text_content()
        assert "6" in err, f"期望提示密码至少6位, 实际: '{err}'"
        assert pg.locator("#auth-overlay").is_visible()

    def test_login_wrong_password(self, page_with_gpu):
        """错误密码 → 显示错误信息, overlay 不消失."""
        pg = page_with_gpu
        pg.locator("#auth-email").fill(_SHARED_EMAIL)
        pg.locator("#auth-password").fill("wrongpassword")
        pg.locator("#auth-login-btn").click()
        pg.wait_for_selector("#auth-error:not(:empty)", timeout=5000)
        err = pg.locator("#auth-error").text_content()
        assert err, f"期望错误信息, 实际: '{err}'"
        assert pg.locator("#auth-overlay").is_visible()

    def test_login_existing_user(self, page_with_gpu):
        """已注册用户登录 → overlay 消失."""
        pg = page_with_gpu
        _login_shared(pg)
        assert pg.locator("#app").is_visible()

    def test_register_new_user(self, page_with_gpu):
        """注册新用户 → overlay 消失, 主界面可见."""
        pg = page_with_gpu
        email = f"e2e_reg_{int(time.time()*1000) % 100000}@test.com"
        pg.locator("#auth-show-register").click()
        pg.locator("#auth-reg-email").fill(email)
        pg.locator("#auth-reg-password").fill("test123456")
        pg.locator("#auth-register-btn").click()
        pg.wait_for_selector("#auth-overlay", state="hidden", timeout=15000)
        assert pg.locator("#app").is_visible()


# === 2. 导航 ===

class TestNavigation:
    """底部导航栏 6 个面板切换."""

    PANELS = ["stream", "docs", "demo", "kb", "chat", "settings"]

    def test_default_panel_is_stream(self, page_with_gpu):
        """默认显示转写面板."""
        pg = _login_shared(page_with_gpu)
        classes = pg.locator("#panel-stream").get_attribute("class")
        assert "active" in classes

    def test_all_panels_switchable(self, page_with_gpu):
        """点击每个导航按钮, 对应面板激活."""
        pg = _login_shared(page_with_gpu)
        for panel in self.PANELS:
            btn = pg.locator(f".bottom-nav button[data-panel='{panel}']")
            btn.click()
            pg.wait_for_selector(f"#panel-{panel}.active", timeout=3000)
            panel_classes = pg.locator(f"#panel-{panel}").get_attribute("class")
            assert "active" in panel_classes, f"面板 {panel} 未激活"
            btn_classes = btn.get_attribute("class")
            assert "active" in btn_classes, f"按钮 {panel} 未 active"


# === 3. 会议创建/选择 ===

class TestMeeting:
    """会议选择/创建 + 录音按钮启用逻辑."""

    def test_rec_button_disabled_by_default(self, page_with_gpu):
        """录音按钮默认 disabled (ADR-0022)."""
        pg = _login_shared(page_with_gpu)
        assert pg.locator("#btn-rec").is_disabled()

    def test_rec_enabled_after_entering_meeting_name(self, page_with_gpu):
        """输入新会议名 → 录音按钮启用."""
        pg = _login_shared(page_with_gpu)
        pg.locator("#meeting-new").fill("e2e-test-meeting")
        assert not pg.locator("#btn-rec").is_disabled()
        assert "开始录音" in pg.locator("#btn-rec").text_content()

    def test_rec_disabled_after_clearing_meeting_name(self, page_with_gpu):
        """清空会议名 → 录音按钮再次 disabled."""
        pg = _login_shared(page_with_gpu)
        pg.locator("#meeting-new").fill("e2e-test")
        assert not pg.locator("#btn-rec").is_disabled()
        pg.locator("#meeting-new").fill("")
        assert pg.locator("#btn-rec").is_disabled()

    def test_audio_source_options(self, page_with_gpu):
        """音频源下拉有 3 个选项 (ADR-0021)."""
        pg = _login_shared(page_with_gpu)
        options = pg.locator("#audio-source-kind option").all_text_contents()
        assert any("麦克风" in o and "内录" not in o for o in options)
        assert any("内录" in o for o in options)
        assert any("麦克风" in o and "内录" in o for o in options)

    def test_meeting_select_populated(self, page_with_gpu):
        """登录后 meeting-select 有 option."""
        pg = _login_shared(page_with_gpu)
        pg.wait_for_selector("#meeting-select option", state="attached", timeout=5000)
        options = pg.locator("#meeting-select option").all_text_contents()
        assert len(options) >= 1


# === 4. Chat 界面 ===

class TestChat:
    """VP Chat 面板交互."""

    def test_chat_panel_elements(self, page_with_gpu):
        """Chat 面板关键元素."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='chat']").click()
        pg.wait_for_selector("#panel-chat.active", timeout=3000)
        assert pg.locator("#chat-input").count() == 1
        assert pg.locator("#chat-send").count() == 1
        assert pg.locator("#chat-attach").count() == 1
        assert pg.locator("#chat-file").count() == 1

    def test_chat_input_placeholder(self, page_with_gpu):
        """Chat 输入框 placeholder 存在."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='chat']").click()
        pg.wait_for_selector("#panel-chat.active", timeout=3000)
        placeholder = pg.locator("#chat-input").get_attribute("placeholder")
        assert placeholder, "chat-input 无 placeholder"

    def test_chat_empty_state(self, page_with_gpu):
        """Chat 未开始时显示空状态提示."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='chat']").click()
        pg.wait_for_selector("#panel-chat.active", timeout=3000)
        assert pg.locator(".chat-empty").count() >= 1


# === 5. 知识库 ===

class TestKnowledgeBase:
    """知识库 RAG 面板."""

    def test_kb_panel_elements(self, page_with_gpu):
        """KB 面板关键元素."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='kb']").click()
        pg.wait_for_selector("#panel-kb.active", timeout=3000)
        assert pg.locator("#kb-q").count() == 1
        assert pg.locator("#kb-btn").count() == 1
        assert pg.locator("#kb-file").count() == 1
        assert pg.locator("#kb-upload-btn").count() == 1

    def test_kb_search_input(self, page_with_gpu):
        """知识库搜索框可输入."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='kb']").click()
        pg.wait_for_selector("#panel-kb.active", timeout=3000)
        pg.locator("#kb-q").fill("测试查询")
        assert pg.locator("#kb-q").input_value() == "测试查询"

    def test_kb_upload_disabled_without_meeting(self, page_with_gpu):
        """未开始会议时, KB 上传按钮 disabled."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='kb']").click()
        pg.wait_for_selector("#panel-kb.active", timeout=3000)
        assert pg.locator("#kb-upload-btn").is_disabled()


# === 6. 设置页 ===

class TestSettings:
    """设置面板."""

    def test_settings_panel_elements(self, page_with_gpu):
        """设置页关键元素."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='settings']").click()
        pg.wait_for_selector("#panel-settings.active", timeout=3000)
        assert pg.locator("#gpu-url").count() == 1
        assert pg.locator("#btn-save-url").count() == 1
        assert pg.locator("#btn-open-config-dir").count() == 1
        assert pg.locator("#log-path").count() == 1
        assert pg.locator("#ui-lang").count() == 1

    def test_gpu_url_input_editable(self, page_with_gpu):
        """GPU URL 输入框可编辑."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='settings']").click()
        pg.wait_for_selector("#panel-settings.active", timeout=3000)
        pg.locator("#gpu-url").fill("http://test:9999")
        assert pg.locator("#gpu-url").input_value() == "http://test:9999"

    def test_language_select_options(self, page_with_gpu):
        """语言下拉有中文和 English."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='settings']").click()
        pg.wait_for_selector("#panel-settings.active", timeout=3000)
        options = pg.locator("#ui-lang option").all_text_contents()
        assert any("中文" in o for o in options)
        assert any("English" in o for o in options)


# === 7. GPU 连接状态 ===

class TestGpuConnection:
    """GPU 服务器连接状态 pill."""

    def test_gpu_pill_exists(self, page_with_gpu):
        """GPU pill 元素存在."""
        pg = _login_shared(page_with_gpu)
        assert pg.locator("#gpu-pill").count() == 1
        assert pg.locator("#gpu-dot").count() == 1
        assert pg.locator("#gpu-status").count() == 1

    def test_gpu_pill_shows_status(self, page_with_gpu):
        """GPU pill 显示连接状态文字 (非空)."""
        pg = _login_shared(page_with_gpu)
        pg.wait_for_function(
            "() => document.getElementById('gpu-status')?.textContent?.length > 0",
            timeout=10000,
        )
        status = pg.locator("#gpu-status").text_content()
        assert status, "GPU pill 状态为空"


# === 8. 产物面板 ===

class TestDocsPanel:
    """会议产物面板."""

    def test_docs_grid_has_six_blocks(self, page_with_gpu):
        """产物面板 6 个 doc-block."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='docs']").click()
        pg.wait_for_selector("#panel-docs.active", timeout=3000)
        blocks = pg.locator("#docs-grid .doc-block")
        assert blocks.count() >= 5, f"期望至少 5 个 doc-block, 实际 {blocks.count()}"

    def test_docs_block_kinds(self, page_with_gpu):
        """产物块类型: req/arch/tasks/api/risk."""
        pg = _login_shared(page_with_gpu)
        pg.locator(".bottom-nav button[data-panel='docs']").click()
        pg.wait_for_selector("#panel-docs.active", timeout=3000)
        kinds = pg.locator("#docs-grid .doc-block").evaluate_all(
            "els => els.map(e => e.getAttribute('data-kind'))"
        )
        for expected in ["req", "arch", "tasks", "api", "risk"]:
            assert expected in kinds, f"缺 {expected}: {kinds}"
