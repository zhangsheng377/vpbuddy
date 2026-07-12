"""v0.22.6: KB content_hash 跨用户去重 + chat 图片落盘 API 测试"""

from __future__ import annotations
import json
import uuid
from .conftest import api


class TestKBDedup:
    def test_same_user_duplicate_blocked(self, meeting):
        """同用户上传相同内容 → duplicate=True."""
        mid = meeting["mid"]
        tok = meeting["token"]
        boundary = "----dedup-" + uuid.uuid4().hex[:16]
        text = f"唯一去重测试内容 {uuid.uuid4().hex[:8]}\n"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="meeting_id"\r\n\r\n{mid}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="dedup.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n{text}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        ct = f"multipart/form-data; boundary={boundary}"

        code, r1 = api("/api/kb/upload", method="POST", body=body, token=tok, ct=ct)
        assert code == 200, f"first upload failed: {r1}"
        assert r1.get("duplicate") is not True

        code, r2 = api("/api/kb/upload", method="POST", body=body, token=tok, ct=ct)
        assert code == 200
        assert r2.get("duplicate") is True, f"same content should be duplicate: {r2}"

    def test_cross_user_no_false_duplicate(self, meeting, auth_alt):
        """v0.22.6: 不同用户上传相同内容 → 不应误判为重复."""
        mid_a = meeting["mid"]
        tok_a = meeting["token"]
        tok_b = auth_alt["token"]
        mid_b = f"kb_xuser_{uuid.uuid4().hex[:8]}"
        code, _ = api(f"/api/meetings/stream_start?meeting_id={mid_b}&audio_source=microphone",
                       method="POST", token=tok_b)
        assert code == 200

        text = f"跨用户去重测试 {uuid.uuid4().hex[:8]}\n"
        boundary = "----xuser-" + uuid.uuid4().hex[:16]
        body_a = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="meeting_id"\r\n\r\n{mid_a}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="a.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n{text}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        body_b = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="meeting_id"\r\n\r\n{mid_b}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="b.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n{text}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        ct = f"multipart/form-data; boundary={boundary}"

        code, r1 = api("/api/kb/upload", method="POST", body=body_a, token=tok_a, ct=ct)
        assert code == 200
        assert r1.get("duplicate") is not True

        code, r2 = api("/api/kb/upload", method="POST", body=body_b, token=tok_b, ct=ct)
        assert code == 200
        assert r2.get("duplicate") is not True, \
            f"cross-user upload should NOT be blocked as duplicate: {r2}"


class TestChatUploadImage:
    def test_image_upload_returns_path(self, meeting):
        """v0.22.6: chat 图片上传返回 status=image + path."""
        mid = meeting["mid"]
        tok = meeting["token"]
        import struct, io
        buf = io.BytesIO()
        buf.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        buf.write(b"\xff\xdb\x00\x43\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\x09\x09\x08\x0a\x0c")
        buf.write(b"\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
        buf.write(b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00")
        buf.write(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\xff\xd9")
        img = buf.getvalue()

        boundary = "----img-" + uuid.uuid4().hex[:16]
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="text"\r\n\r\n这是什么图片?\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="test.jpg"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode() + img + f"\r\n--{boundary}--\r\n".encode()
        ct = f"multipart/form-data; boundary={boundary}"

        code, resp = api(f"/api/meetings/{mid}/chat", method="POST", body=body, token=tok, ct=ct, timeout=30)
        assert code == 200, f"chat upload failed: {resp}"
        files = resp.get("files", [])
        image_files = [f for f in files if f.get("status") == "image"]
        assert len(image_files) >= 1, f"should have image file: {files}"
        img_file = image_files[0]
        assert img_file.get("status") == "image"
        assert "path" in img_file, f"image should have 'path' field (v0.22.6): {img_file}"
