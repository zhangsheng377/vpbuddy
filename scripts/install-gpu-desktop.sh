#!/bin/bash
# VPBuddy 桌面客户端 GUI 联调脚本 (2026-06-24)
# 用途: 在 GPU 服务器 (192.168.10.63) 上启用虚拟 X11 + VNC,
#       让 Tauri 客户端 binary 能在 GPU 端真跑起来 (无须 VP 笔记本)
#
# 使用:
#   1. ssh zsd@192.168.10.63
#   2. sudo bash scripts/install-gpu-desktop.sh
#   3. ./scripts/start-vpbuddy-gui.sh    # 在本地 user 跑 (不需 sudo)
#   4. Mac/Win VNC viewer → 192.168.10.63:5900 (密码 123456)

set -euo pipefail

echo "=== VPBuddy 桌面客户端 GUI 联调环境安装 ==="

# 检测包管理器
if command -v apt-get &>/dev/null; then
    PKG="apt-get"
    echo "检测到 Debian/Ubuntu, 用 apt"
elif command -v dnf &>/dev/null; then
    PKG="dnf"
    echo "检测到 Fedora/RHEL, 用 dnf"
elif command -v pacman &>/dev/null; then
    PKG="pacman"
    echo "检测到 Arch, 用 pacman"
else
    echo "错误: 未识别包管理器"
    exit 1
fi

echo "[1/4] 装 Xvfb (虚拟 framebuffer)"
case $PKG in
    apt-get) sudo apt-get install -y xvfb ;;
    dnf)     sudo dnf install -y xorg-x11-server-Xvfb ;;
    pacman)  sudo pacman -S --noconfirm xorg-server-xvfb ;;
esac

echo "[2/4] 装 x11vnc (VNC server, 让远端 viewer 能看)"
case $PKG in
    apt-get) sudo apt-get install -y x11vnc ;;
    dnf)     sudo dnf install -y x11vnc ;;
    pacman)  sudo pacman -S --noconfirm x11vnc ;;
esac

echo "[3/4] 装 X11 工具 + 字体 (GTK 应用必需)"
case $PKG in
    apt-get) sudo apt-get install -y xauth xinit fonts-dejavu-core ;;
    dnf)     sudo dnf install -y xauth xinit dejavu-fonts ;;
    pacman)  sudo pacman -S --noconfirm xorg-xauth xorg-xinit ttf-dejavu ;;
esac

echo "[4/4] 装 alsa 音频 (cpal 必需, 已装过的话跳过)"
case $PKG in
    apt-get) sudo apt-get install -y libasound2-dev ;;
    dnf)     sudo dnf install -y alsa-lib-devel ;;
    pacman)  sudo pacman -S --noconfirm alsa-lib ;;
esac

echo ""
echo "=== 安装完成 ==="
echo ""
echo "下一步:"
echo "  1. VNC 密码: mkdir -p ~/.vnc && x11vnc -storepasswd 123456 ~/.vnc/passwd"
echo "  2. 启动 GUI: bash scripts/start-vpbuddy-gui.sh"
echo "  3. Mac/Win VNC viewer → 192.168.10.63:5900"
echo ""
echo "VNC 端口默认 5900, 可通过 \$DISPLAY=:0 或 :1 切换"