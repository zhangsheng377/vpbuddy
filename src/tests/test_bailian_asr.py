"""测试: bailian_asr — 百炼 Fun‑ASR‑Realtime relay 模块

覆盖:
- _ASRSession 句子累积
- BailianCallback 回调链 (on_event / on_open / on_close / on_complete / on_error)
- _write_state → MeetingStorage 持久化
- 异常容错 (data_dir 为空 / meeting 不存在)
- 完整生命周期: start_session → send_audio → stop_session
"""
from __future__ import annotations

import sys
import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 本地没有 dashscope 时注入 mock ────────────────────────────────
_FAKE_DASHSCOPE = MagicMock()
_FAKE_DASHSCOPE.audio.asr.Recognition = MagicMock()

# RecognitionResult 必须是一个 class 且有 is_sentence_end 静态方法
class _FakeRecognitionResult:
    @staticmethod
    def is_sentence_end(sentence):
        return sentence.get("sentence_end", False) if hasattr(sentence, 'get') else False

_mock_asr = MagicMock()
_mock_asr.RecognitionResult = _FakeRecognitionResult
sys.modules["dashscope"] = _FAKE_DASHSCOPE
sys.modules["dashscope.audio"] = MagicMock()
sys.modules["dashscope.audio.asr"] = _mock_asr
sys.modules["dashscope.audio.asr.Recognition"] = _FAKE_DASHSCOPE.audio.asr.Recognition

from vpbuddy.state import MeetingState
from vpbuddy.storage import MeetingStorage
from vpbuddy.server.bailian_asr import (
    _ASRSession,
    BailianCallback,
    start_session,
    send_audio,
    stop_session,
    API_KEY,
    MODEL,
)

# ── fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_storage(tmp_path):
    return MeetingStorage(data_dir=tmp_path / "meetings")


@pytest.fixture
def state_in_storage(tmp_storage):
    """在 tmp_storage 中预建一个 MeetingState."""
    state = MeetingState(meeting_id="test_m1", platform="local")
    tmp_storage.save(state)
    return state, tmp_storage


@pytest.fixture
def fake_loop():
    """模拟 asyncio loop — call_soon_threadsafe + create_task 同步执行."""
    _loop = MagicMock()
    def _create_task(coro):
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            loop.run_until_complete(coro)
            loop.close()
        except Exception:
            pass
    _loop.create_task = _create_task
    def _call(fn, *args):
        try:
            fn(*args)
        except Exception:
            pass
    _loop.call_soon_threadsafe = _call
    return _loop


@pytest.fixture
def capture_msgs():
    """收集 callback 推送的 JSON 消息."""
    msgs = []
    async def _send(msg):
        msgs.append(msg)
    return msgs, _send


# ── _ASRSession ────────────────────────────────────────────────────────

class TestASRSession:
    def test_initial_state(self):
        sess = _ASRSession(meeting_id="m1")
        assert sess.meeting_id == "m1"
        assert sess.accumulated_text == ""
        assert sess.sentence_count == 0
        assert sess.recognition is None
        assert sess.running is False
        assert isinstance(sess.lock, type(threading.Lock()))

    def test_add_sentence_accumulates(self):
        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("第一句。", "第一句。")
        assert sess.sentence_count == 1
        assert sess.accumulated_text == "第一句。"

        sess.add_sentence("第二句。", "第二句。")
        assert sess.sentence_count == 2
        assert sess.accumulated_text == "第一句。第二句。"

        sess.add_sentence("第三句。", "第三句。")
        assert sess.sentence_count == 3
        assert sess.accumulated_text == "第一句。第二句。第三句。"

    def test_add_sentence_empty(self):
        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("", "")
        assert sess.sentence_count == 1
        assert sess.accumulated_text == ""

    def test_accumulated_text_long(self):
        sess = _ASRSession(meeting_id="m1")
        texts = [f"这是第{i}句话。" for i in range(100)]
        for t in texts:
            sess.add_sentence(t, t)
        assert sess.sentence_count == 100
        assert len(sess.accumulated_text) == sum(len(t) for t in texts)
        assert sess.accumulated_text.startswith("这是第0句话。")
        assert sess.accumulated_text.endswith("这是第99句话。")


# ── BailianCallback._write_state ───────────────────────────────────────

class TestWriteState:
    def test_writes_cleaned_text(self, fake_loop, capture_msgs, tmp_storage):
        msgs, send = capture_msgs
        state = MeetingState(meeting_id="m1", platform="local")
        tmp_storage.save(state)

        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("你好。", "你好。")

        cb = BailianCallback(fake_loop, send, sess, data_dir=str(tmp_storage.data_dir))
        cb._write_state("你好。", 1)

        loaded = tmp_storage.load("m1")
        assert loaded.cleaned_text == "你好。"
        assert loaded.last_updated != state.last_updated

    def test_accumulates_over_sentences(self, fake_loop, capture_msgs, tmp_storage):
        msgs, send = capture_msgs
        state = MeetingState(meeting_id="m1", platform="local")
        tmp_storage.save(state)

        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess, data_dir=str(tmp_storage.data_dir))

        sentences = ["第一句。", "第二句来了。", "第三句收尾。"]
        accumulated = ""
        for i, text in enumerate(sentences, 1):
            sess.add_sentence(text, text)
            accumulated += text
            cb._write_state(text, i)
            loaded = tmp_storage.load("m1")
            assert loaded.cleaned_text == accumulated

    def test_no_data_dir_does_nothing(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("test。", "test。")
        cb = BailianCallback(fake_loop, send, sess, data_dir="")
        cb._write_state("test。", 1)
        assert sess.accumulated_text == "test。"

    def test_nonexistent_meeting_noop(self, fake_loop, capture_msgs, tmp_storage):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="ghost")
        sess.add_sentence("孤魂野鬼。", "孤魂野鬼。")
        cb = BailianCallback(fake_loop, send, sess, data_dir=str(tmp_storage.data_dir))
        cb._write_state("孤魂野鬼。", 1)
        assert not tmp_storage.exists("ghost")

    def test_exception_does_not_crash(self, fake_loop, capture_msgs, tmp_storage):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="m1")
        # 传入不存在的目录, _write_state 的 st.save 抛异常但被 try/except 吞掉
        cb = BailianCallback(fake_loop, send, sess, data_dir="/nonexistent/path/xyz")
        cb._write_state("test。", 1)
        assert sess.accumulated_text == ""  # add_sentence 没调, 直接写 state 失败

    def test_uses_accumulated_text_when_available(self, fake_loop, capture_msgs, tmp_storage):
        msgs, send = capture_msgs
        state = MeetingState(meeting_id="m1", platform="local")
        tmp_storage.save(state)

        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("第一句。", "第一句。")
        sess.add_sentence("第二句。", "第二句。")

        cb = BailianCallback(fake_loop, send, sess, data_dir=str(tmp_storage.data_dir))
        cb._write_state("第二句。", 2)

        loaded = tmp_storage.load("m1")
        assert loaded.cleaned_text == "第一句。第二句。"


# ── BailianCallback 回调链 ─────────────────────────────────────────────

class TestBailianCallbackEvents:
    def test_on_open(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess)
        cb.on_open()
        assert len(msgs) == 1
        assert msgs[0]["type"] == "asr_status"
        assert msgs[0]["status"] == "connected"

    def test_on_close(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess)
        cb.on_close()
        assert len(msgs) == 1
        assert msgs[0]["type"] == "asr_status"
        assert msgs[0]["status"] == "closed"

    def test_on_complete(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="m1")
        sess.add_sentence("第1句。", "第1句。")
        sess.add_sentence("第2句。", "第2句。")
        cb = BailianCallback(fake_loop, send, sess)
        cb.on_complete()
        assert len(msgs) == 1
        assert msgs[0]["type"] == "asr_complete"
        assert msgs[0]["sentence_count"] == 2
        assert msgs[0]["full_text"] == "第1句。第2句。"

    def test_on_error(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess)

        class FakeError:
            message = "connection refused"
        cb.on_error(FakeError())

        assert len(msgs) == 1
        assert msgs[0]["type"] == "asr_error"
        assert "connection refused" in msgs[0]["error"]


class TestBailianCallbackOnEvent:
    def test_mid_sentence_no_write(self, fake_loop, capture_msgs, tmp_storage):
        """句中片段(is_end=False): 推 WS 但不写 storage."""
        msgs, send = capture_msgs
        state = MeetingState(meeting_id="m1", platform="local")
        tmp_storage.save(state)

        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess, data_dir=str(tmp_storage.data_dir))

        fake_result = _make_fake_result(text="我在说话", begin_time=0, end_time=1000, is_end=False)
        cb.on_event(fake_result)

        assert any(m["type"] == "transcript" for m in msgs)
        loaded = tmp_storage.load("m1")
        assert loaded.cleaned_text == ""
        assert sess.sentence_count == 0

    def test_sentence_end_writes_state(self, fake_loop, capture_msgs, tmp_storage):
        """句末(is_end=True): 写 storage 并累积."""
        msgs, send = capture_msgs
        state = MeetingState(meeting_id="m1", platform="local")
        tmp_storage.save(state)

        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess, data_dir=str(tmp_storage.data_dir))

        fake_result = _make_fake_result(text="百炼测试完成。", begin_time=0, end_time=2000, is_end=True)
        cb.on_event(fake_result)

        assert sess.sentence_count == 1
        assert sess.accumulated_text == "百炼测试完成。"
        loaded = tmp_storage.load("m1")
        assert "百炼测试完成" in loaded.cleaned_text

    def test_empty_text_sentence_end_noop(self, fake_loop, capture_msgs, tmp_storage):
        """句末但 text 为空: 不累积."""
        msgs, send = capture_msgs
        state = MeetingState(meeting_id="m1", platform="local")
        tmp_storage.save(state)

        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess, data_dir=str(tmp_storage.data_dir))

        fake_result = _make_fake_result(text="", begin_time=0, end_time=500, is_end=True)
        cb.on_event(fake_result)

        assert sess.sentence_count == 0
        loaded = tmp_storage.load("m1")
        assert loaded.cleaned_text == ""

    def test_transcript_push_includes_timestamps(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = _ASRSession(meeting_id="m1")
        cb = BailianCallback(fake_loop, send, sess)

        fake_result = _make_fake_result(text="timestamp test", begin_time=3500, end_time=7000, is_end=True)
        cb.on_event(fake_result)

        t_msg = next(m for m in msgs if m["type"] == "transcript")
        assert t_msg["begin_time"] == 3500
        assert t_msg["end_time"] == 7000
        assert t_msg["text"] == "timestamp test"


# ── 生命周期集成 ───────────────────────────────────────────────────────

class TestLifecycle:
    def test_start_session_basic(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = start_session(
            loop=fake_loop,
            meeting_id="lifecycle_m1",
            send_json=send,
            sample_rate=16000,
            fmt="pcm",
        )
        assert sess.meeting_id == "lifecycle_m1"
        assert sess.running is True
        assert sess.recognition is not None
        assert sess.sentence_count == 0

        send_audio(sess, b"\x00" * 3200)
        stop_session(sess)
        assert sess.running is False
        assert sess.recognition is None

    def test_send_audio_after_stop_noop(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = start_session(fake_loop, "m1", send, 16000, "pcm")
        stop_session(sess)
        send_audio(sess, b"\x00" * 3200)

    def test_stop_twice_noop(self, fake_loop, capture_msgs):
        msgs, send = capture_msgs
        sess = start_session(fake_loop, "m1", send, 16000, "pcm")
        stop_session(sess)
        stop_session(sess)


# ── 线程安全 ───────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_add_sentence(self):
        """多线程同时 add_sentence, 最终 accumulated_text 长度正确."""
        sess = _ASRSession(meeting_id="m1")
        threads = []
        results = [[] for _ in range(5)]

        def worker(idx, texts):
            for t in texts:
                sess.add_sentence(t, t)
                results[idx].append(t)

        all_texts = [
            [f"T{idx}-{j}。" for j in range(20)]
            for idx in range(5)
        ]
        for idx in range(5):
            t = threading.Thread(target=worker, args=(idx, all_texts[idx]))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        total_len = sum(len(s) for group in all_texts for s in group)
        assert sess.sentence_count == 100
        assert len(sess.accumulated_text) == total_len


# ── 常量 ───────────────────────────────────────────────────────────────

class TestConstants:
    def test_api_key_set(self):
        if not API_KEY:
            pytest.skip("API_KEY 来自环境变量, 本地测试可设置 DASHSCOPE_API_KEY")
        assert API_KEY.startswith("sk-") or API_KEY.startswith("LV-"), "API_KEY 应以 sk- 或 LV- 开头"

    def test_model_is_fun_asr_realtime(self):
        assert MODEL == "fun-asr-realtime"


# ── helpers ─────────────────────────────────────────────────────────────

class _FakeSentence:
    def __init__(self, d):
        self._d = d
    def get(self, key, default=None):
        return self._d.get(key, default)


class _FakeResult:
    def __init__(self, sentence_dict):
        self._sentence = _FakeSentence(sentence_dict)
        self._sentence_dict = sentence_dict
    def get_sentence(self):
        if not self._sentence_dict:
            return None
        return self._sentence


def _make_fake_result(text, begin_time, end_time, is_end):
    return _FakeResult({
        "text": text,
        "begin_time": begin_time,
        "end_time": end_time,
        "sentence_end": is_end,
    })
