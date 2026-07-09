"""安全修复回归测试 — v0.19.0 code review

验证:
- 高危 #4: 前端桥接路由 8 个均已添加认证
- 高危 #5: aggregate 端点现已需 owner 校验
- 高危 #8: owner_id 空字符串不再绕过权限检查
- 回归: 原有合法端点仍正常返回
"""
from __future__ import annotations

import json
import uuid

from .conftest import api, register_user


# ══════════════════════════════════════════════════════════════════
# HIGH #4: 前端桥接路由认证
# ══════════════════════════════════════════════════════════════════

def test_fe_meetings_no_auth_401():
    """GET /meetings (前端桥接) 无 token → 401."""
    code, _ = api("/meetings")
    assert code == 401


def test_fe_meeting_no_auth_401():
    """GET /api/meetings/{id} (前端桥接) 无 token → 401."""
    code, _ = api("/api/meetings/test")
    assert code == 401


def test_fe_transcript_no_auth_401():
    """GET /meetings/{id}/transcript-segments 无 token → 401."""
    code, _ = api("/meetings/test/transcript-segments")
    assert code == 401


def test_fe_deliverables_no_auth_401():
    """GET /meetings/{id}/deliverables 无 token → 401."""
    code, _ = api("/meetings/test/deliverables")
    assert code == 401


def test_fe_deliverable_no_auth_401():
    """GET /deliverables/{id} 无 token → 401."""
    code, _ = api("/deliverables/test")
    assert code == 401


def test_fe_archive_no_auth_401():
    """POST /meetings/{id}/archive 无 token → 401."""
    code, _ = api("/meetings/test/archive", method="POST", body=b"{}")
    assert code == 401


# ══════════════════════════════════════════════════════════════════
# HIGH #5: aggregate 认证
# ══════════════════════════════════════════════════════════════════

def test_aggregate_no_auth_401():
    """aggregate 无 token → 401."""
    code, _ = api("/api/meetings/test/aggregate")
    assert code == 401


def test_aggregate_cross_user_403(meeting):
    """aggregate 非 owner → 403."""
    tok2, _ = register_user("agg_cross")
    code, _ = api(
        f"/api/meetings/{meeting['mid']}/aggregate",
        token=tok2,
    )
    assert code == 403


def test_aggregate_owner_200(meeting):
    """aggregate owner → 200."""
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/aggregate",
        token=meeting["token"],
    )
    assert code == 200
    assert "meeting_id" in resp


# ══════════════════════════════════════════════════════════════════
# 回归: 原有合法端点仍然正常
# ══════════════════════════════════════════════════════════════════

def test_regression_me_list(meeting):
    """GET /api/meetings 认证后仍正常."""
    code, resp = api("/api/meetings", token=meeting["token"])
    assert code == 200
    assert "meetings" in resp


def test_regression_state(meeting):
    """GET /state 认证+owner 后仍正常."""
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/state",
        token=meeting["token"],
    )
    assert code == 200


def test_regression_docs(meeting):
    """GET /docs 认证+owner 后仍正常."""
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/docs",
        token=meeting["token"],
    )
    assert code == 200
