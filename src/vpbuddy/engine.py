"""TranscriptionEngine — Whisper 转写 + pyannote 说话人 融合

设计原则(ADR-0004):
- 简单时间窗口对齐:每个 whisper segment 的中点 → 找 pyannote turn 中点最近的 speaker
- 不做复杂的 ASR-Diarization 联合对齐(WhisperX 那种) — YAGNI
- 不做 VAD 二次处理 — Y4
- 输出 DiarizedSegment(带 speaker_id),不耦合 Step 1 MeetingState
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path

from .diarization import PyannoteDiarizer
from .transcript import DiarizedSegment, TranscriptResult, TranscriptSegment
from .whisper_provider import WhisperProvider

logger = logging.getLogger(__name__)


class TranscriptionEngine:
    """ASR + 说话人分离 融合

    Args:
        whisper: 已配置好的 WhisperProvider
        diarizer: 已配置好的 PyannoteDiarizer
    """

    def __init__(self, whisper: WhisperProvider, diarizer: PyannoteDiarizer):
        self.whisper = whisper
        self.diarizer = diarizer

    @classmethod
    def default(
        cls,
        model_size: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
        hf_token: str | None = None,  # 兼容旧 API(已弃用,见 ADR-0004)
        pyannote_local_dir: str | None = None,
    ) -> TranscriptionEngine:
        """便利工厂:用推荐配置初始化

        Args:
            model_size: Whisper 模型大小(large-v3 / medium / ...)
            device: cuda / cpu
            compute_type: float16 / int8 / float32
            hf_token: 已弃用(2026-06-21)。改用 ModelScope 镜像 + 本地 .bin。
            pyannote_local_dir: pyannote 模型目录(默认 $PYANNOTE_LOCAL_DIR 或 /tmp/pyannote_models)
        """
        return cls(
            whisper=WhisperProvider(
                model_size=model_size,
                device=device,
                compute_type=compute_type,
            ),
            diarizer=PyannoteDiarizer(
                local_models_dir=pyannote_local_dir,
                device=device,
            ),
        )

    def _assign_speaker(self, seg: TranscriptSegment, turns: list) -> str:
        """给一个 whisper segment 找最近的说话人 turn

        策略:取 segment 中点,在 pyannote turns 里找中点最近的那个
        """
        if not turns:
            return "SPEAKER_UNKNOWN"
        mid = (seg.start_sec + seg.end_sec) / 2.0
        best_label = "SPEAKER_UNKNOWN"
        best_dist = float("inf")
        for t_start, t_end, label in turns:
            t_mid = (t_start + t_end) / 2.0
            dist = abs(mid - t_mid)
            if dist < best_dist:
                best_dist = dist
                best_label = label
        return best_label

    def process(
        self,
        audio_path: str | Path,
        language: str | None = None,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> TranscriptResult:
        """完整流程:whisper 转写 + pyannote 说话人 + 融合"""
        audio_path = str(audio_path)
        logger.info(f"TranscriptionEngine.process({audio_path}) start.")

        # 1) Whisper 转写
        logger.info("[1/3] Running Whisper...")
        whisper_segs: list[TranscriptSegment] = self.whisper.transcribe_file(
            audio_path, language=language
        )
        logger.info(f"[1/3] Whisper produced {len(whisper_segs)} segments.")

        # 2) Pyannote 说话人
        logger.info("[2/3] Running Pyannote...")
        turns = self.diarizer.get_speaker_turns(
            audio_path,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        logger.info(f"[2/3] Pyannote produced {len(turns)} speaker turns.")

        # 3) 融合
        logger.info("[3/3] Merging...")
        diarized: list[DiarizedSegment] = []
        for seg in whisper_segs:
            speaker_id = self._assign_speaker(seg, turns)
            diarized.append(DiarizedSegment(
                start_sec=seg.start_sec,
                end_sec=seg.end_sec,
                text=seg.text,
                confidence=seg.confidence,
                language=seg.language,
                speaker_id=speaker_id,
                speaker_name=None,  # 后续人工/stt_map 填入(ADR-0008 Superseded: 不再依赖飞书妙记)
                source="whisper+pyannote",
            ))

        # 统计
        speaker_ids = set(s.speaker_id for s in diarized)
        duration_sec = max((s.end_sec for s in diarized), default=0.0)

        result = TranscriptResult(
            audio_path=audio_path,
            language=diarized[0].language if diarized else "zh",
            duration_sec=duration_sec,
            num_speakers=len(speaker_ids),
            segments=diarized,
            model_name=self.whisper.model_size,
            device=self.whisper.device,
            compute_type=self.whisper.compute_type,
            diarization_model=self.diarizer.model_name,
            created_at=datetime.now(UTC).isoformat(),
        )
        logger.info(f"Done. {result.stats()}")
        return result
