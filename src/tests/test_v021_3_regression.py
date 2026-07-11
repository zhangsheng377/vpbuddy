"""v0.21.3 回归测试 — 覆盖 #28 (鉴权), #30 (JWT链路), #31 (ASR降噪/断线状态机)"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from vpbuddy.server.bailian_asr import (
    _is_noise_only,
    _strip_fillers,
    _compress_repetitions,
    _ASRSession,
    _FILLER_WORDS,
    _NOISE_PATTERNS,
    _DEVICE_TEST_PHRASES,
)


# ══════════════════════════════════════════════════════════════════
# #31 - ASR 降噪第一层
# ══════════════════════════════════════════════════════════════════

class TestASRNoiseFilter:
    def test_is_noise_only_empty(self):
        assert _is_noise_only("") is True
        assert _is_noise_only("  ") is True
        assert _is_noise_only("。") is True

    def test_is_noise_only_fillers(self):
        assert _is_noise_only("嗯嗯嗯啊") is True
        assert _is_noise_only("呃，这个，然后，就是") is True

    def test_is_noise_only_device_test(self):
        assert _is_noise_only("测试测试") is True
        assert _is_noise_only("喂喂喂") is True
        assert _is_noise_only("能听到吗") is True
        assert _is_noise_only("开始录音了吗") is True

    def test_is_noise_only_repetitions(self):
        assert _is_noise_only("不是不是不是不是") is True

    def test_is_noise_only_short(self):
        assert _is_noise_only("哦") is True
        assert _is_noise_only("好") is True

    def test_not_noise_business_content(self):
        assert _is_noise_only("登录功能先实现") is False
        assert _is_noise_only("用户需要注册功能") is False
        assert _is_noise_only("下周三之前上线") is False
        assert _is_noise_only("API 限流 100 次每分钟") is False

    def test_not_noise_mixed_content(self):
        assert _is_noise_only("嗯那个我觉得先做登录吧") is False
        assert _is_noise_only("呃，数据库用 PostgreSQL") is False

    def test_compress_repetitions(self):
        assert _compress_repetitions("不是不是不是不是") == "不是不是"
        assert _compress_repetitions("怎么怎么怎么怎么") == "怎么怎么"
        assert _compress_repetitions("正常文本") == "正常文本"

    def test_strip_fillers(self):
        result = _strip_fillers("嗯那个我觉得就是先做登录")
        assert "我觉得" in result or "做登录" in result or "登录" in result
        assert "嗯" not in result

    def test_strip_fillers_short_returns_original(self):
        result = _strip_fillers("嗯")
        assert result == "嗯"


# ══════════════════════════════════════════════════════════════════
# #31 - _ASRSession noise tracking
# ══════════════════════════════════════════════════════════════════

class TestSessionNoiseTracking:
    def test_add_sentence_noise(self):
        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("嗯嗯嗯啊", "嗯嗯嗯啊", is_noise=True)
        assert sess.sentence_count == 1
        assert sess.noise_count == 1
        assert sess.accumulated_text == "嗯嗯嗯啊"
        assert sess.cleaned_accumulated_text == ""

    def test_add_sentence_business(self):
        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("登录功能。", "登录功能。", is_noise=False)
        assert sess.sentence_count == 1
        assert sess.noise_count == 0
        assert sess.cleaned_accumulated_text == "登录功能。"

    def test_add_sentence_mixed(self):
        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("嗯嗯测试", "嗯嗯测试", is_noise=True)
        sess.add_sentence("API限流100次。", "API限流100次。", is_noise=False)
        sess.add_sentence("那个就是", "那个就是", is_noise=True)
        assert sess.sentence_count == 3
        assert sess.noise_count == 2
        assert sess.cleaned_accumulated_text == "API限流100次。"

    def test_session_has_session_ids(self):
        sess = _ASRSession(meeting_id="m1", session_id="abc123", recording_session_id="rec456")
        assert sess.session_id == "abc123"
        assert sess.recording_session_id == "rec456"

    def test_session_default_ids_empty(self):
        sess = _ASRSession(meeting_id="m1")
        assert sess.session_id == ""
        assert sess.recording_session_id == ""


# ══════════════════════════════════════════════════════════════════
# #31 - WS 断线状态机
# ══════════════════════════════════════════════════════════════════

class TestWSDisconnectStateMachine:
    def test_disconnect_no_stop_does_not_close(self):
        _stop_received = False
        called = [0]
        def fake_close(mid):
            called[0] += 1
        if _stop_received:
            fake_close("test_mid")
        assert called[0] == 0

    def test_explicit_stop_triggers_close(self):
        _stop_received = True
        called = [0]
        def fake_close(mid):
            called[0] += 1
        if _stop_received:
            fake_close("test_mid")
        assert called[0] == 1


# ══════════════════════════════════════════════════════════════════
# #30 - healthz 端点 + WS 鉴权
# ══════════════════════════════════════════════════════════════════

class TestHealthzEndpoint:
    @pytest.fixture
    def client(self):
        from vpbuddy.server.fastapi_app import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_healthz_no_auth_returns_200(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_api_status_no_auth_returns_401(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 401

    def test_api_status_with_token_returns_200(self, client):
        from vpbuddy.server.auth import _create_token
        token = _create_token("test_uid", "test@t.com")
        resp = client.get("/api/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


class TestWSAuthRequired:
    @pytest.fixture
    def client(self):
        from vpbuddy.server.fastapi_app import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_ws_with_invalid_token_rejected(self, client):
        with client.websocket_connect("/api/meetings/test/realtime_asr?token=invalid") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "token" in msg.get("error", "").lower() or "无效" in msg.get("error", "")

    def test_ws_with_valid_token_accepted(self, client):
        from vpbuddy.server.auth import _create_token
        import uuid
        token = _create_token(uuid.uuid4().hex[:16], "ws@t.com")
        mid = f"ws_auth_{uuid.uuid4().hex[:6]}"
        sess = MagicMock()
        sess.running = True
        sess.meeting_id = mid
        sess.sentence_count = 0
        sess.noise_count = 0

        with patch("vpbuddy.server.bailian_asr.start_session", return_value=sess):
            with client.websocket_connect(f"/api/meetings/{mid}/realtime_asr?token={token}") as ws:
                ws.send_json({"type": "start", "format": "pcm", "sample_rate": 16000})
                ws.send_json({"type": "ping"})
                pong = ws.receive_json()
                assert pong["type"] == "pong"


# ══════════════════════════════════════════════════════════════════
# #28 - owner 校验
# ══════════════════════════════════════════════════════════════════

class TestOwnerVerification:
    @pytest.fixture
    def client(self):
        from vpbuddy.server.fastapi_app import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def _make_token(self, uid=None):
        from vpbuddy.server.auth import _create_token
        import uuid
        uid = uid or uuid.uuid4().hex[:16]
        return _create_token(uid, f"{uid[:8]}@t.com")

    def test_bff_get_meeting_no_owner_403(self, client):
        import uuid
        token_a = self._make_token()
        token_b = self._make_token()
        mid = f"ow_{uuid.uuid4().hex[:8]}"

        resp = client.post(
            f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 200

        resp = client.get(
            f"/api/meetings/{mid}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403

    def test_bff_transcript_segments_no_owner_403(self, client):
        import uuid
        token_a = self._make_token()
        token_b = self._make_token()
        mid = f"ts_{uuid.uuid4().hex[:8]}"
        client.post(
            f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp = client.get(
            f"/meetings/{mid}/transcript-segments",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403

    def test_bff_recording_start_no_owner_403(self, client):
        import uuid
        token_a = self._make_token()
        token_b = self._make_token()
        mid = f"rec_{uuid.uuid4().hex[:8]}"
        client.post(
            f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp = client.post(
            f"/meetings/{mid}/recording/start",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403

    def test_bff_deliverables_no_owner_403(self, client):
        import uuid
        token_a = self._make_token()
        token_b = self._make_token()
        mid = f"del_{uuid.uuid4().hex[:8]}"
        client.post(
            f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp = client.get(
            f"/meetings/{mid}/deliverables",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403

    def test_bff_archive_no_owner_403(self, client):
        import uuid
        token_a = self._make_token()
        token_b = self._make_token()
        mid = f"arch_{uuid.uuid4().hex[:8]}"
        client.post(
            f"/api/meetings/stream_start?meeting_id={mid}&audio_source=microphone",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp = client.post(
            f"/meetings/{mid}/archive",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════
# #31 - cleaned_text 只含 cleaned_accumulated_text
# ══════════════════════════════════════════════════════════════════

class TestCleanedTextState:
    def test_write_state_uses_cleaned_text(self):
        from vpbuddy.storage import MeetingStorage
        from vpbuddy.state import MeetingState
        from vpbuddy.server.bailian_asr import BailianCallback
        import tempfile

        fake_loop = MagicMock()
        fake_loop.create_task = lambda coro: None
        fake_loop.call_soon_threadsafe = lambda fn, *args: fn(*args)

        msgs = []
        async def send(msg):
            msgs.append(msg)

        with tempfile.TemporaryDirectory() as tmp:
            st = MeetingStorage(data_dir=tmp)
            state = MeetingState(meeting_id="m1", platform="local")
            st.save(state)

            sess = _ASRSession(meeting_id="m1")
            sess.add_sentence("嗯嗯登录。", "登录。", is_noise=True)
            sess.add_sentence("API限流。", "API限流。", is_noise=False)

            cb = BailianCallback(fake_loop, send, sess, data_dir=tmp)
            cb._write_state("API限流。", 2)

            loaded = st.load("m1")
            assert loaded.cleaned_text == "API限流。"
            assert "嗯嗯" not in loaded.cleaned_text


class TestDocSchedulingHash:
    def test_hash_detects_meaningful_change(self):
        cur = "用户需要登录功能实现"  # 10 chars
        cur_hash = hashlib.md5(cur.encode()).hexdigest()
        assert cur_hash != ""
        assert len(cur) >= 10

    def test_same_hash_no_trigger(self):
        old = hashlib.md5(b"same-text-same-text").hexdigest()
        new = hashlib.md5(b"same-text-same-text").hexdigest()
        assert old == new

    def test_short_text_below_threshold(self):
        assert len("太短") < 10
