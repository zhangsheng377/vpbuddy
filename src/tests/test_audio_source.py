"""测试 v0.6 Phase 4 (ADR-0021) 服务端: audio_source 字段 + stream_start API 接受 + 持久化."""

from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.state import MeetingState, Platform, AudioSourceKind


# ── AudioSourceKind enum ──


def test_audio_source_kind_values():
    """三个值: microphone / loopback / both."""
    assert AudioSourceKind.MICROPHONE.value == "microphone"
    assert AudioSourceKind.LOOPBACK.value == "loopback"
    assert AudioSourceKind.BOTH.value == "both"


def test_audio_source_kind_from_string():
    """字符串可转 enum (来自 query / form)."""
    assert AudioSourceKind("microphone") is AudioSourceKind.MICROPHONE
    assert AudioSourceKind("loopback") is AudioSourceKind.LOOPBACK
    assert AudioSourceKind("both") is AudioSourceKind.BOTH


def test_audio_source_kind_invalid_raises():
    """非法字符串 ValueError (调方负责 fallback)."""
    with pytest.raises(ValueError):
        AudioSourceKind("invalid")
    with pytest.raises(ValueError):
        AudioSourceKind("")


# ── MeetingState.audio_source 字段 ──


def test_meeting_state_default_audio_source():
    """新建 MeetingState 默认 microphone (兼容老客户端)."""
    s = MeetingState(meeting_id="m1")
    assert s.audio_source == AudioSourceKind.MICROPHONE


def test_meeting_state_audio_source_persists_through_serialization():
    """audio_source 字段能 JSON 序列化 + 反序列化."""
    s = MeetingState(meeting_id="m1", audio_source=AudioSourceKind.BOTH)
    j = s.model_dump()
    assert j["audio_source"] == "both"
    s2 = MeetingState.model_validate(j)
    assert s2.audio_source == AudioSourceKind.BOTH


def test_meeting_state_audio_source_loopback():
    s = MeetingState(meeting_id="m1", audio_source=AudioSourceKind.LOOPBACK)
    assert s.audio_source == AudioSourceKind.LOOPBACK


def test_meeting_state_audio_source_default_for_legacy_json():
    """老 JSON (没 audio_source 字段) 加载时默认 microphone — 向后兼容."""
    legacy = {
        "meeting_id": "old",
        "platform": "local",
        "project_name": None,
        "started_at": "2026-06-01T00:00:00+00:00",
        "speaker_map": {}, "last_updated": "2026-06-01T00:00:00+00:00",
        "vpbuddy_version": "0.5.0",
    }
    s = MeetingState.model_validate(legacy)
    assert s.audio_source == AudioSourceKind.MICROPHONE


# ── storage round-trip ──


def test_audio_source_round_trip_through_storage(tmp_path):
    """audio_source 字段经 storage.save → load 完整往返."""
    from vpbuddy.storage import MeetingStorage
    storage = MeetingStorage(tmp_path)
    s = MeetingState(meeting_id="rt", audio_source=AudioSourceKind.LOOPBACK)
    storage.save(s)
    loaded = storage.load("rt")
    assert loaded.audio_source == AudioSourceKind.LOOPBACK



# ── HTTP 端点测试 — v0.9.0 旧 Handler 已删除, 由 FastAPI E2E 覆盖 ──
