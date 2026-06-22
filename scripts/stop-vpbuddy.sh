#!/usr/bin/env bash
# stop-vpbuddy.sh — 停止 VPBuddy controller + UI (2026-06-23)
#
# 用法:
#   bash scripts/stop-vpbuddy.sh           # 停 controller + UI
#   bash scripts/stop-vpbuddy.sh controller # 只停 controller
#   bash scripts/stop-vpbuddy.sh ui         # 只停 UI
set -euo pipefail

stop_component() {
    local name="$1"
    local pidfile="/tmp/vpbuddy_${name}.pid"
    if [[ ! -f "$pidfile" ]]; then
        echo "⚠️  $name PID 文件不存在: $pidfile"
        return 0
    fi
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        sleep 2
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" || true
            echo "✅ $name 强杀 PID $pid"
        else
            echo "✅ $name 优雅停止 PID $pid"
        fi
    else
        echo "⚠️  $name PID $pid 已不在跑"
    fi
    rm -f "$pidfile"
}

COMPONENT="${1:-all}"
case "$COMPONENT" in
    controller) stop_component controller ;;
    ui) stop_component ui ;;
    all|"") stop_component controller; stop_component ui ;;
    *) echo "用法: $0 [controller|ui|all]"; exit 1 ;;
esac

echo ""
echo "验证无残留:"
ps -ef | grep -E 'vpbuddy (controller|ui)' | grep -v grep || echo "✅ 已全部停止"
