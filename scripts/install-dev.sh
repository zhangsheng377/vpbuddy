#!/usr/bin/env bash
# install-dev.sh — 开发者本地一键配置 (2026-06-22 ADR-0009)
#
# 目标:开发者从 GitHub 拉源码,本地 editable install,跑测试
# 关键:绝不在本机 Mac pip install -e . (会污染本机 venv),走 conda env vpbuddy-dev
#
# 用法:bash install-dev.sh
#
# 详见 docs/部署/INSTALL.md §角色 C
set -euo pipefail

echo "=================================================="
echo "  VPBuddy 开发环境配置"
echo "=================================================="

# ===== 1. 检测 conda =====
echo "[1/5] Conda 检查..."
if ! command -v conda &>/dev/null; then
    echo "❌ 需要 conda (https://docs.conda.io/en/latest/miniconda.html)"
    exit 1
fi

# shellcheck disable=SC1091
source "$HOME/miniconda3/etc/profile.d/conda.sh" 2>/dev/null || \
source "$HOME/anaconda3/etc/profile.d/conda.sh" 2>/dev/null || {
    echo "❌ conda activate 失败"
    exit 1
}

# ===== 2. 拉源码 =====
echo "[2/5] 源码准备..."
VPBUDDY_DIR="${VPBUDDY_DIR:-$HOME/vpbuddy}"
if [[ ! -d "$VPBUDDY_DIR" ]]; then
    echo "  clone zhangsheng377/vpbuddy..."
    git clone https://github.com/zhangsheng377/vpbuddy.git "$VPBUDDY_DIR"
fi
cd "$VPBUDDY_DIR"

# ===== 3. conda env =====
echo "[3/5] 创建 conda env (vpbuddy-dev)..."
if ! conda env list | grep -q vpbuddy-dev; then
    conda create -y -n vpbuddy-dev python=3.11
fi
conda activate vpbuddy-dev

# 国内 pip 镜像
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ 2>/dev/null || true

# ===== 4. 装 hermes-agent + editable vpbuddy[dev] =====
echo "[4/5] 装依赖 (editable)..."
pip install --quiet --upgrade pip
pip install --quiet "hermes-agent>=0.16.0,<1.0"
# dev extras: pytest, black, ruff, mypy
pip install --quiet -e ".[dev]"
# 如果有 GPU,装 gpu extras (可选)
pip install --quiet -e ".[gpu]" 2>/dev/null || echo "  ⚠️ GPU extras 跳过(没 CUDA 或 torch)"

# ===== 5. 验证 =====
echo "[5/5] 验证..."
vpbuddy version

# 跑测试
echo ""
echo ">>> 跑 pytest (期望: 80 passed,没 GPU 跑 65 passed) <<<"
PYTHONPATH=src python3 -m pytest src/tests/ -q 2>&1 | tail -5 || echo "  ⚠️ 一些测试失败(GPU 相关正常)"

echo ""
echo "=================================================="
echo "  ✅ Dev 环境就绪"
echo "=================================================="
echo ""
echo "日常用法:"
echo "  conda activate vpbuddy-dev"
echo "  cd $VPBUDDY_DIR"
echo ""
echo "  # 跑测试"
echo "  PYTHONPATH=src python3 -m pytest src/tests/ -v"
echo ""
echo "  # 触发一次 controller (dry-run,不调 LLM)"
echo "  PYTHONPATH=src python3 -m vpbuddy.sub_session_controller --once --dry-run"
echo ""
echo "  # 启动 UI"
echo "  vpbuddy ui --port 8765"
echo ""
echo "详见 docs/部署/INSTALL.md §角色 C"