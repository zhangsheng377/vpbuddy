"""#1 经验蒸馏 API 测试 — v0.19.0

测试:
- GET  /api/experiences → 已确认经验列表
- GET  /api/experiences/candidates?meeting_id=X → 经验候选 (需 owner)
- POST /api/experiences/{id}/approve → 确认 (需 owner)
- POST /api/experiences/{id}/reject → 拒绝 (需 owner)
- 401 无认证
- 403 非 owner
"""
from __future__ import annotations

import json
import uuid

from .conftest import api, register_user


def test_get_experiences_empty(auth):
    """新用户没有已确认经验."""
    code, resp = api("/api/experiences", token=auth["token"])
    assert code == 200
    assert isinstance(resp["experiences"], list)


def test_get_candidates_no_auth_401():
    code, _ = api("/api/experiences/candidates?meeting_id=test")
    assert code == 401


def test_get_candidates_owner(meeting):
    """owner 可查看自己会议的经验候选 (即使为空)."""
    code, resp = api(
        f"/api/experiences/candidates?meeting_id={meeting['mid']}",
        token=meeting["token"],
    )
    assert code == 200
    assert "candidates" in resp
    assert "count" in resp


def test_get_candidates_cross_user_403(meeting):
    """非 owner 不能查看别人会议的经验候选."""
    tok2, _ = register_user("exp_cross")
    code, _ = api(
        f"/api/experiences/candidates?meeting_id={meeting['mid']}",
        token=tok2,
    )
    assert code == 403


def test_approve_no_auth_401():
    code, _ = api("/api/experiences/fake-id/approve", method="POST", body=b"{}")
    assert code == 401


def test_approve_missing_meeting_id(meeting):
    """缺少 meeting_id → 400."""
    code, resp = api(
        "/api/experiences/fake-id/approve",
        method="POST", body=b"{}", token=meeting["token"],
    )
    assert code == 400


def test_reject_no_auth_401():
    code, _ = api("/api/experiences/fake-id/reject", method="POST", body=b"{}")
    assert code == 401


def test_reject_missing_meeting_id(meeting):
    """缺少 meeting_id → 400."""
    code, resp = api(
        "/api/experiences/fake-id/reject",
        method="POST", body=b"{}", token=meeting["token"],
    )
    assert code == 400
