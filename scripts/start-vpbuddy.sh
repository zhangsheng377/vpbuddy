#!/usr/bin/env bash
# start-vpbuddy.sh — VPBuddy 官方启动脚本 (2026-06-23 ADR-0011 落地)
#
# 启动 UI (FastAPI, :8765)
# 自动设置 HF 离线铁律环境变量,无需手动 export
#
# v0.9.0: controller 已删除 — 文档生成由 _close_meeting 通过 task_manager 触发
#
# 用法:
#   bash scripts/start-vpbuddy.sh           # 启动 UI
#   bash scripts/start-vpbuddy.sh ui         # 明确指定 UI
#
# 停止:
#   bash scripts/stop-vpbuddy.sh
#
# 详见 docs/decisions/0011-HF模型离线铁律.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPBUDDY_ROOT="$(dirname "$SCRIPT_DIR")"
CONDA_ENV="${VPBUDDY_CONDA_ENV:-vpbuddy-gpu}"
LOG_DIR="$VPBUDDY_ROOT/logs"
mkdir -p "$LOG_DIR"

# ===== 1. 环境变量铁律 (ADR-0011) =====
# 国内 huggingface.co 被墙,启动时强制走本地 cache + hf-mirror 镜像
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export PYANNOTE_LOCAL_DIR="${PYANNOTE_LOCAL_DIR:-$HOME/pyannote_models}"

echo "==================================================="
echo "  VPBuddy 启动"
echo "==================================================="
echo "  VPBUDDY_ROOT:    $VPBUDDY_ROOT"
echo "  CONDA_ENV:       $CONDA_ENV"
echo "  LOG_DIR:         $LOG_DIR"
echo "  HF_ENDPOINT:     $HF_ENDPOINT"
echo "  HF_HUB_OFFLINE:  $HF_HUB_OFFLINE"
echo "  PYANNOTE_LOCAL:  $PYANNOTE_LOCAL_DIR"
echo ""

# ===== 2. 激活 conda env =====
if ! command -v conda &>/dev/null; then
    if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
        source "$HOME/miniconda3/etc/profile.d/conda.sh"
    else
        echo "❌ conda 未找到,装 Miniconda:bash scripts/install-gpu-server.sh"
        exit 1
    fi
fi
conda activate "$CONDA_ENV"

cd "$VPBUDDY_ROOT"

# ===== 3. 启动 UI (默认 FastAPI, --legacy 回退) =====
start_ui() {
    if [[ -f /tmp/vpbuddy_ui.pid ]] && kill -0 "$(cat /tmp/vpbuddy_ui.pid)" 2>/dev/null; then
        echo "⚠️  UI 已在跑 (PID $(cat /tmp/vpbuddy_ui.pid))"
        return 0
    fi
    nohup vpbuddy ui --port 8765 > "$LOG_DIR/ui.log" 2>&1 &
    echo $! > /tmp/vpbuddy_ui.pid
    echo "✅ UI 启动 PID $(cat /tmp/vpbuddy_ui.pid)"
    echo "   日志: $LOG_DIR/ui.log"
    sleep 5
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/ | grep -q 200; then
        echo "   ✅ HTTP 200 ready"
    else
        echo "   ⚠️  HTTP 未就绪,查 $LOG_DIR/ui.log"
    fi
}

# v0.9.0: controller 已删除 — 文档生成由 _close_meeting 通过 task_manager 触发
COMPONENT="${1:-all}"
case "$COMPONENT" in
    ui|all|"")
        start_ui
        ;;
    all|"")
        start_ui
        ;;
    *)
        echo "用法: $0 [controller|ui|all]"
        exit 1
        ;;
esac

echo ""
echo "==================================================="
echo "  验证"
echo "==================================================="
ps -ef | grep -E 'vpbuddy ui' | grep -v grep | awk '{print $2, $8, $9, $10, $11}' || true
echo ""
echo "UI:    http://localhost:8765/"
echo "停止:  bash scripts/stop-vpbuddy.sh"
