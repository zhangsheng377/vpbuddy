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
echo "[0/7] 系统包..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git curl wget ffmpeg build-essential \
    python3-pip python3-venv python3-dev \
    portaudio19-dev libasound2-dev \
    nodejs npm

# v0.22.6: mmx-cli — MiniMax 原生 VLM, vision 后备通道 (ADR-0054)
echo "  [0a/7] mmx-cli 安装..."
if ! command -v mmx &>/dev/null; then
    npm install -g mmx-cli
    echo "  ✅ mmx-cli 已安装"
else
    echo "  ✅ mmx-cli 已存在 ($(mmx --version | tail -1))"
fi
# mmx auth login 需要 API key, 需用户手动执行:
#   mmx auth login --api-key sk-xxx
# 详见 docs/decisions/0054-vision三层逃生通道-mmx-cli后备.md

# ===== 1. NVIDIA driver + CUDA (假设已有则跳过) =====
echo "[1/7] NVIDIA/CUDA 检查..."
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
echo "[2/7] Miniconda 检查..."
if [[ ! -d "$HOME/miniconda3" ]]; then
    echo "  装 Miniconda..."
    wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
    bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
    rm /tmp/miniconda.sh
fi
# shellcheck disable=SC1091
source "$HOME/miniconda3/etc/profile.d/conda.sh"

# ===== 3. 创建 vpbuddy-gpu conda env =====
echo "[3/7] 创建 conda env (vpbuddy-gpu)..."
if ! conda env list | grep -q vpbuddy-gpu; then
    conda create -y -n vpbuddy-gpu python=3.11
fi
conda activate vpbuddy-gpu

# 国内 pip 镜像
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || true
pip config set global.trusted-host mirrors.aliyun.com 2>/dev/null || true

# ===== 4. 装 hermes-agent + vpbuddy =====
echo "[4/7] 装 hermes-agent + vpbuddy[gpu]..."

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
echo "[5/7] Hermes 配置..."
mkdir -p "$HOME/.hermes"

# 🔒 信息隔离铁律 (2026-06-22 ADR-0010):
# 1. config.yaml / .env 都用占位符,真实 key 由用户手动 vim 填
# 2. 已存在的文件绝不覆盖 (开发机 / 之前的部署)
# 3. 任何 install 脚本都不接触真实 API key

if [[ ! -f "$HOME/.hermes/config.yaml" ]]; then
    echo "  Hermes config 不存在,创建干净模板(无真实 key)..."
    cat > "$HOME/.hermes/config.yaml" <<'EOF'
# Hermes Agent Configuration - CLEAN INSTALL TEMPLATE (2026-06-22)
# 真实 API key 必须通过环境变量 (MINIMAX_CN_API_KEY / OPENROUTER_API_KEY) 提供
# 详见 docs/部署/踩坑记录.md §20 信息隔离

model:
  default: MiniMax-M3
  provider: mini_max

providers:
  mini_max:
    api_key: ${MINIMAX_CN_API_KEY}
    base_url: https://api.minimaxi.com/v1
    default_model: MiniMax-M3
    thinking: true
  openrouter:
    api_key: ${OPENROUTER_API_KEY}
    base_url: https://openrouter.ai/api/v1
    thinking: true

fallback_providers:
  - provider: openrouter
    model: openrouter/free
    base_url: https://openrouter.ai/api/v1
    api_mode: chat_completions

credential_pool_strategies: {}
toolsets:
  - hermes-cli
max_concurrent_sessions: null

agent:
  max_turns: 90
  gateway_timeout: 1800
  restart_drain_timeout: 60
  api_max_retries: 3
  reasoning_effort: xhigh
  task_completion_guidance: true
  environment_probe: true
  image_input_mode: auto

terminal:
  backend: local
  modal_mode: auto
  cwd: .
  timeout: 180

logging:
  level: INFO
  redact_secrets: true
EOF
    chmod 600 "$HOME/.hermes/config.yaml"
fi

if [[ ! -f "$HOME/.hermes/.env" ]]; then
    echo "  ⚠️  Hermes .env 不存在,创建干净模板(只有占位符)..."
    cat > "$HOME/.hermes/.env" <<EOF
# Hermes Agent Environment - CLEAN INSTALL TEMPLATE (2026-06-22)
# 🔒 你必须手动填你的 LLM API key:
#   vim ~/.hermes/.env
# 🔒 不要从开发机 scp 这个文件 — install 脚本绝不包含真实 key

# ===== LLM Provider (至少填一个) =====
MINIMAX_CN_API_KEY=YOUR_M...n
# OPENROUTER_API_KEY=YOUR_O...n

# ===== GPU 模型路径 =====
PYANNOTE_LOCAL_DIR=/home/$TARGET_USER/pyannote_models
HF_HUB_OFFLINE=1
EOF
    chmod 600 "$HOME/.hermes/.env"
    echo ""
    echo "  ⚠️  ⚠️  ⚠️  请编辑 \$HOME/.hermes/.env 填你的 LLM API key  ⚠️  ⚠️  ⚠️"
    echo "      vim \$HOME/.hermes/.env"
    echo "      # 把 MINIMAX_CN_API_KEY=YOUR_M...n 改成你的真 key"
    echo ""
else
    echo "  ✅ ~/.hermes/.env 已存在(不动用户填好的 key)"
fi

# ===== 6. 模型下载 (可选) =====
if [[ $SKIP_MODELS -eq 0 ]]; then
    echo "[6/7] 下 GPU 模型..."
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
    echo "[6/7] 跳过模型下载 (--no-models)"
fi

# ===== 6.5 bashrc 写 HF 离线铁律 (ADR-0011 落地) =====
echo "[7/7] bashrc 写 HF 离线铁律(机器重启后仍生效)..."
BASHRC="$HOME/.bashrc"
if ! grep -q "VPBuddy HF 离线铁律" "$BASHRC" 2>/dev/null; then
    cat >> "$BASHRC" <<'EOF'

# VPBuddy HF 离线铁律 (ADR-0011 2026-06-23)
# 国内 huggingface.co 被墙,默认走本地 cache + hf-mirror 镜像
# 临时下新模型:HF_HUB_OFFLINE=0 vpbuddy setup-gpu
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYANNOTE_LOCAL_DIR="$HOME/pyannote_models"
EOF
    echo "  ✅ ~/.bashrc 已加 HF 离线铁律 (4 行)"
else
    echo "  ✅ ~/.bashrc 已含 HF 离线铁律(跳过)"
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
echo "  3. 跑测试 (116 passed):"
echo "       conda activate vpbuddy-gpu"
echo "       PYTHONPATH=/home/zsd/vpbuddy/src python3 -m pytest /home/zsd/vpbuddy/src/tests/ -v"
echo "  4. 启动 VP 入口 (推荐用官方脚本):"
echo "       bash scripts/start-vpbuddy.sh all      # controller + UI"
echo "       bash scripts/start-vpbuddy.sh ui        # 只 UI"
echo "  5. 浏览器访问:  http://localhost:8765"
echo ""
echo "详见 docs/部署/INSTALL.md"