"""API 集成测试 conftest — 真 GPU server + Bearer auth.

跑法:
    RUN_E2E=1 pytest src/tests/apitest/ -v -s

所有测试默认 skip, 设 RUN_E2E=1 才跑 (跟 e2e/ 风格一致).
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
import uuid
from typing import Any

import pytest


GPU_SERVER_URL = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")


# ── Gate ──

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_E2E") != "1":
        skip = pytest.mark.skip(reason="API e2e opt-in: RUN_E2E=1")
        for item in items:
            item.add_marker(skip)


# ── Helpers ──

def api(url_suffix: str, method: str = "GET", body: bytes = None,
        token: str = "", ct: str = "application/json", timeout: float = 60.0) -> tuple[int, Any]:
    """Call GPU server API, return (status_code, decoded_json_or_text)."""
    full_url = f"{GPU_SERVER_URL}{url_suffix}"
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
            body_text = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return e.code, body_text


def register_user(prefix: str = "api") -> tuple[str, str]:
    """Register a test user, return (token, email)."""
    email = f"{prefix}_{uuid.uuid4().hex[:8]}@test.com"
    body = json.dumps({"email": email, "password": "t123456"}).encode()
    code, resp = api("/api/auth/register", method="POST", body=body)
    assert code == 200, f"register failed: {resp}"
    return resp["token"], email


@pytest.fixture(scope="session")
def auth():
    """Register a fresh test user once per session, return {'token': str, 'email': str}."""
    token, email = register_user("api")
    return {"token": token, "email": email}


@pytest.fixture
def auth_alt():
    """Second user for cross-user tests, registered once per test."""
    import time; time.sleep(1)
    token, email = register_user("ap2")
    return {"token": token, "email": email}


@pytest.fixture
def meeting(auth):
    """Create a meeting owned by auth user, return {'mid': str, 'token': str}."""
    mid = f"api_{uuid.uuid4().hex[:8]}"
    url = f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone"
    code, resp = api(url, method="POST", token=auth["token"])
    assert code == 200, f"meeting create failed: {resp}"
    return {"mid": mid, "token": auth["token"]}
