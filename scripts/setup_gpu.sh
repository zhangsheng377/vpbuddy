#!/usr/bin/env bash
# VPBuddy GPU 服务器一键部署脚本
# 在新 GPU 服务器(任何发行版)上跑这个脚本即可完成全部部署
# 用法: bash setup_gpu.sh [--help]
#
# 设计原则(YAGNI):
# - 不假设发行版(ubuntu/centos/arch 都跑)
# - 不假设已有 conda(自动检测)
# - 不假设网络好(funasr/modelscope 镜像,中国下载 25MB/s)
# - 不写 system-wide systemd(各服务器 init 不同),只装在用户家目录
#
# 完成后:
# - miniconda3 在 ~/miniconda3
# - conda env: vpbuddy-gpu (python 3.11)
# - 模型在 ~/.cache/modelscope + ~/.cache/huggingface/hub
# - 调用: conda activate vpbuddy-gpu && python scripts/gpu_transcribe.py xxx.wav
#
# 2026-06-21 张胜东 + Hermes 写

set -euo pipefail

# === --help 处理(防止误触发实际跑) ===
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<EOF
VPBuddy GPU 服务器一键部署脚本

用法: bash setup_gpu.sh [--help]

完成后:
  - miniconda3 在 ~/miniconda3
  - conda env: vpbuddy-gpu (python 3.11)
  - 模型在 ~/.cache/modelscope + ~/.cache/huggingface/hub
  - 调用: conda activate vpbuddy-gpu && python scripts/gpu_transcribe.py xxx.wav

详细文档: docs/部署/gpu服务器部署.md
踩坑记录: docs/部署/踩坑记录.md
EOF
    exit 0
fi

# === 配置区(改这里就能定制) ===
ENV_NAME="vpbuddy-gpu"
PYTHON_VERSION="3.11"
MODELS_DIR="${HOME}/.cache/vpbuddy_models"   # 模型本地仓库目录
HF_CACHE_DIR="${HOME}/.cache/huggingface/hub"
PYANNOTE_LOCAL_DIR="${MODELS_DIR}/pyannote_models"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo_step() { echo -e "${GREEN}▶${NC} $*"; }
echo_warn() { echo -e "${YELLOW}⚠${NC} $*"; }
echo_err()  { echo -e "${RED}✗${NC} $*" >&2; }

# === Step 1: 基础工具 ===
echo_step "Step 1/6: 检查基础工具..."
for cmd in curl wget git; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo_err "missing command: $cmd"
        echo "  ubuntu: sudo apt install -y $cmd"
        echo "  centos: sudo yum install -y $cmd"
        echo "  arch:   sudo pacman -S --noconfirm $cmd"
        exit 1
    fi
done

# === Step 2: miniconda(若没装)===
if ! command -v conda >/dev/null 2>&1; then
    echo_step "Step 2/6: 安装 miniconda(用户目录,不需 sudo)..."
    MINICONDA_DIR="${HOME}/miniconda3"
    if [ ! -d "$MINICONDA_DIR" ]; then
        wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
        bash /tmp/miniconda.sh -b -p "$MINICONDA_DIR"
        rm /tmp/miniconda.sh
    fi
    # shellcheck disable=SC1091
    source "$MINICONDA_DIR/etc/profile.d/conda.sh"
else
    echo_step "Step 2/6: conda 已存在"
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh"
fi

# === Step 3: 创建 conda env ===
echo_step "Step 3/6: 创建 conda env: $ENV_NAME (python $PYTHON_VERSION)..."
if ! conda env list | grep -q "^$ENV_NAME "; then
    conda create -n "$ENV_NAME" python="$PYTHON_VERSION" -y
fi
conda activate "$ENV_NAME"

# === Step 4: 装依赖(版本固定,2026-06-21 验证可用)===
echo_step "Step 4/6: 安装 Python 依赖..."
# torch 需对应 CUDA 版本(默认 cu121,适配 30/40 系列)
pip install --quiet --upgrade pip
pip install --quiet \
    "torch>=2.5,<3" \
    "torchvision>=0.20,<1" \
    "torchaudio>=2.5,<3" \
    "pyannote-audio>=3.3,<4" \
    "pyannote-core>=6.0,<7" \
    "funasr>=1.1.9,<1.2" \
    "modelscope>=1.20,<2" \
    "faster-whisper>=1.0,<2" \
    "huggingface_hub>=1.20,<2" \
    "sqlite-vec" \
    "numpy>=2.0,<3" \
    "pydub" \
    "onnx" \
    "onnxconverter_common"

# === Step 5: 下载模型 ===
echo_step "Step 5/6: 下载模型(国内镜像 ~25MB/s,约 3GB)..."
mkdir -p "$MODELS_DIR" "$HF_CACHE_DIR"
export PYANNOTE_LOCAL_DIR="$PYANNOTE_LOCAL_DIR"
python "$(dirname "$0")/download_gpu_models.py"

# === Step 6: 验证 ===
echo_step "Step 6/6: 验证 GPU 推理..."
python "$(dirname "$0")/gpu_transcribe.py" --self-test

echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ VPBuddy GPU 部署完成!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo ""
echo "使用方式:"
echo "  conda activate $ENV_NAME"
echo "  python scripts/gpu_transcribe.py your_audio.wav -o transcript.json"
echo ""
echo "模型位置:"
echo "  $MODELS_DIR          (本地仓库,offline 推理)"
echo "  $HF_CACHE_DIR        (HF 标准缓存)"
echo ""
echo "环境变量(写到 ~/.bashrc 让永久生效):"
echo "  export PYANNOTE_LOCAL_DIR=$PYANNOTE_LOCAL_DIR"
echo "  export HF_HUB_OFFLINE=1"
echo "  export MODELSCOPE_CACHE=${HOME}/.cache/modelscope"