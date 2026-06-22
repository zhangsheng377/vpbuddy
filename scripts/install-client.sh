#!/usr/bin/env bash
# install-client.sh — VP 桌面客户端一键部署 (2026-06-22 ADR-0009)
#
# 目标:VP 在自己 Mac/笔记本上跑 vpbuddy ui + 音频采集
# 范围:macOS / Linux desktop,可有或无 GPU(优先用云 LLM API)
# 关键:**VP 客户端独立运营,完全在 VP 自己机器上,不是在我们 zsd/GPU 上**
#
# 用法:
#   1. 从 GitHub clone vpbuddy:  git clone https://github.com/zhangsheng377/vpbuddy.git ~/vpbuddy
#   2. 跑:  cd ~/vpbuddy && bash scripts/install-client.sh
#   3. 填 API key:  vim ~/.hermes/.env
#   4. 启动:  source ~/.vpbuddy-venv/bin/activate && vpbuddy ui --port 8765
#
# 详见 docs/部署/INSTALL.md §角色 B
set -euo pipefail

echo "=================================================="
echo "  VPBuddy 桌面客户端安装 (VP 独立运营)"
echo "=================================================="

# ===== 1. 系统包 =====
echo "[1/6] 系统包..."
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
        portaudio19-dev libasound2-dev ffmpeg \
        build-essential
fi

# ===== 2. 创建 venv =====
echo "[2/6] 创建 venv (.vpbuddy-venv)..."
VENV_DIR="$HOME/.vpbuddy-venv"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip

# 国内 pip 镜像
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || true

# ===== 3. 装 hermes-agent + vpbuddy[audio] =====
echo "[3/6] 装 hermes-agent + vpbuddy[audio]..."
# hermes-agent 从 pypi 装(VPBuddy 依赖的 AI agent 运行时,2026-06-22 决定)
pip install --quiet "hermes-agent>=0.16.0,<1.0"

# vpbuddy 装当前目录(用户要先 git clone)
# 兼容两种情况:(a) 从 github clone 的标准目录 (b) 开发时已 cd 进来
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPBUDDY_ROOT="$(dirname "$SCRIPT_DIR")"
echo "  VPBuddy 根目录: $VPBUDDY_ROOT"
pip install --quiet -e "${VPBUDDY_ROOT}[audio]"

# ===== 4. KB 依赖(2026-06-22 加:sqlite-vec + sentence-transformers) =====
echo "[4/6] 装 KB 依赖(sqlite-vec + sentence-transformers)..."
pip install --quiet sqlite-vec sentence-transformers

# ===== 5. 预下载 KB embedding 模型(2026-06-22:首次启动冷加载 40s,提前下好) =====
echo "[5/6] 预下载 KB embedding 模型(256MB,首次启动需要)..."
python3 - <<'PYEOF'
import os
# 显式下载(不放到 HF cache,显式提示用户)
from sentence_transformers import SentenceTransformer
model_name = "paraphrase-multilingual-MiniLM-L12-v2"
cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
try:
    print(f"  正在下载 {model_name} → {cache_dir} ...")
    m = SentenceTransformer(model_name, cache_folder=cache_dir)
    print(f"  ✅ 模型已下载,共 {len(m.get_sentence_embedding_dimension())} 维向量")
except Exception as e:
    print(f"  ⚠️  模型下载失败 (可能需要翻墙): {e}")
    print(f"  首次启动会再次尝试。如持续失败,设环境变量 HF_ENDPOINT=https://hf-mirror.com 后重跑")
PYEOF

# ===== 6. Hermes 配置 =====
echo "[6/6] Hermes 配置..."
mkdir -p "$HOME/.hermes"

# 🔒 信息隔离铁律 (2026-06-22 ADR-0010):
# 1. config.yaml / .env 都用占位符,真实 key 由用户手动 vim 填
# 2. 已存在的文件绝不覆盖 (开发机 / 之前的部署)
# 3. 任何 install 脚本都不接触真实 API key

if [[ ! -f "$HOME/.hermes/config.yaml" ]]; then
    echo "  Hermes config 不存在,创建干净模板..."
    cat > "$HOME/.hermes/config.yaml" <<'EOF'
# Hermes Agent Configuration - CLEAN INSTALL TEMPLATE (2026-06-22)
# 真实 API key 必须通过环境变量提供
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
    cat > "$HOME/.hermes/.env" <<'EOF'
# Hermes Agent Environment - CLEAN INSTALL TEMPLATE (2026-06-22)
# 🔒 你必须手动填你的 LLM API key:
#   vim ~/.hermes/.env
# 🔒 不要从开发机 scp 这个文件 — install 脚本绝不包含真实 key

# ===== LLM Provider (至少填一个) =====
MINIMAX_CN_API_KEY=YOUR_M...n
# OPENROUTER_API_KEY=YOUR_O...n

# ===== KB embedding 模型 (国内环境走镜像站) =====
HF_ENDPOINT=https://hf-mirror.com
EOF
    chmod 600 "$HOME/.hermes/.env"
    echo ""
    echo "  ⚠️  ⚠️  ⚠️  请编辑 ~/.hermes/.env 填你的 LLM API key  ⚠️  ⚠️  ⚠️"
    echo "      vim ~/.hermes/.env"
    echo "      # 把 MINIMAX_CN_API_KEY=YOUR_M...n 改成你的真 key"
    echo ""
else
    echo "  ✅ ~/.hermes/.env 已存在(不动用户填好的 key)"
fi

# ===== 收尾 =====
echo ""
echo "=================================================="
echo "  ✅ VP 桌面客户端安装完成"
echo "=================================================="
echo ""
echo "下一步:"
echo "  1. 配 API key:  vim $HOME/.hermes/.env"
echo "  2. 激活 venv:   source $VENV_DIR/bin/activate"
echo "  3. 验证:        vpbuddy version"
echo "  4. 启动 UI:     vpbuddy ui --port 8765"
echo "  5. 验证 KB:     vpbuddy kb-status  (空状态OK,trigger 后会出现 stored docs)"
echo "  6. (可选) 装 sample 音频:    vpbuddy transcribe <audio.wav>"
echo ""
echo "详见 docs/部署/INSTALL.md §角色 B"