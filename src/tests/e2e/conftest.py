"""e2e 测试 conftest — 本机 vite preview + GPU 真 server + Playwright headless Chrome.

跑法 (本机, 假设 GPU server 在 192.168.10.63:8765 已起):
    RUN_E2E=1 pytest src/tests/e2e/ -v -m e2e

设计 (铁律 5: 真实部署驱动, server 在 GPU):
- 本机: 起 `vite preview` serve `vpbuddy-client/dist/` (用户可见的同份 bundle)
- 本机: Playwright headless chromium 操作 vite UI
- 远程 (GPU 192.168.10.63:8765): 真 server 跑 (不是 pytest fixture, 是部署环境)
- inject `window.__TAURI__` 让 main.js 跳过 Rust 端, 但所有 fetch(/api/...) 真发到 GPU server

为什么这样拆分:
- "本机起 server" 被用户禁止, server 不在本机
- 客户端 (Tauri WebView UI) 本机必有, vite preview = 同份 bundle, headless 可操作
- Rust 端 (Tauri binary) 替代: window.__TAURI__ stub
- GPU 端: server 在跑, 这是 deployment 路径, 验真 server 行为

为什么默认 skip:
- e2e 要 GPU server + Playwright 装好, CI 默认 release test 不跑这么重
- opt-in RUN_E2E=1 才跑, 避免 20 分钟 build 跑 + 撞死 GPU 上别人正在开的 server
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest


# === 配置 ===
VPBUDDY_CLIENT_DIR = Path("/home/zsd/vpbuddy/vpbuddy-client")
DIST_DIR = VPBUDDY_CLIENT_DIR / "dist"
GPU_SERVER_URL = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")
E2E_VITE_PORT = 4173


def _e2e_enabled() -> bool:
    return os.environ.get("RUN_E2E") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """RUN_E2E != 1 时自动 skip 所有 @pytest.mark.e2e."""
    if _e2e_enabled():
        return
    skip_marker = pytest.mark.skip(reason="e2e opt-in: RUN_E2E=1 + GPU server 跑通")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_marker)


def _port_free(port: int) -> bool:
    """简单端口占用检查 (避免 vite preview 撞死)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _http_ready(url: str, timeout: float = 5.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def gpu_server() -> str:
    """验证 GPU server 在跑, 不行就 skip (铁律 5: 必须真 server).

    Returns: GPU server base URL.
    """
    if not _http_ready(GPU_SERVER_URL):
        pytest.skip(f"GPU server 不通: {GPU_SERVER_URL} (部署路径不通, e2e 跳过)")
    return GPU_SERVER_URL


@pytest.fixture(scope="session")
def vite_preview_url() -> Iterator[str]:
    """起 vite preview (serve dist/) on port 4173, session 级 fixture.

    Yields: http://localhost:4173
    """
    if not DIST_DIR.exists():
        pytest.skip(f"vpbuddy-client/dist/ 不存在, 先 `cd vpbuddy-client && npm run build`")

    if not _port_free(E2E_VITE_PORT):
        pytest.skip(f"port {E2E_VITE_PORT} 被占, e2e 起 vite preview 失败")

    # 跑 vite preview 后台, 用进程组 setsid 隔离 (finalizer 杀整个 group)
    import os
    proc = subprocess.Popen(
        ["npx", "--yes", "vite", "preview", "--port", str(E2E_VITE_PORT), "--strictPort"],
        cwd=str(VPBUDDY_CLIENT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        preexec_fn=os.setsid,  # 新进程组, 保证 finalizer SIGTERM 能传到子进程
    )
    url = f"http://localhost:{E2E_VITE_PORT}"

    # 等待 vite preview ready
    for _ in range(40):
        if _http_ready(url):
            break
        time.sleep(0.25)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except OSError:
            pass
        pytest.skip(f"vite preview 启动失败 ({url})")

    try:
        yield url
    finally:
        # SIGTERM 整个进程组 → 5s 内不死就 SIGKILL
        try:
            os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (OSError, ProcessLookupError):
                pass


# === Playwright fixtures ===

# Tauri stub: Tauri 2.6+ ESM bundle 在浏览器内部用 `window.__TAURI_INTERNALS__.invoke`
# 跟 `window.__TAURI_INTERNALS__.transformCallback` (不是 `window.__TAURI__.core.invoke`).
# 这 stub 给这俩注入, 让 main.js (Tauri client UI) 不需要 Tauri binary 也能跑.
TAURI_STUB_SCRIPT = r"""
(function() {
  let nextCallbackId = 1;
  const callbacks = new Map();

  function invoke(cmd, args, options) {
    console.log('[TAURI STUB invoke]', cmd, args);
    switch (cmd) {
      case 'start_capture':
        // 返回 meetingId 让前端 UI 真切换到 recording 态
        return Promise.resolve(args && args.meetingId ? args.meetingId : 'stub-meeting');
      case 'stop_capture':
      case 'plugin:event|unlisten':
        return Promise.resolve();
      case 'plugin:event|listen':
        // 返回假 eventId 让 listen() resolve, UI 不会 hang
        return Promise.resolve(0);
      case 'list_audio_devices':
        return Promise.resolve([{ name: 'stub-mic', is_default: true, is_loopback: false }]);
      case 'set_gpu_url':
      case 'get_gpu_url':
        return Promise.resolve(window.__VP_E2E_GPU_URL__ || '');
      case 'kb_search':
        // 真 fetch GPU server 的 kb_search endpoint (GET /api/kb/search?q=...&meeting_id=...)
        const url = new URL((window.__VP_E2E_GPU_URL__ || '') + '/api/kb/search');
        url.searchParams.set('q', String(args && args.query || ''));
        // meetingId 是 stub 注入的全局变量 (主流程里 start_capture 拿到)
        const mid = args && args.meetingId || window.__VP_E2E_MEETING_ID__;
        if (mid) url.searchParams.set('meeting_id', mid);
        return fetch(url.toString(), { method: 'GET' }).then(r => r.json());
      case 'post_meeting_chat':
      case 'fetch_meeting_chat_history':
        return Promise.resolve({ ok: true, messages: [] });
      case 'get_log_path_cmd':
      case 'open_log_dir_cmd':
      case 'open_config_dir_cmd':
        return Promise.resolve('/tmp/stub');
      default:
        return Promise.resolve(null);
    }
  }

  function transformCallback(cb, once) {
    const id = nextCallbackId++;
    callbacks.set(id, cb);
    return id;
  }

  function unregisterCallback(id) {
    callbacks.delete(id);
  }

  window.__TAURI_INTERNALS__ = {
    invoke,
    transformCallback,
    unregisterCallback,
    plugins: { path: { sep: '/', delimiter: ':' } },
  };

  // 兼容 main.js 的 fallback 路径 (window.__TAURI__.core.invoke)
  window.__TAURI__ = {
    core: { invoke },
    event: { listen: () => Promise.resolve(() => {}) },
  };
})();
"""


@pytest.fixture(scope="session")
def playwright_browser():
    """Playwright chromium browser session.

    第一次 import playwright 会自动装 chromium binary (本机已经预先装好
    ~/.cache/ms-playwright/chromium-1208). 失败也 skip, 不让 CI 挂.
    """
    if not _e2e_enabled():
        pytest.skip("RUN_E2E 未设")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright 未装, `pip install playwright` 后 `playwright install chromium`")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(playwright_browser, vite_preview_url):
    """单 page 上下文, 注入 Tauri stub. 不依赖 GPU server (UI-only 测试用)."""
    ctx = playwright_browser.new_context()
    ctx.add_init_script(
        f"window.__VP_E2E_GPU_URL__ = {GPU_SERVER_URL!r};\n{TAURI_STUB_SCRIPT}"
    )
    pg = ctx.new_page()
    # 收集浏览器 console 方便调试 (pytest -s 时能看到)
    pg.on("console", lambda msg: print(f"[BROWSER {msg.type}] {msg.text}"))
    pg.on("pageerror", lambda err: print(f"[PAGEERROR] {err}"))
    pg.goto(vite_preview_url, wait_until="domcontentloaded")
    yield pg
    pg.close()
    ctx.close()


@pytest.fixture
def page_with_gpu(playwright_browser, vite_preview_url, gpu_server):
    """单 page 上下文, 注入 Tauri stub + 真 GPU URL. 依赖 GPU server."""
    ctx = playwright_browser.new_context()
    ctx.add_init_script(
        f"window.__VP_E2E_GPU_URL__ = {gpu_server!r};\n{TAURI_STUB_SCRIPT}"
    )
    pg = ctx.new_page()
    pg.on("console", lambda msg: print(f"[BROWSER {msg.type}] {msg.text}"))
    pg.on("pageerror", lambda err: print(f"[PAGEERROR] {err}"))
    pg.on("response", lambda r: print(f"[FETCH {r.status}] {r.url[:120]}") if "47.100.182.3" in r.url else None)
    pg.on("requestfailed", lambda r: print(f"[FETCH FAILED] {r.url[:120]} {r.failure}"))
    pg.goto(vite_preview_url, wait_until="domcontentloaded")
    yield pg
    pg.close()
    ctx.close()


# === fixture helpers ===

@pytest.fixture
def fixtures_dir() -> Path:
    """src/tests/fixtures/ 的绝对路径."""
    return Path(__file__).parent.parent / "fixtures"
