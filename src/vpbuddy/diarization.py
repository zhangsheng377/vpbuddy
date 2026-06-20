"""PyannoteDiarizer — pyannote-audio 3.1 说话人分离

设计原则(ADR-0004):
- 只封装 pipeline,不做后处理(聚类/合并/重命名等 Step 2.5 再做)
- num_speakers 可选(None = 自动检测,业务用)
- min_speakers / max_speakers 给可选上下界
- HF token 读 env(Y9)
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


class PyannoteDiarizer:
    """pyannote-audio 3.1 说话人分离(需要 HF_TOKEN)

    参数:
        model_name: 默认 "pyannote/speaker-diarization-3.1"
        hf_token: 显式传 / None = 读 env HF_TOKEN
        use_auth_token: pyannote 兼容别名(传 hf_token 即可)
    """

    def __init__(
        self,
        model_name: str = "pyannote/speaker-diarization-3.1",
        hf_token: Optional[str] = None,
        device: str = "cuda",
    ):
        self.model_name = model_name
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.device = device
        self._pipeline = None  # lazy load

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        if not self.hf_token:
            raise RuntimeError(
                "pyannote 模型是 gated 的,需要 HF_TOKEN。\n"
                "步骤:1) huggingface.co 同意 pyannote/speaker-diarization-3.1 用户协议\n"
                "      2) https://huggingface.co/settings/tokens 创建 token\n"
                "      3) export HF_TOKEN=hf_xxxxxxxxxxxx"
            )
        from pyannote.audio import Pipeline
        import torch
        logger.info(f"Loading pyannote pipeline {self.model_name}...")
        self._pipeline = Pipeline.from_pretrained(
            self.model_name,
            use_auth_token=self.hf_token,
        )
        # 移到 GPU
        if self.device == "cuda" and torch.cuda.is_available():
            self._pipeline.to(torch.device("cuda"))
            logger.info("Pipeline moved to CUDA.")
        else:
            logger.info(f"Pipeline running on {self.device}.")
        return self._pipeline

    def diarize(
        self,
        audio_path: Union[str, Path],
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ):
        """说话人分离

        Args:
            audio_path: 音频文件
            num_speakers: 精确指定(2 = 强制 2 人)
            min_speakers: 最少人数(>=)
            max_speakers: 最多人数(<=)

        Returns:
            pyannote.core.Annotation 对象:
                - .itertracks(yield_label=True): (segment, track, label) 迭代
                - label 形如 "SPEAKER_00"
        """
        pipeline = self._load()
        audio_path = str(audio_path)
        # pyannote 3.1 API:
        if num_speakers is not None:
            diarization = pipeline(audio_path, num_speakers=num_speakers)
        elif min_speakers is not None or max_speakers is not None:
            kwargs = {}
            if min_speakers is not None:
                kwargs["min_speakers"] = min_speakers
            if max_speakers is not None:
                kwargs["max_speakers"] = max_speakers
            diarization = pipeline(audio_path, **kwargs)
        else:
            diarization = pipeline(audio_path)

        # 统计说话人数
        labels = set()
        for _segment, _track, label in diarization.itertracks(yield_label=True):
            labels.add(label)
        logger.info(f"Diarization found {len(labels)} speakers: {sorted(labels)}")
        return diarization

    def get_speaker_turns(self, audio_path: Union[str, Path], **kwargs) -> list:
        """便利方法:直接返回 [(start, end, speaker_id), ...] 列表"""
        diarization = self.diarize(audio_path, **kwargs)
        turns = []
        for segment, _track, label in diarization.itertracks(yield_label=True):
            turns.append((float(segment.start), float(segment.end), label))
        turns.sort(key=lambda t: t[0])
        return turns
