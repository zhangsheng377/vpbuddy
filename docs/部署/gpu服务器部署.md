# GPU 服务器部署指南

> VPBuddy 端到端推理需要 GPU(本机 CPU/Mac 不跑 whisper + pyannote)。
> 本文档说清"在新 GPU 服务器上从零部署,5 分钟跑通端到端"。

## 1. 前置要求

| 项 | 要求 | 备注 |
|---|---|---|
| GPU | NVIDIA ≥ 8GB VRAM | 验证过 RTX 3090 Ti (24GB),RTX 3060 也可 |
| CUDA | ≥ 12.1 | `nvidia-smi` 看 |
| 内存 | ≥ 16GB | 模型加载 ~6GB |
| 磁盘 | ≥ 10GB | 模型 3GB + 缓存 |
| 系统 | Linux (Ubuntu/CentOS/Arch) | macOS 不支持 CUDA |
| 网络 | 出海 OR 国内镜像 | 国内用户走 ModelScope |

## 2. 一键部署

```bash
git clone https://github.com/BZ-coding/financial-data-service.git vpbuddy  # 或你的 fork
cd vpbuddy
bash scripts/setup_gpu.sh
```

脚本会自动:

1. 安装 miniconda3 (到 `~/miniconda3`,**不需要 sudo**)
2. 创建 conda env `vpbuddy-gpu` (python 3.11)
3. 安装 torch + pyannote + funasr + modelscope + faster-whisper
4. 下载所有模型到 `~/.cache/vpbuddy_models/` + `~/.cache/huggingface/hub/`
5. 跑冒烟测试,确认 pipeline 跑通

预计耗时: **5-10 分钟**(主要在下载模型,国内 ModelScope 25MB/s)。

## 3. 使用

```bash
# 激活环境
conda activate vpbuddy-gpu

# 转写音频(输出 VPBuddy 标准 transcript.json)
python scripts/gpu_transcribe.py /path/to/meeting.wav -o transcript.json

# 喂给 VPBuddy engine
PYTHONPATH=src python -c "
from vpbuddy.state import MeetingState, Platform
from vpbuddy.storage import create_storage
state = MeetingState(meeting_id='MY_MEETING', platform=Platform.FEISHU)
# ... 用 transcript 提取 requirement/goal/risk
storage = create_storage()
storage.save(state)
"
```

## 4. 切换模型/添加模型

所有模型清单在 [`scripts/download_gpu_models.py`](../../scripts/download_gpu_models.py) 的 `MODELSPECS` 列表里。

要换模型:改 `MODELSPECS` 然后重跑 `python scripts/download_gpu_models.py`。

要加模型:同样追加到 `MODELSPECS`,type 可选:

- `'ms'` — ModelScope 镜像(国内推荐,免翻墙,25MB/s)
- `'hf'` — HuggingFace 直连(需出海)
- `'hf_local'` — 本地已有 .bin 文件,只建 HF cache 链接

### 已验证可用模型

| 模型 | 来源 | 用途 | 备注 |
|---|---|---|---|
| `iic/SenseVoiceSmall` | ModelScope | 多语种 ASR | 893MB,中英粤日韩,带 emotion/event |
| `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | ModelScope | 中文 ASR | 944MB,带时间戳,会议首选 |
| `iic/speech_campplus_sv_zh-cn_16k-common` | ModelScope | 说话人 embedding | 33MB,funasr pipeline 内置 |
| `iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` | ModelScope | VAD | 40MB,funasr pipeline 内置 |
| `iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch` | ModelScope | 中文标点 | 1.2GB |
| `pyannote/segmentation-3.0` | HuggingFace | 切片分割 | 60MB |
| `pyannote/wespeaker-voxceleb-resnet34-LM` | HuggingFace | 说话人 embedding | 100MB |
| `pyannote/speaker-diarization-3.1` | HuggingFace | 说话人 pipeline config | 5KB yaml |

## 5. 部署到不同服务器

不同机器**只需**:

```bash
git clone <repo>
cd vpbuddy
bash scripts/setup_gpu.sh
```

模型会自动从 ModelScope 下载(国内)或 HuggingFace 下载(国外)。

**模型不进 git 仓**(3GB+ 不适合 git),通过 `download_gpu_models.py` 重新拉。

## 6. 验证部署

```bash
# 冒烟测试(不需音频)
conda activate vpbuddy-gpu
python scripts/gpu_transcribe.py --self-test

# 真实测试
wget https://github.com/BZ-coding/financial-data-service/raw/main/samples/test_zh_16k_mono.wav
python scripts/gpu_transcribe.py test_zh_16k_mono.wav -o /tmp/test.json
cat /tmp/test.json | python3 -m json.tool | head -30
```

## 7. 故障排查

详见 [`踩坑记录.md`](./踩坑记录.md)。常见问题:

| 现象 | 原因 | 修复 |
|---|---|---|
| `HFValidationError: Repo id must be in the form` | pyannote 3.3.2 + HF 1.20.1 不兼容 | 看踩坑记录 |
| `use_auth_token got an unexpected keyword argument` | HF API 改名 | 看踩坑记录 |
| `sentence_info` 只有 1 段 | 没加 punc_model 或 spk_model | 在 `gpu_transcribe.py` 加 |
| RTF > 1 (慢于实时) | 没装 GPU 版 torch | 重装 `torch+cu121` |

---

最后更新: 2026-06-21 (Phase 2 端到端测试后固化)