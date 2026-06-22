#!/usr/bin/env bash
# install-gpu-server.sh — 生产 GPU 服务器一键部署 (2026-06-22 ADR-0009)
#
# 目标:从 0 起,5-10 分钟跑通端到端 VPBuddy + Hermes
# 范围:Ubuntu 22.04/24.04 + NVIDIA GPU + CUDA 12.x
#
# 用法:sudo bash install-gpu-server.sh [--no-models] [--user USERNAME]
#
#   --no-models    跳过模型下载(只装代码和依赖)
#   --user         指定运行用户(默认当前用户)
#
# 详见 docs/部署/INSTALL.md §角色 A
set -euo pipefail

# ===== 参数解析 =====
SKIP_MODELS=0
TARGET_USER="${SUDO_USER:-$(whoami)}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-models) SKIP_MODELS=1; shift ;;
        --user) TARGET_USER="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

echo "=================================================="
echo "  VPBuddy GPU Server 安装"
echo "=================================================="
echo "  Target user:  $TARGET_USER"
echo "  Skip models:  $SKIP_MODELS"
echo ""

# ===== 0. 系统包 =====
echo "[0/6] 系统包..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git curl wget ffmpeg build-essential \
    python3-pip python3-venv python3-dev \
    portaudio19-dev libasound2-dev

# ===== 1. NVIDIA driver + CUDA (假设已有则跳过) =====
echo "[1/6] NVIDIA/CUDA 检查..."
if ! command -v nvidia-smi &>/dev/null; then
    echo "  nvidia-smi 不存在,尝试装 NVIDIA driver 535..."
    sudo apt-get install -y -qq nvidia-driver-535 || {
        echo "❌ nvidia-driver 装失败,请手动装(参考 https://docs.nvidia.com/datacenter/tesla/tesla-installation-notes/)"
        exit 1
    }
    echo "  ⚠️  NVIDIA driver 装好,需要重启机器:sudo reboot"
    echo "  重启后再跑此脚本"
    exit 0
fi
echo "  ✅ $(nvidia-smi --query-gpu=name --format=csv,noheader,nounits | head -1)"

# ===== 2. Miniconda =====
echo "[2/6] Miniconda 检查..."
if [[ ! -d "$HOME/miniconda3" ]]; then
    echo "  装 Miniconda..."
    wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
    bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
    rm /tmp/miniconda.sh
fi
# shellcheck disable=SC1091
source "$HOME/miniconda3/etc/profile.d/conda.sh"

# ===== 3. 创建 vpbuddy-gpu conda env =====
echo "[3/6] 创建 conda env (vpbuddy-gpu)..."
if ! conda env list | grep -q vpbuddy-gpu; then
    conda create -y -n vpbuddy-gpu python=3.11
fi
conda activate vpbuddy-gpu

# 国内 pip 镜像
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || true
pip config set global.trusted-host mirrors.aliyun.com 2>/dev/null || true

# ===== 4. 装 hermes-agent + vpbuddy =====
echo "[4/6] 装 hermes-agent + vpbuddy[gpu]..."

# 先 hermes-agent(VPBuddy = Hermes runtime,ADR-0009 钉死)
pip install --quiet "hermes-agent>=0.16.0,<1.0"

# 再装 VPBuddy(从本地源码,避免 PyPI 还没发布)
cd /home/zsd/vpbuddy 2>/dev/null || {
    echo "❌ VPBuddy 源码不在 /home/zsd/vpbuddy,先 git clone:"
    echo "    git clone https://github.com/zhangsheng377/vpbuddy.git"
    exit 1
}
pip install --quiet -e ".[gpu,audio]"

# ===== 5. Hermes 配置 =====
echo "[5/6] Hermes 配置..."
mkdir -p "$HOME/.hermes"
if [[ ! -f "$HOME/.hermes/config.yaml" ]]; then
    echo "  Hermes config 不存在,创建默认配置..."
    # 创建默认 config.yaml
    cat > "$HOME/.hermes/config.yaml" <<'EOF'
model:
  default: MiniMax-M3
  provider: mini_max

providers:
  mini_max:
    base_url: https://api.minimaxi.com/v1/
    default_model: MiniMax-M3
    api_key_env: MINIMAX_API_KEY
  openrouter:
    base_url: https://openrouter.ai/api/v1/
    default_model: openrouter/free
    api_key_env: OPENROUTER_API_KEY
EOF
fi

if [[ ! -f "$HOME/.hermes/.env" ]]; then
    echo "  ⚠️  Hermes .env 不存在,创建模板..."
    cat > "$HOME/.hermes/.env" <<'EOF'
# Hermes 用的 API key (2026-06-22)
# 至少填一个:
# MINIMAX_API_KEY=your-key-here
# OPENROUTER_API_KEY=your-key-here

# GPU 模型路径(可选)
PYANNOTE_LOCAL_DIR=/home/USER/pyannote_models
EOF
    sed -i "s|/home/USER|/home/$TARGET_USER|g" "$HOME/.hermes/.env"
    echo "  ⚠️  请编辑 $HOME/.hermes/.env 填 LLM API key"
fi

# ===== 6. 模型下载 (可选) =====
if [[ $SKIP_MODELS -eq 0 ]]; then
    echo "[6/6] 下 GPU 模型..."
    PYANNOTE_DIR="$HOME/pyannote_models"
    mkdir -p "$PYANNOTE_DIR"
    export PYANNOTE_LOCAL_DIR="$PYANNOTE_DIR"

    # pyannote 说话人分离 (国内 ModelScope 镜像)
    pip install --quiet modelscope
    python3 -m modelscope.download --model pyannote/speaker-diarization-3.1 --local_dir "$PYANNOTE_DIR/speaker-diarization-3.1" 2>/dev/null || true
    python3 -m modelscope.download --model pyannote/segmentation-3.0 --local_dir "$PYANNOTE_DIR/segmentation-3.0" 2>/dev/null || true
    python3 -m modelscope.download --model pyannote/wespeaker-voxceleb-resnet34-LM --local_dir "$PYANNOTE_DIR/wespeaker-voxceleb-resnet34-LM" 2>/dev/null || true

    # faster-whisper large-v3 (HF 镜像)
    export HF_ENDPOINT=https://hf-mirror.com
    python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cuda', compute_type='float16')" 2>&1 | tail -3 || echo "  ⚠️ faster-whisper 下载失败(可重试)"

    # sentence-transformers KB embedding 模型
    export HF_HUB_OFFLINE=0  # 临时允许联网下
    python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')" 2>&1 | tail -3 || echo "  ⚠️ sentence-transformers 下失败"
    unset HF_HUB_OFFLINE
else
    echo "[6/6] 跳过模型下载 (--no-models)"
fi

# ===== 收尾 =====
echo ""
echo "=================================================="
echo "  ✅ 安装完成"
echo "=================================================="
echo ""
echo "下一步:"
echo "  1. 配 LLM API key:  vim $HOME/.hermes/.env"
echo "  2. 验证:             vpbuddy version"
echo "  3. 跑测试 (80 passed):"
echo "       conda activate vpbuddy-gpu"
echo "       PYTHONPATH=/home/zsd/vpbuddy/src python3 -m pytest /home/zsd/vpbuddy/src/tests/ -v"
echo "  4. 启动 UI (VP 入口):  vpbuddy ui --port 8765"
echo "  5. 启动 controller:    vpbuddy controller"
echo ""
echo "详见 docs/部署/INSTALL.md"