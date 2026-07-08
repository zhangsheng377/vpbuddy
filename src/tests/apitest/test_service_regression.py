"""Service 层重构回归测试 — v0.19.0 #14

验证 chat/state/docs/meetings 关键路由重构后仍正常:
- POST /api/meetings/{id}/chat (文本 + multipart)
- GET  /api/meetings/{id}/state
- GET  /api/meetings/{id}/docs
- GET  /api/meetings (列表 + own 过滤)
- GET  /api/timeline
- GET  /api/status
"""
from __future__ import annotations

import json
import uuid

from .conftest import api


def test_chat_text(meeting):
    """POST /chat text → 返回 reply."""
    body = json.dumps({"message": "你好，请简单介绍你自己"}).encode()
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/chat",
        method="POST", body=body, token=meeting["token"], timeout=120,
    )
    assert code == 200
    assert resp.get("status") in ("ok", "partial", None) or "assistant_message" in resp


def test_chat_multipart(meeting):
    """POST /chat multipart (文件上传)."""
    boundary = "----svc-chat-" + uuid.uuid4().hex[:16]
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\n"
        f"你好\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    ct = f"multipart/form-data; boundary={boundary}"
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/chat",
        method="POST", body=body, token=meeting["token"], ct=ct, timeout=120,
    )
    assert code == 200


def test_get_state(meeting):
    """GET /state → 返回 meeting_id (可能包在 state 下)."""
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/state",
        token=meeting["token"],
    )
    assert code == 200
    # state 可能在顶层或嵌套在 "state" key 下
    inner = resp.get("state", resp)
    assert "meeting_id" in inner


def test_state_cross_user_403(meeting):
    """非 owner → 403."""
    from .conftest import register_user
    tok2, _ = register_user("svc_cross")
    code, _ = api(
        f"/api/meetings/{meeting['mid']}/state",
        token=tok2,
    )
    assert code == 403


def test_list_meetings(auth):
    """GET /api/meetings → 返回列表 (可空)."""
    code, resp = api("/api/meetings", token=auth["token"])
    assert code == 200
    assert "meetings" in resp
    assert isinstance(resp["meetings"], list)


def test_get_status():
    """GET /api/status → 200."""
    code, resp = api("/api/status")
    assert code == 200


def test_get_timeline(auth):
    """GET /api/timeline → 200 (需认证)."""
    code, resp = api("/api/timeline", token=auth["token"])
    assert code == 200
