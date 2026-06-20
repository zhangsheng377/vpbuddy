"""PyannoteDiarizer — pyannote-audio 3.1 说话人分离

设计原则(ADR-0004):
- 只封装 pipeline,不做后处理(聚类/合并/重命名等 Step 2.5 再做)
- num_speakers 可选(None = 自动检测,业务用)
- min_speakers / max_speakers 给可选上下界
- **不依赖 HF_TOKEN**:用 ModelScope 镜像 + 本地 .bin 文件

模型准备(2026-06-21 踩坑后方案):
    pip install modelscope
    mkdir -p /tmp/pyannote_models
    modelscope download --model pyannote/speaker-diarization-3.1 \\
        --local_dir /tmp/pyannote_models/speaker-diarization-3.1
    modelscope download --model pyannote/segmentation-3.0 \\
        --local_dir /tmp/pyannote_models/segmentation-3.0
    modelscope download --model pyannote/wespeaker-voxceleb-resnet34-LM \\
        --local_dir /tmp/pyannote_models/wespeaker-voxceleb-resnet34-LM

    # 然后设置环境变量(或传参)
    export PYANNOTE_LOCAL_DIR=/tmp/pyannote_models
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# 模型下载(辅助函数)
def ensure_pyannote_models(local_dir: str = "/tmp/pyannote_models") -> dict:
    """确保 pyannote 模型已下载(用 ModelScope 镜像,不需要 HF_TOKEN)

    Returns:
        {"pipeline_dir": ..., "segmentation": ..., "embedding": ...}
    """
    base = Path(local_dir)
    paths = {
        "pipeline_dir": base / "speaker-diarization-3.1",
        "segmentation": base / "segmentation-3.0" / "pytorch_model.bin",
        "embedding": base / "wespeaker-voxceleb-resnet34-LM" / "pytorch_model.bin",
    }
    # 检查文件是否存在
    if all(p.exists() for p in paths.values()):
        return {k: str(v) for k, v in paths.items()}

    # 用 ModelScope 下载
    logger.info(f"Downloading pyannote models to {local_dir} via ModelScope...")
    try:
        from modelscope import snapshot_download
    except ImportError:
        raise ImportError("pip install modelscope")

    base.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        "pyannote/speaker-diarization-3.1",
        local_dir=str(paths["pipeline_dir"]),
    )
    snapshot_download(
        "pyannote/segmentation-3.0",
        local_dir=str(paths["pipeline_dir"].parent / "segmentation-3.0"),
    )
    snapshot_download(
        "pyannote/wespeaker-voxceleb-resnet34-LM",
        local_dir=str(paths["pipeline_dir"].parent / "wespeaker-voxceleb-resnet34-LM"),
    )
    return {k: str(v) for k, v in paths.items()}


class PyannoteDiarizer:
    """pyannote-audio 3.1 说话人分离(不需要 HF_TOKEN,用 ModelScope 镜像)

    参数:
        model_name: 默认 "pyannote/speaker-diarization-3.1"
        local_models_dir: pyannote 模型本地目录(默认 $PYANNOTE_LOCAL_DIR 或 /tmp/pyannote_models)
        device: cuda / cpu
    """

    def __init__(
        self,
        model_name: str = "pyannote/speaker-diarization-3.1",
        local_models_dir: Optional[str] = None,
        device: str = "cuda",
    ):
        self.model_name = model_name
        self.local_models_dir = (
            local_models_dir
            or os.environ.get("PYANNOTE_LOCAL_DIR")
            or "/tmp/pyannote_models"
        )
        self.device = device
        self._pipeline = None  # lazy load

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline

        # 确保模型存在
        paths = ensure_pyannote_models(self.local_models_dir)

        # Patch: 把 use_auth_token 重定向到 token(避免新版 hf_hub 报错)
        import huggingface_hub
        _orig = huggingface_hub.hf_hub_download
        def _patched(*args, **kwargs):
            if "use_auth_token" in kwargs:
                kwargs["token"] = kwargs.pop("use_auth_token")
            return _orig(*args, **kwargs)
        huggingface_hub.hf_hub_download = _patched
        import huggingface_hub.file_download
        huggingface_hub.file_download.hf_hub_download = _patched

        import torch
        from omegaconf import OmegaConf
        from hydra.utils import instantiate

        # 读本地 config
        config_path = Path(paths["pipeline_dir"]) / "config.yaml"
        cfg = OmegaConf.create(config_path.read_text())
        pipeline_cfg = cfg.pipeline
        target = pipeline_cfg.name
        params = dict(pipeline_cfg.params or {})

        # 用本地路径替换 repo id
        local_paths = {
            "segmentation": paths["segmentation"],
            "embedding": paths["embedding"],
        }
        flat_params = {}
        for k, v in params.items():
            if v == "AgglomerativeClustering":
                flat_params[k] = "AgglomerativeClustering"  # 字符串查表
            elif k in local_paths:
                flat_params[k] = local_paths[k]
            else:
                flat_params[k] = v

        logger.info(f"Loading pyannote pipeline from {config_path}...")
        inst_cfg = OmegaConf.create({"_target_": target, **flat_params})
        self._pipeline = instantiate(inst_cfg)

        # 默认参数(clustering + segmentation)
        default_params = {
            "clustering": {
                "method": "centroid",
                "min_cluster_size": 12,
                "threshold": 0.7045654963945799,
            },
            "segmentation": {"min_duration_off": 0.0},
        }
        self._pipeline = self._pipeline.instantiate(default_params)

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
            audio_path: 音频文件(推荐 16kHz mono PCM)
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
