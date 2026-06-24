#!/bin/bash
# 启动 VPBuddy 桌面客户端 (Xvfb + x11vnc + Tauri)
# 跑在 GPU 服务器, VNC viewer 远端看
#
# 用法:
#   bash scripts/start-vpbuddy-gui.sh [display]
#   默认 display=:99 (5900 端口)

set -euo pipefail

DISPLAY_NUM="${1:-99}"
VNC_PORT=$((5900 + DISPLAY_NUM))
VPBUDDY_BIN="/home/zsd/vpbuddy/vpbuddy-client/src-tauri/target/release/vpbuddy-client"

# 1. 启动 Xvfb (虚拟 framebuffer)
if ! pgrep -f "Xvfb :$DISPLAY_NUM" >/dev/null; then
    echo "[1/3] 启动 Xvfb :$DISPLAY_NUM"
    Xvfb :$DISPLAY_NUM -screen 0 1280x720x24 &
    sleep 2
fi

# 2. 启动 x11vnc
if ! pgrep -f "x11vnc.*:$DISPLAY_NUM" >/dev/null; then
    echo "[2/3] 启动 x11vnc on :$VNC_PORT"
    if [ ! -f ~/.vnc/passwd ]; then
        mkdir -p ~/.vnc
        x11vnc -storepasswd 123456 ~/.vnc/passwd
    fi
    x11vnc -display :$DISPLAY_NUM -rfbauth ~/.vnc/passwd -forever -bg -o ~/.vnc/x11vnc.log
    sleep 1
fi

# 3. 启动 Tauri 客户端
if [ ! -f "$VPBUDDY_BIN" ]; then
    echo "错误: $VPBUDDY_BIN 不存在"
    echo "先编译: cd /home/zsd/vpbuddy/vpbuddy-client/src-tauri && cargo build --release"
    exit 1
fi

echo "[3/3] 启动 vpbuddy-client (DISPLAY=:$DISPLAY_NUM)"
DISPLAY=:$DISPLAY_NUM VPBUDDY_GPU_URL="${VPBUDDY_GPU_URL:-http://localhost:8765}" \
    "$VPBUDDY_BIN" 2>&1 | tee /tmp/vpbuddy-client.log

echo ""
echo "=== 提示 ==="
echo "Mac/Win VNC viewer: 192.168.10.63:$VNC_PORT  密码: 123456"
echo "或 SSH X11 forward: ssh -X zsd@192.168.10.63"