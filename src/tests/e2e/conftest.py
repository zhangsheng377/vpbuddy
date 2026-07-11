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

import json
import math
import os
from functools import lru_cache
import random
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest


# === 配置 ===
_IS_WIN = sys.platform.startswith("win")
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent.parent
VPBUDDY_CLIENT_DIR = _REPO_ROOT / "vpbuddy-client"
DIST_DIR = VPBUDDY_CLIENT_DIR / "dist"
GPU_SERVER_URL = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")
E2E_VITE_PORT = int(os.environ.get("VP_E2E_PORT", "4173"))
SR = 16000


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


def _http_ready(url: str, timeout: float = 5.0, method: str = "GET",
                data: bytes | None = None, content_type: str | None = None,
                expected_status: int = 200) -> bool:
    try:
        req = urllib.request.Request(url, method=method, data=data)
        if content_type:
            req.add_header("Content-Type", content_type)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == expected_status
    except urllib.error.HTTPError as e:
        return e.code == expected_status
    except Exception:
        return False


def http_post(url: str, data: bytes | None = None, content_type: str = "application/octet-stream",
              timeout: float = 300.0, token: str = "") -> dict:
    """POST JSON/二进制 → 解析 JSON 响应."""
    req = urllib.request.Request(url, data=data, method="POST")
    if content_type:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_get(url: str, timeout: float = 10.0, token: str = "") -> dict:
    """GET → 解析 JSON 响应."""
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_get_text(url: str, timeout: float = 10.0, token: str = "") -> str:
    """GET → 返回纯文本."""
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def generate_wav(dur_sec: float = 8.0) -> bytes:
    """生成 16kHz mono WAV 模拟语音信号."""
    ns = int(SR * dur_sec)
    samples = []
    for i in range(ns):
        t = i / SR
        val = (
            0.3 * math.sin(2 * math.pi * 300 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 4 * t))
            + 0.2 * math.sin(2 * math.pi * 800 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 3 * t))
            + 0.15 * math.sin(2 * math.pi * 2000 * t) * (0.5 + 0.5 * math.sin(2 * math.pi * 5 * t))
            + 0.1 * math.sin(2 * math.pi * (200 + 100 * math.sin(2 * math.pi * 0.5 * t)) * t)
            + 0.01 * random.gauss(0, 1)
        )
        val = max(-0.95, min(0.95, val))
        samples.append(int(val * 32767))

    buf = bytearray()
    nch, bits, bps = 1, 16, 2
    ba = nch * bps
    ds = len(samples) * ba
    buf.extend(b"RIFF")
    buf.extend(struct.pack("<I", 36 + ds))
    buf.extend(b"WAVE")
    buf.extend(b"fmt ")
    buf.extend(struct.pack("<I", 16))
    buf.extend(struct.pack("<H", 1))
    buf.extend(struct.pack("<H", nch))
    buf.extend(struct.pack("<I", SR))
    buf.extend(struct.pack("<I", SR * ba))
    buf.extend(struct.pack("<H", ba))
    buf.extend(struct.pack("<H", bits))
    buf.extend(b"data")
    buf.extend(struct.pack("<I", ds))
    for s in samples:
        buf.extend(struct.pack("<h", s))
    return bytes(buf)


def build_upload_multipart(wav_bytes: bytes, project_name: str = "e2e-test",
                           platform: str = "e2e") -> tuple[bytes, str]:
    """构建 multipart/form-data 用于 upload 端点."""
    boundary = b"----e2e-upload-boundary"
    parts = []

    def _add(name: str, value: str | bytes):
        parts.append(b"--" + boundary + b"\r\n")
        if isinstance(value, bytes):
            parts.append(f'Content-Disposition: form-data; name="{name}"; filename="audio.wav"\r\n'.encode())
            parts.append(b"Content-Type: audio/wav\r\n\r\n")
            parts.append(value)
        else:
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            parts.append(value.encode())
        parts.append(b"\r\n")

    _add("project_name", project_name)
    _add("platform", platform)
    _add("audio", wav_bytes)

    parts.append(b"--" + boundary + b"--\r\n")
    body = b"".join(parts)
    ct = f'multipart/form-data; boundary={boundary.decode()}'
    return body, ct


def poll_docs(gpu_url: str, meeting_id: str, timeout: float = 300.0,
              poll_interval: float = 15.0, min_kinds: list[str] | None = None) -> dict:
    """轮询 doc endpoint 直到所有文档有内容或超时.

    Returns:
        文档列表 (每个含 kind, status, doc_size, content_preview)
    Raises:
        TimeoutError: 超时仍有文档未就绪
    """
    if min_kinds is None:
        min_kinds = ["req", "arch", "tasks", "api", "risk", "demo"]
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            resp = http_get(f"{gpu_url}/api/meetings/{meeting_id}", timeout=10)
        except Exception:
            time.sleep(poll_interval)
            continue

        docs = resp.get("docs", [])
        used_kinds = {d["kind"]: d for d in docs}
        missing = [k for k in min_kinds if k not in used_kinds]
        empty = [k for k, d in used_kinds.items()
                 if d.get("doc_size", 0) == 0 and d.get("status") != "empty"]

        if not missing:
            return docs  # 全部就绪

        if time.time() + poll_interval > deadline:
            break
        time.sleep(poll_interval)

    # 超时: 返回部分结果 + 抛异常
    raise TimeoutError(
        f"文档就绪超时 ({timeout}s): "
        f"missing={[k for k in min_kinds if k not in used_kinds]}, "
        f"empty={[k for k, d in used_kinds.items() if d.get('doc_size', 0) == 0]}, "
        f"got={list(used_kinds.keys())}"
    )


# === session-scoped fixtures ===

@pytest.fixture(scope="session")
def gpu_server() -> str:
    """验证 GPU server 在跑, 不行就 skip (铁律 5: 必须真 server).

    Returns: GPU server base URL.
    """
    if not _http_ready(f"{GPU_SERVER_URL}/api/auth/login", method="POST", data=b'{"email":"test@test.com","password":"wrong"}', content_type="application/json", expected_status=401):
        pytest.skip(f"GPU server 不通: {GPU_SERVER_URL} (部署路径不通, e2e 跳过)")
    return GPU_SERVER_URL


@lru_cache(maxsize=1)
def _e2e_token() -> str:
    """注册/登录 E2E 测试用户, 返回 Bearer token (session 级缓存)."""
    import json as _json
    email = "e2e_auto@vpbuddy.test"
    password = "e2e_test_123456"
    try:
        data = _json.dumps({"email": email, "password": password}).encode()
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request(f"{GPU_SERVER_URL}/api/auth/login", data=data, method="POST",
                                    headers={"Content-Type": "application/json"}),
            timeout=10,
        ).read())
        return resp["token"]
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # 注册
            data = _json.dumps({"email": email, "password": password}).encode()
            resp = json.loads(urllib.request.urlopen(
                urllib.request.Request(f"{GPU_SERVER_URL}/api/auth/register", data=data, method="POST",
                                        headers={"Content-Type": "application/json"}),
                timeout=10,
            ).read())
            return resp["token"]
        raise


@pytest.fixture(scope="session")
def synth_wav() -> bytes:
    """生成 30s 合成语音用于 e2e 上传测试.(session 级缓存)."""
    return generate_wav(30.0)


@pytest.fixture(scope="session")
def short_wav() -> bytes:
    """8s 短音频 (用于边界测试)."""
    return generate_wav(8.0)


@pytest.fixture(scope="session")
def vite_preview_url() -> Iterator[str]:
    """起 vite preview (serve dist/) on port 4173, session 级 fixture.

    Yields: http://localhost:4173
    """
    if not DIST_DIR.exists():
        pytest.skip(f"vpbuddy-client/dist/ 不存在, 先 `cd vpbuddy-client && npm run build`")

    if not _port_free(E2E_VITE_PORT):
        pytest.skip(f"port {E2E_VITE_PORT} 被占, e2e 起 vite preview 失败")

    npx_cmd = "npx.cmd" if _IS_WIN else "npx"
    popen_kwargs = {}
    if not _IS_WIN:
        popen_kwargs["preexec_fn"] = os.setsid

    proc = subprocess.Popen(
        [npx_cmd, "--yes", "vite", "preview", "--port", str(E2E_VITE_PORT), "--strictPort"],
        cwd=str(VPBUDDY_CLIENT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        **popen_kwargs,
    )
    url = f"http://localhost:{E2E_VITE_PORT}"

    for _ in range(40):
        if _http_ready(url):
            break
        time.sleep(0.25)
    else:
        try:
            if _IS_WIN:
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), 9)
        except OSError:
            pass
        pytest.skip(f"vite preview 启动失败 ({url})")

    try:
        yield url
    finally:
        try:
            if _IS_WIN:
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), 15)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if _IS_WIN:
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), 9)
            except (OSError, ProcessLookupError):
                pass


# === Tauri stub ===

TAURI_STUB_SCRIPT = r"""
(function() {
  let nextCallbackId = 1;
  const callbacks = new Map();

  function invoke(cmd, args, options) {
    console.log('[TAURI STUB invoke]', cmd, args);
    switch (cmd) {
      case 'start_capture':
        return Promise.resolve(args && args.meetingId ? args.meetingId : 'stub-meeting');
      case 'stop_capture':
      case 'plugin:event|unlisten':
        return Promise.resolve();
      case 'plugin:event|listen':
        return Promise.resolve(0);
      case 'list_audio_devices':
        return Promise.resolve([{ name: 'stub-mic', is_default: true, is_loopback: false }]);
      case 'set_gpu_url':
      case 'get_gpu_url':
        return Promise.resolve(window.__VP_E2E_GPU_URL__ || '');
      case 'kb_search':
        const url = new URL((window.__VP_E2E_GPU_URL__ || '') + '/api/kb/search');
        url.searchParams.set('q', String(args && args.query || ''));
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

  window.__TAURI__ = {
    core: { invoke },
    event: { listen: () => Promise.resolve(() => {}) },
  };
})();
"""


@pytest.fixture(scope="session")
def playwright_browser():
    """Playwright chromium browser session."""
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
    """单 page 上下文, 注入 Tauri stub + E2E token. 不依赖 GPU server (UI-only 测试用)."""
    token = _e2e_token()
    ctx = playwright_browser.new_context()
    ctx.add_init_script(
        f"window.__VP_E2E_GPU_URL__ = {GPU_SERVER_URL!r};\n"
        f"window.__VP_E2E_TOKEN__ = {token!r};\n"
        f"localStorage.setItem('vpbuddy-token', {token!r});\n"
        f"localStorage.setItem('vpbuddy-email', 'e2e_auto@vpbuddy.test');\n"
        f"{TAURI_STUB_SCRIPT}"
    )
    pg = ctx.new_page()
    pg.on("console", lambda msg: print(f"[BROWSER {msg.type}] {msg.text}"))
    pg.on("pageerror", lambda err: print(f"[PAGEERROR] {err}"))
    pg.goto(vite_preview_url, wait_until="domcontentloaded")
    yield pg
    pg.close()
    ctx.close()


@pytest.fixture(scope="session")
def e2e_token() -> str:
    """E2E 测试用户 Bearer token (session 级缓存)."""
    return _e2e_token()

@pytest.fixture
def page_with_gpu(playwright_browser, vite_preview_url, gpu_server):
    """单 page 上下文, 注入 Tauri stub + E2E token + 真 GPU URL. 依赖 GPU server."""
    token = _e2e_token()
    ctx = playwright_browser.new_context()
    ctx.add_init_script(
        f"window.__VP_E2E_GPU_URL__ = {gpu_server!r};\n"
        f"window.__VP_E2E_TOKEN__ = {token!r};\n"
        f"localStorage.setItem('vpbuddy-token', {token!r});\n"
        f"localStorage.setItem('vpbuddy-email', 'e2e_auto@vpbuddy.test');\n"
        f"{TAURI_STUB_SCRIPT}"
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
