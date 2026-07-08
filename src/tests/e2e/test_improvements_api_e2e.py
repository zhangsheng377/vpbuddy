"""改进功能 E2E API 测试 — 直接调用 GPU server 验证改进效果.

覆盖改进:
- P0: 速率限制中间件
- P0: 未认证端点补全 (3个)
- P1: 统一异常处理器 (错误格式统一)
- P2: 输入长度校验 (chat 消息长度, 文件大小)

运行: RUN_E2E=1 pytest src/tests/e2e/test_improvements_api_e2e.py -v
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
import urllib.request
import urllib.error

import httpx
import pytest

pytestmark = pytest.mark.e2e

GPU_URL = "http://47.100.182.3:28765"


def _api(url_suffix: str, method: str = "GET", body: bytes = None, token: str = "",
         ct: str = "application/json", timeout: float = 30.0) -> tuple[int, dict | str]:
    """调用 GPU server API."""
    full_url = f"{GPU_URL}{url_suffix}"
    headers = {"Content-Type": ct}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(full_url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body_text)
            except json.JSONDecodeError:
                return e.code, body_text
        except Exception:
            return e.code, ""


def _register_user() -> tuple[str, str]:
    """注册测试用户."""
    email = f"e2e_impr_{uuid.uuid4().hex[:8]}@test.com"
    body = json.dumps({"email": email, "password": "t123456"}).encode()
    code, resp = _api("/api/auth/register", method="POST", body=body)
    if code == 429:
        time.sleep(61)
        code, resp = _api("/api/auth/register", method="POST", body=body)
    assert code == 200, f"注册失败: {resp}"
    return resp["token"], email


def _create_meeting(token: str) -> str:
    """创建会议."""
    mid = f"e2e_impr_{uuid.uuid4().hex[:8]}"
    code, _ = _api(f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone",
                   method="POST", token=token)
    assert code == 200, f"创建会议失败"
    return mid


class TestRateLimit:
    """验证速率限制中间件."""

    def test_api_rate_limit(self):
        """快速连续请求应触发速率限制 (429)."""
        token, _ = _register_user()
        
        async def _run():
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            async with httpx.AsyncClient(timeout=10, limits=limits) as client:
                tasks = [
                    client.get(f"{GPU_URL}/api/status", headers={"Authorization": f"Bearer {token}"})
                    for _ in range(200)
                ]
                responses = await asyncio.gather(*tasks)
                return [r.status_code for r in responses]
        
        codes = asyncio.run(_run())
        assert 429 in codes, f"未触发速率限制, 响应码: {set(codes)}, 总数: {len(codes)}"

    def test_auth_rate_limit_stricter(self):
        """认证端点应有更严格的限制."""
        codes = []
        for i in range(15):
            email = f"auth_rate_{uuid.uuid4().hex[:8]}@test.com"
            body = json.dumps({"email": email, "password": "t123456"}).encode()
            code, _ = _api("/api/auth/register", method="POST", body=body)
            codes.append(code)
            if code == 429:
                break
        assert 429 in codes, f"认证端点未触发速率限制, 响应码: {set(codes)}"


class TestAuthRequired:
    """验证未认证端点补全."""

    def test_check_id_requires_auth(self):
        """GET /api/meetings/check_id 应要求认证."""
        code, resp = _api("/api/meetings/check_id?id=test123")
        assert code == 401, f"check_id 未认证应返回 401, 实际 {code}: {resp}"

    def test_demo_versions_requires_auth(self):
        """GET /api/meetings/{id}/demo/versions 应要求认证."""
        code, resp = _api("/api/meetings/test123/demo/versions")
        assert code == 401, f"demo_versions 未认证应返回 401, 实际 {code}: {resp}"

    def test_device_status_requires_auth(self):
        """GET /api/client/device-status 应要求认证."""
        code, resp = _api("/api/client/device-status")
        assert code == 401, f"device-status 未认证应返回 401, 实际 {code}: {resp}"


class TestUnifiedErrorFormat:
    """验证统一异常处理器."""

    def test_http_exception_format(self):
        """HTTPException 应返回统一的 {"error": str, "status": int} 格式."""
        token, _ = _register_user()
        mid = _create_meeting(token)
        code, resp = _api(f"/api/meetings/{mid}/chat", method="POST", token=token,
                          body=json.dumps({}).encode())
        assert code == 400, f"预期 400, 实际 {code}"
        assert isinstance(resp, dict), f"响应应为 dict, 实际 {type(resp)}"
        assert "error" in resp, f"响应缺少 error 字段: {resp}"
        assert "status" in resp, f"响应缺少 status 字段: {resp}"

    def test_unhandled_exception_no_traceback(self):
        """未捕获异常不应泄露 traceback."""
        token, _ = _register_user()
        mid = _create_meeting(token)
        code, resp = _api(f"/api/meetings/{mid}/upload_audio", method="POST", token=token,
                          body=b"invalid-data", ct="text/plain")
        resp_str = str(resp)
        assert "traceback" not in resp_str.lower(), f"响应不应包含 traceback: {resp_str}"
        assert "stack" not in resp_str.lower(), f"响应不应包含 stack: {resp_str}"
        assert "line " not in resp_str.lower(), f"响应不应包含代码行号: {resp_str}"


class TestInputValidation:
    """验证输入长度校验."""

    def test_chat_message_length_limit(self):
        """超长 chat 消息应被拒绝 (400)."""
        token, _ = _register_user()
        mid = _create_meeting(token)
        long_msg = "a" * 30000
        body = json.dumps({"message": long_msg}).encode()
        code, resp = _api(f"/api/meetings/{mid}/chat", method="POST", token=token, body=body)
        assert code == 400, f"超长消息应返回 400, 实际 {code}: {resp}"

    def test_file_size_limit(self):
        """超大上传文件应被拒绝 (413)."""
        token, _ = _register_user()
        mid = _create_meeting(token)
        huge_data = b"x" * (101 * 1024 * 1024)
        body, ct = _build_multipart(huge_data, "test-project")
        code, resp = _api(f"/api/meetings/{mid}/upload_audio", method="POST", token=token,
                          body=body, ct=ct, timeout=60)
        assert code == 413, f"超大文件应返回 413, 实际 {code}: {resp}"


def _build_multipart(wav_bytes: bytes, project_name: str) -> tuple[bytes, str]:
    """构建 upload 端点的 multipart/form-data."""
    boundary = b"----e2e-upload-boundary"
    parts = []

    def add(name: str, value: str | bytes):
        parts.append(b"--" + boundary + b"\r\n")
        if isinstance(value, bytes):
            parts.append(f'Content-Disposition: form-data; name="{name}"; filename="audio.wav"\r\n'.encode())
            parts.append(b"Content-Type: audio/wav\r\n\r\n")
            parts.append(value)
        else:
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            parts.append(value.encode())
        parts.append(b"\r\n")

    add("project_name", project_name)
    add("platform", "e2e")
    add("audio", wav_bytes)
    parts.append(b"--" + boundary + b"--\r\n")
    return b"".join(parts), f'multipart/form-data; boundary={boundary.decode()}'