#!/usr/bin/env bash
# install-client.sh — VP 桌面客户端一键部署 (2026-06-25 简化:不依赖本地 LLM)
#
# 目标:VP 在自己 Mac/笔记本上跑 vpbuddy ui + 音频采集
# 范围:macOS / Linux desktop,可有或无 GPU
#
# 关键变更 (2026-06-25):
#   - 移除 LLM API key 强制要求 (Tauri 客户端上传音频到 GPU server, LLM 全跑 server 端)
#   - Hermes config.yaml 改为占位 (不预设 provider, server URL 从 GPU_URL 环境变量读)
#   - .env 不再强求 MINIMAX_CN_API_KEY (空 .env 也能起 ui, 只 VP Chat 不能用)
#   - KB 预下载可选 (网络不通时跳过, 首次启动按需下)
#
# 用法:
#   1. 从 GitHub clone vpbuddy:  git clone https://github.com/zhangsheng377/vpbuddy.git ~/vpbuddy
#   2. 跑:  cd ~/vpbuddy && bash scripts/install-client.sh
#   3. (可选) 设 GPU server 地址:  export VPBUDDY_GPU_URL=http://192.168.10.63:8765
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

# ===== 3. 装 vpbuddy[audio] =====
echo "[3/6] 装 vpbuddy[audio]..."
# 2026-06-25 简化: VPBuddy 客户端本身不调 LLM, 不装 hermes-agent
# LLM 流量全在 GPU server 端 vpbuddy-gpu conda env 跑
# 用户若要 VP Chat, 直接在 GPU server 上用 hermes-agent 即可

# vpbuddy 装当前目录(用户要先 git clone)
# 兼容两种情况:(a) 从 github clone 的标准目录 (b) 开发时已 cd 进来
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPBUDDY_ROOT="$(dirname "$SCRIPT_DIR")"
echo "  VPBuddy 根目录: $VPBUDDY_ROOT"
pip install --quiet -e "${VPBUDDY_ROOT}[audio]"

# ===== 4. KB 依赖 (可选, server 端有 KB 客户端不必须) =====
echo "[4/6] 装 KB 依赖 (sqlite-vec + sentence-transformers, 可选)..."
pip install --quiet sqlite-vec sentence-transformers || echo "  ⚠️  KB 依赖装失败不影响主功能, 跨会议检索功能可能不能用"

# ===== 5. 预下载 KB embedding 模型 (网络好才下, 失败不阻塞) =====
echo "[5/6] 预下载 KB embedding 模型 (256MB, 可选)..."
python3 - <<'PYEOF' || echo "  ⚠️  KB 模型预下载失败 (可能需要翻墙/HF 镜像), 首次启动按需下载"
import os
try:
    from sentence_transformers import SentenceTransformer
    model_name = "paraphrase-multilingual-MiniLM-L12-v2"
    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
    print(f"  正在下载 {model_name} → {cache_dir} ...")
    m = SentenceTransformer(model_name, cache_folder=cache_dir)
    print(f"  ✅ 模型已下载,共 {len(m.get_sentence_embedding_dimension())} 维向量")
except Exception as e:
    print(f"  ⚠️  模型下载失败: {e}")
    print(f"  如需跨会议 KB 检索, 设 HF_ENDPOINT=https://hf-mirror.com 后重跑本步骤")
PYEOF

# ===== 6. 客户端配置 (无 LLM, 无 hermes) =====
echo "[6/6] 客户端配置..."
# 2026-06-25 简化: VPBuddy 客户端不调 LLM, 不装 hermes-agent, 不写 ~/.hermes/{config.yaml,.env}
# 客户端只做音频采集 + 上传 + SSE 订阅, LLM 全在 GPU server 端

# 写一个轻量 client.yaml 记录 GPU server 地址 (供 Tauri 客户端读)
VPBUDDY_CLIENT_CONFIG="$HOME/.vpbuddy-client.yaml"
if [[ ! -f "$VPBUDDY_CLIENT_CONFIG" ]]; then
    cat > "$VPBUDDY_CLIENT_CONFIG" <<'EOF'
# VPBuddy 客户端配置 (2026-06-25 简化版)
# 客户端不调 LLM, 仅记录 GPU server 地址

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
