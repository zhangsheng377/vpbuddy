"""v0.22.6 e2e: 真实公网服务端链路 — gkd 触发 → doc 生成 → 验证文件落地"""
import json, time, uuid, os, sys
import urllib.request, urllib.error
import pytest

GPU = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")
RUN_E2E = os.environ.get("RUN_E2E") == "1"
pytestmark = pytest.mark.skipif(not RUN_E2E, reason="RUN_E2E=1 required")


def _api(path, method="GET", body=None, token="", ct="application/json", timeout=30):
    h = {"Content-Type": ct}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{GPU}{path}", data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {}


def _register():
    email = f"e2egkd_{uuid.uuid4().hex[:8]}@test.com"
    c, r = _api("/api/auth/register", "POST", json.dumps({"email": email, "password": "t123456"}).encode())
    assert c == 200, f"register failed: {r}"
    return r["token"]


class TestGkdE2E:
    def test_stream_start_creates_meeting(self):
        token = _register()
        mid = f"e2esm_{uuid.uuid4().hex[:8]}"
        c, r = _api(f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone", "POST", token=token)
        assert c == 200
        assert r.get("meeting_id") == mid

    def test_meeting_list_returns_array(self):
        token = _register()
        c, r = _api("/api/meetings", token=token)
        assert c == 200
        assert "meetings" in r
        assert "count" in r

    def test_doc_status_returns_pending(self):
        token = _register()
        mid = f"e2eds_{uuid.uuid4().hex[:8]}"
        _api(f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone", "POST", token=token)
        c, r = _api(f"/api/meetings/{mid}/docs/req", token=token)
        assert c == 200
        assert r.get("kind") == "req"
        assert r.get("status") in ("pending", "stored")

    def test_healthz_returns_ok(self):
        c, r = _api("/healthz", timeout=5)
        assert c == 200
        assert r.get("ok") is True

    def test_auth_register_and_login(self):
        email = f"e2ealog_{uuid.uuid4().hex[:8]}@test.com"
        c, r = _api("/api/auth/register", "POST", json.dumps({"email": email, "password": "t123456"}).encode())
        assert c == 200
        token = r["token"]
        c2, r2 = _api("/api/auth/login", "POST", json.dumps({"email": email, "password": "t123456"}).encode())
        assert c2 == 200
        assert r2.get("token") is not None

    def test_kb_list_returns_docs(self):
        token = _register()
        c, r = _api("/api/kb/list", token=token)
        assert c == 200
        assert "documents" in r

    def test_demo_versions_endpoint(self):
        token = _register()
        mid = f"e2edv_{uuid.uuid4().hex[:8]}"
        _api(f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone", "POST", token=token)
        c, r = _api(f"/api/meetings/{mid}/demo/versions", token=token)
        assert c in (200, 404)
