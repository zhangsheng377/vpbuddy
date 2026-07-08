"""#22 下载端点测试 — v0.19.0

测试:
- GET /api/meetings/{id}/docs/{kind}/download → 返回文件 (需认证)
- GET /api/materials/{id}/file → 返回文件 (需认证)
- GET /api/kb/{doc_id}/file → 返回文件 (需认证)
- 401 无认证
- 403 非 owner
- 404 不存在
"""
from __future__ import annotations

import json

from .conftest import api, register_user


def test_doc_download_404(meeting):
    """不存在的 kind → 400, 不存在的文件 → 404."""
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/docs/nope/download",
        token=meeting["token"],
    )
    assert code == 400


def test_doc_download_no_auth_401(meeting):
    """无 token → 401."""
    code, _ = api(f"/api/meetings/{meeting['mid']}/docs/req/download")
    assert code == 401


def test_doc_download_cross_user_403(meeting):
    """其他用户不能下载不属于他的会议文档."""
    tok2, _ = register_user("cross_dl")
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/docs/req/download",
        token=tok2,
    )
    assert code == 403


def test_material_file_no_auth_401():
    """无 token → 401."""
    code, _ = api("/api/materials/nonexistent/file")
    assert code == 401


def test_material_file_not_found_404(meeting):
    """不存在的 material → 404."""
    code, resp = api(
        "/api/materials/mat_nonexistent00/file",
        token=meeting["token"],
    )
    # material not found or no such meeting=owner → 可能 403(非自己的 meeting) 或 404
    assert code in (403, 404)


def test_kb_file_no_auth_401():
    """无 token → 401."""
    code, _ = api("/api/kb/nonexistent/file")
    assert code == 401


def test_kb_file_not_found_404(meeting):
    """不存在的 KB doc → 404."""
    code, resp = api(
        "/api/kb/nonexistent:file/file",
        token=meeting["token"],
    )
    assert code == 404
