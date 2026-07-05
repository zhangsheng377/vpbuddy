"""GPU pytest conftest — 4 个关键 monkey-patch + 离线默认

(1) torchaudio.AudioMetaData / list_audio_backends 缺失注入(pyannote 3.3.2 兼容)
(2) torch.load weights_only=False 默认(pyannote 老 checkpoint 兼容)
(3) torch.serialization.add_safe_globals(白名单 pyannote 类)
(4) huggingface_hub.hf_hub_download: use_auth_token → token(HF 1.20+ 兼容)

(5) 【2026-06-22 新增】默认设 HF_HUB_OFFLINE=1 + TRANSFORMERS_OFFLINE=1 —
    sentence-transformers 加载模型时,huggingface_hub 即使有本地 cache 也会
    向 HF endpoint HEAD 请求验证最新版,可能解析到 facebookresearch 的 AS
    (AS32934 = Meta,IP 段 69.63.186.0/24 + 2a03:2880::/32 复用于多家),
    触发"卡 53 分钟"假死。强制离线 + 本地 cache 后,模型直接走 cache 不联网。
    详见 docs/部署/踩坑记录.md §19。

(6) 【2026-06-22 新增】默认设 RUN_GPU_INTEGRATION=1 —
    test_engine.py 3 个集成测试默认 skip("需要 GPU"),GPU 端必须显式启用。
"""
from __future__ import annotations
import os

# === 关键 (5): 默认离线,模型全走 cache ===
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# === 关键 (6): GPU 端默认跑集成测试 ===
os.environ.setdefault("RUN_GPU_INTEGRATION", "1")

import sys
import types

try:
    import torchaudio  # type: ignore
    _HAS_TORCHAUDIO = True
except ImportError:
    _HAS_TORCHAUDIO = False

if _HAS_TORCHAUDIO:
    if not hasattr(torchaudio, "AudioMetaData"):
        class _AudioMetaData:  # noqa: D401
            """dummy placeholder for torchaudio.AudioMetaData (torchaudio < 2.9 缺失)."""
            pass
        torchaudio.AudioMetaData = _AudioMetaData

    if not hasattr(torchaudio, "list_audio_backends"):
        def _list_audio_backends():
            return ["soundfile"]
        torchaudio.list_audio_backends = _list_audio_backends
else:
    # CPU only / dev box 没有 torchaudio:
    # - 保留 module 占位(让 `import torchaudio` 不挂)
    # - sentence-transformers 的 is_torchaudio_available() 会通过 (无 spec 报错)
    # - 关键修复:给 dummy 真实 __spec__ + __path__,防止 transformers.is_package_available 抛错
    import importlib.machinery
    _spec = importlib.machinery.ModuleSpec("torchaudio", loader=None, is_package=True)
    sys.modules["torchaudio"] = importlib.util.module_from_spec(_spec)  # type: ignore
    import torchaudio  # type: ignore  # noqa: F811
    torchaudio.AudioMetaData = type("AudioMetaData", (), {})
    torchaudio.list_audio_backends = lambda: ["soundfile"]
    torchaudio.load = lambda *a, **kw: (None, 16000)

# PyTorch 2.6+ 默认 weights_only=True,pyannote 3.3.2 老 checkpoint 不兼容
# (e2e 测试不依赖 torch, guard 防止无 torch 环境抛 ModuleNotFoundError)
try:
    import torch as _torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

if _HAS_TORCH:
    from torch.serialization import add_safe_globals

    # 把 pyannote 用到的全局类加进 safe_globals(允许 weights_only=True 加载)
    _safe_classes = []
    for mod_name in [
        "torch.torch_version",
        "pyannote.audio.core.task",
        "pyannote.audio.core.model",
        "pyannote.audio.models.embedding",
        "pyannote.audio.models.segmentation",
        "pyannote.audio.models.blocks",
    ]:
        try:
            mod = __import__(mod_name, fromlist=["*"])
            for attr in dir(mod):
                cls = getattr(mod, attr, None)
                if isinstance(cls, type):
                    _safe_classes.append(cls)
        except (ImportError, AttributeError, RuntimeError):
            pass

    if _safe_classes:
        try:
            add_safe_globals(_safe_classes)
        except Exception:
            pass

    # 同时把 weights_only 默认改成 False(更宽)
    _orig_load = _torch.load
    def _patched_load(*args, **kwargs):
        if "weights_only" not in kwargs:
            kwargs["weights_only"] = False
        return _orig_load(*args, **kwargs)
    _torch.load = _patched_load

    # pyannote.audio 可能 import 了 `from torch import load` 直接引用了
    import torch
    torch.load = _patched_load

    # huggingface_hub 1.20 + deprecated use_auth_token → token
    try:
        import huggingface_hub
        _orig_download = huggingface_hub.hf_hub_download
        def _patched_download(*args, **kwargs):
            if "use_auth_token" in kwargs and "token" not in kwargs:
                kwargs["token"] = kwargs.pop("use_auth_token")
            return _orig_download(*args, **kwargs)
        huggingface_hub.hf_hub_download = _patched_download
    except ImportError:
<<<<<<< Updated upstream
        pass
=======
        pass
>>>>>>> Stashed changes
