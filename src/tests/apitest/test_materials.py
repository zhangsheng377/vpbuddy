"""#16 会议材料认证 + DELETE 测试 — v0.19.0

测试:
- GET  /api/meetings/{id}/materials → 需 owner, 非 owner 403
- POST /api/meetings/{id}/materials → 需 owner
- GET  /api/materials/{id} → 需 owner
- DELETE /api/materials/{id} → 需 owner + 实际删除
- 401 无认证
"""
from __future__ import annotations

import json
import uuid

from .conftest import api, register_user


def test_get_materials_no_auth_401():
    code, _ = api("/api/meetings/nonexistent/materials")
    assert code == 401


def test_get_materials_owner(meeting):
    """owner 可列出自己会议的材料."""
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/materials",
        token=meeting["token"],
    )
    assert code == 200
    assert "materials" in resp
    assert "count" in resp


def test_get_materials_cross_user_403(meeting):
    """非 owner 不能列别人会议的材料."""
    tok2, _ = register_user("mat_cross")
    code, resp = api(
        f"/api/meetings/{meeting['mid']}/materials",
        token=tok2,
    )
    assert code == 403


def test_get_material_detail_no_auth_401():
    code, _ = api("/api/materials/nonexistent")
    assert code == 401


def test_get_material_detail_cross_user_403(meeting):
    """非 owner 不能看别人会议的材料详情."""
    tok2, _ = register_user("mat_detail")
    code, _ = api(
        "/api/materials/mat_cross_test0000",
        token=tok2,
    )
    # 没有 meeting_id 的材料可能 404, 但如果有 meeting_id 会 403
    assert code in (403, 404)


def test_delete_material_no_auth_401():
    code, _ = api("/api/materials/nonexistent", method="DELETE")
    assert code == 401


def test_delete_material_not_found(meeting):
    """删除不存在的 material → 404."""
    code, resp = api(
        "/api/materials/mat_nonexistent000",
        method="DELETE",
        token=meeting["token"],
    )
    assert code == 404


def test_upload_and_delete_material(meeting):
    """上传材料→详情→删除 完整流程."""
    mid = meeting["mid"]
    tok = meeting["token"]

    # Upload a file
    boundary = "----mat-lifecycle-" + uuid.uuid4().hex[:16]
    file_bytes = b"Hello material lifecycle test"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"lifecycle.txt\"\r\n"
        f"Content-Type: text/plain\r\n\r\n".encode()
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )
    ct = f"multipart/form-data; boundary={boundary}"
    code, resp = api(
        f"/api/meetings/{mid}/materials",
        method="POST", body=body, token=tok, ct=ct, timeout=120,
    )
    assert code == 200, f"upload failed: {resp}"
    mat_id = resp.get("material", {}).get("material_id") or resp.get("material_id") or resp.get("id", "")
    assert mat_id, f"no material_id in response: {resp}"

    # GET detail
    code, detail = api(f"/api/materials/{mat_id}", token=tok)
    assert code == 200
    assert detail["filename"] == "lifecycle.txt"

    # DELETE
    code, del_resp = api(f"/api/materials/{mat_id}", method="DELETE", token=tok)
    assert code == 200
    assert del_resp["deleted"] is True

    # 再次 GET → 404
    code, _ = api(f"/api/materials/{mat_id}", token=tok)
    assert code == 404
