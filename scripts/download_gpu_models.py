#!/usr/bin/env python3
"""下载所有 VPBuddy 需要的模型。

设计原则:
- 模型**不进 git 仓**(3GB+),只在脚本里记录"从哪下 + 怎么改"。
- 优先 ModelScope 镜像(国内 25MB/s,免翻墙),fallback HuggingFace。
- 模型按用途分类:

  ASR(自动语音识别):
    - iic/SenseVoiceSmall           893MB  中英粤日韩 多语种,带 emotion+event
    - iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch
                                      944MB  中文长音频,带时间戳

  Speaker(说话人):
    - iic/speech_campplus_sv_zh-cn_16k-common    33MB   说话人 embedding
    - iic/speech_fsmn_vad_zh-cn-16k-common-pytorch  40MB  语音活动检测

  Pyannote(说话人分离):
    - pyannote/segmentation-3.0                  60MB   切片分割
    - pyannote/speaker-diarization-3.1          5KB yaml pipeline 配置
    - pyannote/wespeaker-voxceleb-resnet34-LM   100MB  embedding

  Sentence(标点 + 句切):
    - iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch  1.2GB

- 全部下载到 ~/.cache/vpbuddy_models/ 本地仓库,然后手工构建 HF 缓存布局
  (因为 pyannote 3.3.2 + HF 1.20.1 不兼容,需 hack — 见 docs/部署/踩坑记录.md)

2026-06-21 张胜东 + Hermes 写
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# === 配置 ===
MODELS_DIR = Path(os.environ.get("VPBUDDY_MODELS_DIR", Path.home() / ".cache" / "vpbuddy_models"))
HF_CACHE_DIR = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface" / "hub"))

# (model_id, type, target_relative_path, description)
# type: 'ms' = ModelScope, 'hf' = HuggingFace, 'hf_link' = HF 但本地已有 .bin
MODELSPECS = [
    # === ASR ===
    ("iic/SenseVoiceSmall", "ms", "asr/SenseVoiceSmall", "SenseVoice 多语种 ASR"),
    ("iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
     "ms", "asr/paraformer-zh", "Paraformer 中文长音频 ASR"),
    # === Speaker + VAD ===
    ("iic/speech_campplus_sv_zh-cn_16k-common", "ms", "speaker/campplus", "CampPlus 说话人 embedding"),
    ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "ms", "vad/fsmn", "VAD 语音活动检测"),
    # === Punctuation ===
    ("iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
     "ms", "punc/ct-transformer", "中文标点恢复"),
    # === Pyannote (用本地已下载版本) ===
    # 这些是 pyannote 用的,必须按 HF cache 布局放才能用 pyannote 3.x API
    # 见 docs/部署/踩坑记录.md "pyannote + HF 兼容性"
    ("pyannote/segmentation-3.0", "hf_local", "pyannote/segmentation-3.0", "Pyannote 切片分割 (本地)"),
    ("pyannote/speaker-diarization-3.1", "hf_local", "pyannote/speaker-diarization-3.1", "Pyannote 说话人 pipeline (本地)"),
    ("pyannote/wespeaker-voxceleb-resnet34-LM", "hf_local", "pyannote/wespeaker-voxceleb-resnet34-LM", "Pyannote WeSpeaker embedding (本地)"),
]


def download_modelscope(model_id: str, target: Path) -> None:
    """从 ModelScope 下载,内置镜像加速。"""
    print(f"  ModelScope: {model_id}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if (target / "config.json").exists():
        print(f"    → 已有,跳过")
        return
    # 用 python SDK 而非 CLI,免去登录
    from modelscope import snapshot_download
    cache_root = target.parent.parent
    snapshot_download(model_id, cache_dir=str(cache_root))


def download_huggingface(model_id: str, target: Path) -> None:
    """从 HuggingFace 下载(需外网/代理)。
    注:国内推荐先把 .bin 放到本地,改 type='hf_local'。
    """
    print(f"  HuggingFace: {model_id}")
    target.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id=model_id,
        local_dir=str(target),
        local_dir_use_symlinks=False,
    )


def link_local(model_id: str, source: Path, target: Path) -> None:
    """对已下载到 MODELS_DIR 的模型,在 HF cache 里建符号链接。

    pyannote 3.3.2 + huggingface_hub 1.20.1 配合使用要求:
    ~/.cache/huggingface/hub/models--{org}--{repo}/snapshots/main/{files}
    缺一不可,所以我们手工建 layout。
    """
    target.mkdir(parents=True, exist_ok=True)
    snapshots = target / "snapshots" / "main"
    snapshots.mkdir(parents=True, exist_ok=True)
    refs = target / "refs"
    refs.mkdir(exist_ok=True)
    (refs / "main").write_text("main")

    for f in source.iterdir():
        if f.is_file():
            link = snapshots / f.name
            if not link.exists():
                os.symlink(f, link)
        elif f.is_dir():
            dest = snapshots / f.name
            if not dest.exists():
                shutil.copytree(f, dest)


def main():
    print(f"MODELS_DIR  = {MODELS_DIR}")
    print(f"HF_CACHE_DIR = {HF_CACHE_DIR}")
    print(f"总模型数: {len(MODELSPECS)}\n")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    for spec in MODELSPECS:
        model_id, kind, rel, desc = spec
        target = MODELS_DIR / rel
        print(f"[{kind}] {desc}")
        print(f"  → {target}")

        try:
            if kind == "ms":
                download_modelscope(model_id, target)
            elif kind == "hf":
                download_huggingface(model_id, target)
            elif kind == "hf_local":
                # 本地已有,只需建 HF cache 链接
                if not target.exists():
                    print(f"  ⚠️ 本地不存在: {target}")
                    print(f"     请手动下载到 {target},或改 kind='hf' 从 HF 下载")
                    continue
                print(f"  本地已有,建 HF cache 链接...")
                hf_target = HF_CACHE_DIR / f"models--{model_id.replace('/', '--')}"
                link_local(model_id, target, hf_target)
                print(f"  → HF cache: {hf_target}")
        except Exception as e:
            print(f"  ✗ 失败: {type(e).__name__}: {e}")
            continue
        print()

    print("\n=== 模型清单 ===")
    total_size = 0
    for spec in MODELSPECS:
        model_id, kind, rel, desc = spec
        target = MODELS_DIR / rel
        if target.exists():
            size = sum(f.stat().st_size for f in target.rglob('*') if f.is_file())
            total_size += size
            print(f"  {desc:40s} {size/1024/1024:7.1f} MB  → {target}")
        else:
            print(f"  {desc:40s}   缺失 → {target}")
    print(f"\n总占用: {total_size/1024/1024/1024:.2f} GB")


if __name__ == "__main__":
    main()