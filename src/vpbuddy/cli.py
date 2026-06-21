"""VPBuddy CLI — 统一命令入口 (ADR-0009 落地)

VPBuddy 有自己的 UI/CLI,这是给 VP/用户/管理员/开发者用的。
Hermes TUI 是 Hermes 自己的 dev 工具,不是 VPBuddy 入口 — 别混了。

子命令:
  vpbuddy ui            # 启动 VPBuddy Web UI (:8765) — VP/用户开会用
  vpbuddy controller    # 启动后台 controller (7×24 跑 6 文档生成)
  vpbuddy transcribe    # 转写音频(调用 GPU 服务器)
  vpbuddy setup-gpu     # 装 GPU 模型(本地一次性)
  vpbuddy list          # 列出活跃会议
  vpbuddy version       # 打印版本
"""
from __future__ import annotations
import argparse
import sys
from typing import List, Optional


def cmd_ui(args: argparse.Namespace) -> int:
    """启动 VPBuddy Web UI(Browser-based, 端口 8765)"""
    from .ui_server import main as ui_main
    extra = ["--port", str(args.port), "--host", args.host]
    return ui_main(extra) or 0


def cmd_controller(args: argparse.Namespace) -> int:
    """启动后台 controller(7×24 跑 6 文档生成)"""
    from .sub_session_controller import main as controller_main
    # 透传剩余参数给 sub_session_controller.main()(它自己的 argparse)
    extra = []
    if args.once:
        extra.append("--once")
    if args.meeting:
        extra.extend(["--meeting", args.meeting])
    if args.dry_run:
        extra.append("--dry-run")
    return controller_main(extra) or 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    """转写音频(调用 GPU 服务器上的 Whisper + pyannote)"""
    import subprocess
    cmd = [
        sys.executable, "-m", "vpbuddy.scripts.gpu_transcribe",
        args.audio_file,
        "-o", args.output,
    ]
    return subprocess.call(cmd)


def cmd_setup_gpu(args: argparse.Namespace) -> int:
    """装 GPU 模型(本地一次性)"""
    import subprocess
    cmd = [sys.executable, "-m", "vpbuddy.scripts.setup_gpu"]
    return subprocess.call(cmd)


def cmd_list(args: argparse.Namespace) -> int:
    """列出活跃会议"""
    from .sub_session_controller import list_active_meetings
    meetings = list_active_meetings()
    print(f"Active meetings ({len(meetings)}):")
    for m in meetings:
        print(f"  - {m}")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """打印版本"""
    from . import __version__
    print(f"vpbuddy {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 解析器"""
    parser = argparse.ArgumentParser(
        prog="vpbuddy",
        description="VPBuddy — 人机协同会议操作系统级 AI 助手 (运行在 Hermes Agent 之上)",
        epilog="更多帮助: vpbuddy <command> --help",
    )
    sub = parser.add_subparsers(dest="command", required=True, help="子命令")

    # ui
    p_ui = sub.add_parser("ui", help="启动 VPBuddy Web UI (:8765) — VP/用户开会用")
    p_ui.add_argument("--port", type=int, default=8765, help="端口(默认 8765)")
    p_ui.add_argument("--host", default="0.0.0.0", help="绑定 host(默认 0.0.0.0)")
    p_ui.set_defaults(func=cmd_ui)

    # controller
    p_ctrl = sub.add_parser("controller", help="启动后台 controller (7×24 跑 6 文档生成)")
    p_ctrl.add_argument("--once", action="store_true", help="只跑一轮就退出")
    p_ctrl.add_argument("--meeting", help="只跑指定会议 ID")
    p_ctrl.add_argument("--dry-run", action="store_true", help="只渲染 prompt,不真触发")
    p_ctrl.set_defaults(func=cmd_controller)

    # transcribe
    p_tx = sub.add_parser("transcribe", help="转写音频(调用 GPU 服务器)")
    p_tx.add_argument("audio_file", help="音频文件路径")
    p_tx.add_argument("-o", "--output", default="transcript.json", help="输出文件")
    p_tx.set_defaults(func=cmd_transcribe)

    # setup-gpu
    p_gpu = sub.add_parser("setup-gpu", help="装 GPU 模型(本地一次性)")
    p_gpu.set_defaults(func=cmd_setup_gpu)

    # list
    p_list = sub.add_parser("list", help="列出活跃会议")
    p_list.set_defaults(func=cmd_list)

    # version
    p_ver = sub.add_parser("version", help="打印版本")
    p_ver.set_defaults(func=cmd_version)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主入口 — `vpbuddy` 命令"""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
