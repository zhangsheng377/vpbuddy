#!/usr/bin/env bash
# install-client.sh — VP 桌面客户端一键部署 (2026-06-22 ADR-0009)
#
# 目标:VP 在自己 Mac/笔记本上跑 vpbuddy ui + 音频采集
# 范围:macOS / Linux desktop,无 GPU(用云 LLM API)
#
# 用法:bash install-client.sh
#
# 详见 docs/部署/INSTALL.md §角色 B
set -euo pipefail

echo "=================================================="
echo "  VPBuddy 桌面客户端安装 (无 GPU)"
echo "=================================================="

# ===== 1. 系统包 =====
echo "[1/4] 系统包..."
if [[ "$(uname)" == "Darwin" ]]; then
    # macOS — 用 brew
    if ! command -v brew &>/dev/null; then
        echo "❌ 需要 Homebrew (https://brew.sh)"
        exit 1
    fi
    brew install portaudio ffmpeg python@3.11 2>/dev/null || true
else
    # Linux
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3-pip python3-venv python3-dev \
        portaudio19-dev libasound2-dev ffmpeg
fi

# ===== 2. 创建 venv =====
echo "[2/4] 创建 venv (.venv)..."
VENV_DIR="$HOME/.vpbuddy-venv"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 国内 pip 镜像
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || true

# ===== 3. 装 hermes-agent + vpbuddy[audio] =====
echo "[3/4] 装 hermes-agent + vpbuddy[audio]..."
pip install --quiet --upgrade pip
pip install --quiet "hermes-agent>=0.16.0,<1.0"
pip install --quiet -e "/home/zsd/vpbuddy[audio]" 2>/dev/null || \
pip install --quiet -e ".[audio]"

# ===== 4. Hermes 配置 =====
echo "[4/4] Hermes 配置..."
mkdir -p "$HOME/.hermes"
if [[ ! -f "$HOME/.hermes/config.yaml" ]]; then
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
    cat > "$HOME/.hermes/.env" <<'EOF'
# 填至少一个 LLM API key:
MINIMAX_API_KEY=your-key-here
EOF
    echo "  ⚠️  请编辑 $HOME/.hermes/.env 填 API key"
fi

# ===== 收尾 =====
echo ""
echo "=================================================="
echo "  ✅ 客户端安装完成"
echo "=================================================="
echo ""
echo "下一步:"
echo "  1. 配 API key:  vim $HOME/.hermes/.env"
echo "  2. 激活 venv:   source $VENV_DIR/bin/activate"
echo "  3. 验证:        vpbuddy version"
echo "  4. 启动 UI:     vpbuddy ui --port 8765"
echo "  5. (可选) 装 sample 音频:    vpbuddy transcribe <audio.wav>"
echo ""
echo "详见 docs/部署/INSTALL.md §角色 B"