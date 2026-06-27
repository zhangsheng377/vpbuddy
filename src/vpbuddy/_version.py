"""VPBuddy 版本信息 — CI/release 时由 build 脚本注入

读取优先级:
1. 环境变量 VPBUDDY_VERSION (release 时注入)
2. 本地 git describe (开发时, 自动含 commit hash)

Refs: ADR-0017, 2026-06-28 张胜东 "客户端/服务端 log 打印版本号,
一眼看出是否最新"
"""
import os
import subprocess

__version__ = os.environ.get("VPBUDDY_VERSION")
if not __version__:
    try:
        # git describe --tags --always --dirty: tag 优先, 没 tag 显示 commit hash, dirty 加 -modified
        __version__ = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty=-modified"],
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        __version__ = "unknown"