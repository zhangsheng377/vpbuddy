#!/usr/bin/env bash
# install-client.sh — VP 桌面客户端一键部署 (2026-06-25 极简)
#
# 目标:VP 在自己 Mac/笔记本上跑 vpbuddy ui + 音频采集
# 范围:macOS / Linux desktop
# 关键:VPBuddy 客户端不调 LLM, 不调 KB, 全部走 GPU server
#
# 用法:
#   1. 从 GitHub clone vpbuddy:  git clone https://github.com/zhangsheng377/vpbuddy.git ~/vpbuddy
#   2. 跑:  cd ~/vpbuddy && bash scripts/install-client.sh
#   3. 改 GPU server 地址:  vim ~/.vpbuddy-client.yaml  (改 gpu_server_url)
#   4. 启动:  source ~/.vpbuddy-venv/bin/activate && vpbuddy ui --port 8765
#   5. (推荐) 跑 Tauri 客户端:  cd vpbuddy-client && npm install && cd src-tauri && cargo build --release
#
# 详见 docs/部署/INSTALL.md §角色 B
set -euo pipefail

echo "=================================================="
echo "  VPBuddy 桌面客户端安装 (VP 独立运营)"
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
        portaudio19-dev libasound2-dev ffmpeg \
        build-essential
fi

# ===== 2. 创建 venv =====
echo "[2/4] 创建 venv (.vpbuddy-venv)..."
VENV_DIR="$HOME/.vpbuddy-venv"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip

# 国内 pip 镜像
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || true

# ===== 3. 装 vpbuddy[audio] =====
echo "[3/4] 装 vpbuddy[audio]..."
# 2026-06-25 简化: VPBuddy 客户端本身不调 LLM, 不装 hermes-agent
# LLM 流量全在 GPU server 端 vpbuddy-gpu conda env 跑
# 用户若要 VP Chat, 直接在 GPU server 上用 hermes-agent 即可

# vpbuddy 装当前目录(用户要先 git clone)
# 兼容两种情况:(a) 从 github clone 的标准目录 (b) 开发时已 cd 进来
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPBUDDY_ROOT="$(dirname "$SCRIPT_DIR")"
echo "  VPBuddy 根目录: $VPBUDDY_ROOT"
pip install --quiet -e "${VPBUDDY_ROOT}[audio]"

# ===== 4-5. 客户端配置 (无 KB, 无 LLM) =====
echo "[4/4] 客户端配置..."
# 2026-06-25 简化: VPBuddy 客户端不调 LLM, 不调 KB, 全部走 GPU server
# 不装 hermes-agent / sqlite-vec / sentence-transformers / KB 模型
# 客户端只做: 音频采集 + 上传 + SSE 订阅, 全部数据处理/存储/LLM 在 server 端

# 写一个轻量 client.yaml 记录 GPU server 地址 (供 Tauri 客户端读)
VPBUDDY_CLIENT_CONFIG="$HOME/.vpbuddy-client.yaml"
if [[ ! -f "$VPBUDDY_CLIENT_CONFIG" ]]; then
    cat > "$VPBUDDY_CLIENT_CONFIG" <<'EOF'
# VPBuddy 客户端配置 (2026-06-25 极简版)
# 客户端不调 LLM, 不调 KB, 仅记录 GPU server 地址
# 跨会议检索 / LLM 摘要 / 6 文档生成 全部在 GPU server 端

gpu_server_url: http://192.168.10.63:8765  # 默认连开发 GPU server, VP 笔记本改成自己服务器地址

# 音频采集参数
audio:
  sample_rate: 16000
  chunk_seconds: 30
  overlap_seconds: 0

# SSE 客户端
sse:
  reconnect: true
  max_events_per_chunk: 50
EOF
    echo "  ✅ 客户端配置已写: $VPBUDDY_CLIENT_CONFIG"
else
    echo "  ✅ 客户端配置已存在(不动): $VPBUDDY_CLIENT_CONFIG"
fi

# ===== 收尾 =====
echo ""
echo "=================================================="
echo "  ✅ VP 桌面客户端安装完成 (无 LLM 依赖)"
echo "=================================================="
echo ""
echo "下一步 (任选, 看你用 Python 客户端还是 Tauri 客户端):"
echo ""
echo "  📌 Python 路径 1 (网页客户端):"
echo "    1. 激活 venv:        source $VENV_DIR/bin/activate"
echo "    2. 启动 UI:          vpbuddy ui --port 8765"
echo "    3. 浏览器打开:        http://localhost:8765"
echo ""
echo "  📌 Tauri 路径 2 (桌面客户端, 推荐):"
echo "    1. 编译:             cd vpbuddy-client && npm install && cd src-tauri && cargo build --release"
echo "    2. 启动:             ./target/release/vpbuddy-client"
echo "    3. 改 GPU server:    编辑 $VPBUDDY_CLIENT_CONFIG 或设 VPBUDDY_GPU_URL 环境变量"
echo "       (或从 GitHub Releases 下载预编译的 .msi / .dmg / .AppImage)"
echo ""
echo "  📌 LLM 流量 (VP Chat 等):"
echo "    全在 GPU server 端 vpbuddy-gpu conda env 跑, 客户端不需要"
echo "    想跑 VP Chat: 在 GPU server 上 hermes chat 即可"
echo ""
echo "详见 docs/部署/INSTALL.md §角色 B"
