"""#20 KB 上传 scope/label/meeting_callable 测试 — v0.19.0

测试:
- KB 上传 → 返回 scope/labels/meeting_callable
- 默认值验证
- KB 列表返回文档含 metadata
"""
from __future__ import annotations

import json
import uuid

from .conftest import api


def _kb_upload(token: str, text: str, extra_fields: dict = None) -> dict:
    """上传一段文本到 KB, 返回响应."""
    boundary = "----kb-test-" + uuid.uuid4().hex[:16]
    parts = []
    # file content
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n')
    parts.append(b"Content-Type: text/plain\r\n\r\n")
    parts.append(text.encode())
    parts.append(b"\r\n")
    # extra fields
    if extra_fields:
        for key, val in extra_fields.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            parts.append(str(val).encode())
            parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    ct = f"multipart/form-data; boundary={boundary}"
    code, resp = api("/api/kb/upload", method="POST", body=body, token=token, ct=ct)
    assert code == 200, f"KB upload failed: {resp}"
    return resp


def test_upload_default_scope(meeting):
    """默认 scope=personal_kb, labels=空, meeting_callable=true."""
    resp = _kb_upload(
        meeting["token"],
        "测试默认 KB 参数\n",
        extra_fields={"meeting_id": meeting["mid"]},
    )
    assert resp.get("scope") == "personal_kb"
    assert resp.get("labels") == ""
    assert resp.get("meeting_callable") == "true"


def test_upload_explicit_params(meeting):
    """显式传入 scope/labels/meeting_callable."""
    resp = _kb_upload(
        meeting["token"],
        "ESG 碳管理知识\n",
        extra_fields={
            "meeting_id": meeting["mid"],
            "scope": "enterprise",
            "labels": "ESG,碳管理,绿色金融",
            "meeting_callable": "false",
        },
    )
    assert resp.get("scope") == "enterprise"
    assert "ESG" in resp.get("labels", "")
    assert resp.get("meeting_callable") == "false"


def test_upload_no_auth_401():
    """无 token → 401."""
    boundary = "----kb-test-noauth"
    body = f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"t.txt\"\r\n\r\ntest\r\n--{boundary}--\r\n".encode()
    ct = f"multipart/form-data; boundary={boundary}"
    code, _ = api("/api/kb/upload", method="POST", body=body, ct=ct)
    assert code == 401
