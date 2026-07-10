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


# ── ASR 降噪第一层: 确定性轻量过滤 (Issue #31/ADR-0052) ──

_FILLER_WORDS = {"嗯", "呃", "啊", "哦", "哎", "唉", "诶", "唔", "那个", "就是", "就是说", "然后", "这个", "这样子", "那个啥", "反正"}
_NOISE_PATTERNS = ["不是不是", "怎么怎么", "什么什么", "就是就是", "对对对", "好好好", "行行行", "可以可以"]
_DEVICE_TEST_PHRASES = {"测试测试", "喂喂喂", "能听到吗", "听得见吗", "连接了吗", "开始录音了吗", "录音正常吗", "声音怎么样", "有声音吗"}


def _is_noise_only(text: str) -> bool:
    """判断句子是否只包含填充词/噪声, 不包含业务信息."""
    t = text.strip()
    if not t:
        return True
    if len(t) <= 2:
        return True
    # 设备测试短语
    if t in _DEVICE_TEST_PHRASES:
        return True
    cleaned = t
    for w in sorted(_FILLER_WORDS, key=len, reverse=True):
        cleaned = cleaned.replace(w, "")
    for p in _NOISE_PATTERNS:
        cleaned = cleaned.replace(p, "")
    cleaned = cleaned.strip("，,。.！!？?；;：: ")
    if len(cleaned) <= 2:
        return True
    # 全是标点或空白
    if not cleaned:
        return True
    return False


def _compress_repetitions(text: str) -> str:
    """压缩无意义重复: '不是不是不是' → '不是'"""
    result = text
    for pattern in _NOISE_PATTERNS:
        while pattern * 2 in result:
            result = result.replace(pattern * 2, pattern)
    return result


def _strip_fillers(text: str) -> str:
    """去除句子中的纯填充词."""
    t = text
    for w in sorted(_FILLER_WORDS, key=len, reverse=True):
        t = t.replace(w, "")
    t = t.strip("，,。.！!？?；;：: ")
    if len(t) <= 2 and text.strip():
        return text  # 保留原始, 太短了不清理
    return t or text


@dataclass
class _ASRSession:
    """单个识别会话的状态.

    fun-asr-realtime 全程同一条 WebSocket 双工流,
    模型内部自然利用上文语音特征, 无需外部注入上下文.
    """

    meeting_id: str
    session_id: str = ""
    recording_session_id: str = ""
    recognition = None
    callback: "BailianCallback" = None
    accumulated_text: str = ""
    cleaned_accumulated_text: str = ""
    sentence_count: int = 0
    noise_count: int = 0
    started_at: float = 0.0
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_sentence(self, text: str, cleaned: str, is_noise: bool = False) -> None:
        self.sentence_count += 1
        self.accumulated_text += text
        if is_noise:
            self.noise_count += 1
        else:
            self.cleaned_accumulated_text += cleaned


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

        is_noise = _is_noise_only(text) if text.strip() else False
        cleaned = _strip_fillers(_compress_repetitions(text.strip())) if text.strip() else text

        if text:
            self._safe_send({
                "type": "transcript",
                "text": text,
                "begin_time": begin_time,
                "end_time": end_time,
                "is_sentence_end": is_end,
                "is_noise": is_noise,
                "speaker_id": "UNKNOWN",
            })

        if is_end and text.strip():
            self._session.add_sentence(text, cleaned, is_noise=is_noise)
            tag = " [NOISE]" if is_noise else ""
            self._write_state(cleaned, self._session.sentence_count)
            logger.info("[bailian_asr] sentence #%d%s: %s", self._session.sentence_count, tag, text[:80])

    def _write_state(self, text: str, idx: int) -> None:
        """追加清理后文本到 MeetingStorage (Issue #31: 写 cleaned_accumulated_text)."""
        if not self._data_dir or not self._session.meeting_id:
            return
        try:
            from ..storage import MeetingStorage
            st = MeetingStorage(self._data_dir)
            mid = self._session.meeting_id
            if st.exists(mid):
                state = st.load(mid)
                state.cleaned_text = self._session.cleaned_accumulated_text or text
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
    import uuid
    _ensure_dashscope()

    session = _ASRSession(
        meeting_id=meeting_id,
        session_id=uuid.uuid4().hex[:12],
        recording_session_id=uuid.uuid4().hex[:8],
    )
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
