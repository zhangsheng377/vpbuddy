"""GPU 端 torchaudio 2.11 缺 AudioMetaData 时的 monkey-patch.

pyannote.audio 3.3.2 在 import 时引用 `torchaudio.AudioMetaData` 作为 type annotation,
torchaudio 2.9+ 才有这个类。我们装的是 2.11 但因为某种原因没暴露(可能 wheel 不全)。

运行时不需要 AudioMetaData,只是 type checker。注入一个 dummy class 让 import 通过。
"""
import torchaudio

if not hasattr(torchaudio, "AudioMetaData"):
    class _AudioMetaData:  # noqa: D401
        """dummy placeholder for torchaudio.AudioMetaData (torchaudio < 2.9 缺失)."""
        pass
    torchaudio.AudioMetaData = _AudioMetaData

if not hasattr(torchaudio, "list_audio_backends"):
    def _list_audio_backends():
        return ["soundfile"]
    torchaudio.list_audio_backends = _list_audio_backends

# PyTorch 2.6+ 默认 weights_only=True,pyannote 3.3.2 老 checkpoint 不兼容
import torch as _torch
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
    except Exception:
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
