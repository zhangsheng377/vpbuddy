"""WhisperProvider — faster-whisper large-v3 包装(GPU 推理)

设计原则(ADR-0004):
- 写一个具体的 WhisperProvider,**不写抽象基类**(Y1)
- 整文件同步(不 streaming,Y2)
- 用 faster-whisper 内置 VAD(不引入独立 VAD,Y3)
- 简单参数(model_size/device/compute_type/language),不写 config(Y10)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path

from .transcript import TranscriptResult, TranscriptSegment

logger = logging.getLogger(__name__)


class WhisperProvider:
    """faster-whisper 大模型推理(GPU 优先,fallback CPU)

    参数:
        model_size: "large-v3" (默认) / "large-v3-turbo" / "medium" / "small"
        device: "cuda" (默认) / "cpu"
        compute_type: "float16" (GPU) / "int8" (CPU) / "float32"
        language: "zh" (默认) / "en" / "auto"
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str = "zh",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language if language != "auto" else None
        self._model = None  # lazy load

    def _load(self):
        """惰性加载模型(避免 import 时就占显存)"""
        if self._model is not None:
            return self._model
        from faster_whisper import WhisperModel
        logger.info(f"Loading WhisperModel({self.model_size}, {self.device}, {self.compute_type})...")
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("WhisperModel loaded.")
        return self._model

    def transcribe_file(
        self,
        audio_path: str | Path,
        language: str | None = None,
        vad_filter: bool = True,
    ) -> list[TranscriptSegment]:
        """整文件转写

        Args:
            audio_path: wav/mp3/flac 等
            language: 覆盖默认语言(None = 用 self.language)
            vad_filter: 是否启用 faster-whisper 内置 VAD filter

        Returns:
            List[TranscriptSegment](无说话人信息)
        """
        audio_path = str(audio_path)
        lang = language if language is not None else self.language
        model = self._load()

        segments_iter, info = model.transcribe(
            audio_path,
            language=lang,
            vad_filter=vad_filter,
            word_timestamps=False,
        )
        # info: language, language_probability, duration, duration_after_vad
        logger.info(f"Detected lang={info.language} prob={info.language_probability:.2f} "
                    f"duration={info.duration:.1f}s vad_after={info.duration_after_vad:.1f}s")

        out: list[TranscriptSegment] = []
        for seg in segments_iter:
            # seg.avg_logprob ∈ [-1, 0] → exp 转 confidence
            import math
            confidence = math.exp(seg.avg_logprob) if seg.avg_logprob is not None else 1.0
            out.append(TranscriptSegment(
                start_sec=float(seg.start),
                end_sec=float(seg.end),
                text=seg.text.strip(),
                confidence=confidence,
                language=info.language or "zh",
            ))
        return out

    def transcribe_to_result(
        self,
        audio_path: str | Path,
        language: str | None = None,
    ) -> TranscriptResult:
        """转写并包装成 TranscriptResult(方便统一序列化)

        注意:这个 result 里 **没有说话人信息**,只有 whisper 的转写段。
        融合由 TranscriptionEngine 做。
        """
        segments = self.transcribe_file(audio_path, language=language)
        # 取音频时长(优先用 segments 推算,fallback 0)
        duration_sec = max((s.end_sec for s in segments), default=0.0)
        return TranscriptResult(
            audio_path=str(audio_path),
            language=segments[0].language if segments else (language or "zh"),
            duration_sec=duration_sec,
            num_speakers=0,  # 由 engine 填
            segments=[],  # engine 会用 DiarizedSegment 重填
            model_name=self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            diarization_model="",  # engine 填
            created_at=datetime.now(UTC).isoformat(),
        )
