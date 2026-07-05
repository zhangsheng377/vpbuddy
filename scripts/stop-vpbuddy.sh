#!/usr/bin/env bash
# stop-vpbuddy.sh — 停止 VPBuddy UI
#
# v0.9.0: controller 已删除, 只停 UI
#
# 用法:
#   bash scripts/stop-vpbuddy.sh           # 停 UI
#   bash scripts/stop-vpbuddy.sh ui         # 明确指定 UI
set -euo pipefail

stop_ui() {
    local pidfile="/tmp/vpbuddy_ui.pid"
    if [[ ! -f "$pidfile" ]]; then
        echo "⚠️  UI PID 文件不存在: $pidfile"
        return 0
    fi
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        sleep 2
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" || true
            echo "✅ UI 强杀 PID $pid"
        else
            echo "✅ UI 优雅停止 PID $pid"
        fi
    else
        echo "⚠️  UI PID $pid 已不在跑"
    fi
    rm -f "$pidfile"
}

COMPONENT="${1:-all}"
case "$COMPONENT" in
    ui|all|"") stop_ui ;;
    *) echo "用法: $0 [ui|all]"; exit 1 ;;
esac

echo ""
echo "验证无残留:"
ps -ef | grep -E 'vpbuddy ui' | grep -v grep || echo "✅ 已全部停止"
