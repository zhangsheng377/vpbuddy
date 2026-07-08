"""百炼 Fun-ASR-Realtime WebSocket relay 模块.

架构:
  客户端 WS → GPU Server → dashscope Recognition → 百炼 WS
  ─────────────────────────────────────────────────────
  PCM 音频帧 (binary) → send_audio_frame() → 百炼
  JSON 控制消息 ← → 回调 on_event() → JSON 推客户端

上下文增强:
  fun‑asr‑realtime 在同一 WebSocket 双工流内自动利用上文,
  不需要外部注入. 单次 start()→stop() 即覆盖整场会议.
"""

from __future__ import annotations

import os
import threading
import time
import json
import logging
from datetime import datetime
from typing import Callable, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MODEL = "fun-asr-realtime"
# 2026-07-07 ADR-0051: API Key 从 Hermes .env 读取, 不硬编码
API_KEY = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY", "")


@dataclass
class _ASRSession:
    """单个识别会话的状态.

    fun-asr-realtime 全程同一条 WebSocket 双工流,
    模型内部自然利用上文语音特征, 无需外部注入上下文.
    """

    meeting_id: str
    recognition = None
    callback: "BailianCallback" = None
    accumulated_text: str = ""
    sentence_count: int = 0
    started_at: float = 0.0
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_sentence(self, text: str) -> None:
        self.sentence_count += 1
        self.accumulated_text += text


class BailianCallback:
    """dashscope Recognition 回调, 将结果通过 WS 推给客户端.

    回调在 dashscope 内部线程触发 (非 asyncio event loop),
    用 call_soon_threadsafe 桥接到 asyncio 发送.

    每完成一句, 同时写入 MeetingStorage 以便文档生成 agent 读取.
    """

    def __init__(self, loop, send_json: Callable, session: _ASRSession, data_dir: str = ""):
        self._loop = loop
        self._send = send_json
        self._session = session
        self._data_dir = data_dir

    def _safe_send(self, msg: dict) -> None:
        try:
            self._loop.call_soon_threadsafe(
                lambda: self._loop.create_task(self._send(msg))
            )
        except Exception:
            pass

    def on_open(self) -> None:
        logger.info("[bailian_asr] WS connected to Bailian, meeting=%s", self._session.meeting_id)
        self._safe_send({"type": "asr_status", "status": "connected"})

    def on_close(self) -> None:
        logger.info("[bailian_asr] WS closed by Bailian, meeting=%s", self._session.meeting_id)
        self._safe_send({"type": "asr_status", "status": "closed"})

    def on_complete(self) -> None:
        logger.info("[bailian_asr] recognition complete, meeting=%s sentences=%d",
                     self._session.meeting_id, self._session.sentence_count)
        self._safe_send({
            "type": "asr_complete",
            "sentence_count": self._session.sentence_count,
            "full_text": self._session.accumulated_text,
        })

    def on_error(self, result) -> None:
        msg = str(getattr(result, "message", result))
        logger.error("[bailian_asr] error meeting=%s: %s", self._session.meeting_id, msg)
        self._safe_send({"type": "asr_error", "error": msg})

    def on_event(self, result) -> None:
        sentence = result.get_sentence()
        if not sentence:
            return

        text = sentence.get("text", "")
        begin_time = sentence.get("begin_time", 0)
        end_time = sentence.get("end_time", 0)
        is_end = sentence.get("sentence_end", False)

        from dashscope.audio.asr import RecognitionResult
        if hasattr(RecognitionResult, "is_sentence_end"):
            is_end = RecognitionResult.is_sentence_end(sentence) or is_end

        if text:
            self._safe_send({
                "type": "transcript",
                "text": text,
                "begin_time": begin_time,
                "end_time": end_time,
                "is_sentence_end": is_end,
            })

        if is_end and text.strip():
            self._session.add_sentence(text.strip())
            # 同步写入 MeetingStorage, 让文档 agent 能实时读到文本
            self._write_state(text, self._session.sentence_count)
            logger.info("[bailian_asr] sentence #%d: %s", self._session.sentence_count, text[:80])

    def _write_state(self, text: str, idx: int) -> None:
        """追加最新句子到 MeetingStorage (线程安全, 简单文件 I/O)."""
        if not self._data_dir or not self._session.meeting_id:
            return
        try:
            from ..storage import MeetingStorage
            st = MeetingStorage(self._data_dir)
            mid = self._session.meeting_id
            if st.exists(mid):
                state = st.load(mid)
                state.cleaned_text = self._session.accumulated_text or text
                state.last_updated = datetime.now().isoformat()
                st.save(state)
        except Exception as e:
            logger.error("[bailian_asr] _write_state failed: %s", e)


def _ensure_dashscope() -> None:
    import dashscope
    dashscope.api_key = API_KEY


def start_session(
    loop,
    meeting_id: str,
    send_json: Callable,
    sample_rate: int = 16000,
    fmt: str = "pcm",
    data_dir: str = "",
) -> _ASRSession:
    """启动一个百炼实时 ASR 会话."""
    _ensure_dashscope()

    session = _ASRSession(meeting_id=meeting_id)
    session.started_at = time.time()

    from dashscope.audio.asr import Recognition

    callback = BailianCallback(loop, send_json, session, data_dir=data_dir)
    session.callback = callback

    recognition = Recognition(
        model=MODEL,
        format=fmt,
        sample_rate=sample_rate,
        semantic_punctuation_enabled=True,
        callback=callback,
    )

    session.recognition = recognition
    session.running = True

    # 启动流式识别
    recognition.start()
    logger.info("[bailian_asr] session started: meeting=%s rate=%d", meeting_id, sample_rate)

    return session


def send_audio(session: _ASRSession, data: bytes) -> None:
    """向百炼推送一帧音频."""
    if session.running and session.recognition:
        session.recognition.send_audio_frame(data)


def stop_session(session: _ASRSession) -> None:
    """停止识别会话."""
    logger.info("[bailian_asr] stopping session: meeting=%s sentences=%d",
                 session.meeting_id, session.sentence_count)
    session.running = False
    if session.recognition:
        try:
            session.recognition.stop()
        except Exception as e:
            logger.error("[bailian_asr] stop error: %s", e)
    session.recognition = None
