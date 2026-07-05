"""VPBuddy CLI — 统一命令入口 (ADR-0009 落地)

VPBuddy 有自己的 UI/CLI,这是给 VP/用户/管理员/开发者用的。
Hermes TUI 是 Hermes 自己的 dev 工具,不是 VPBuddy 入口 — 别混了。

子命令:
  vpbuddy ui            # 启动 VPBuddy Web UI (:8765) — VP/用户开会用
  vpbuddy transcribe    # 转写音频(调用 GPU 服务器)
  vpbuddy setup-gpu     # 装 GPU 模型(本地一次性)
  vpbuddy list          # 列出活跃会议
  vpbuddy version       # 打印版本

v0.9.0: controller 子命令已移除 — 文档生成由 _close_meeting 通过 task_manager 触发。
"""
from __future__ import annotations

import argparse
import sys


def cmd_ui(args: argparse.Namespace) -> int:
    """启动 VPBuddy Web UI(Browser-based, 端口 8765) — 默认 FastAPI"""
    from .server.fastapi_app import main as fastapi_main
    extra = ["--port", str(args.port), "--host", args.host]
    return fastapi_main(extra) or 0



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


def cmd_kb_status(args: argparse.Namespace) -> int:
    """打印 KB 写入状态(2026-06-22 新增)

    给运维 / VP 看哪些会议文档已经进 KB、哪些还在队列、哪些失败。
    """
    from .sub_session_controller import get_kb_status
    data = get_kb_status(meeting_id=args.meeting)
    summary = data["summary"]
    items = data["items"]

    print("KB 状态摘要:")
    print(f"  total:    {summary.get('total', 0)}")
    print(f"  stored:   {summary.get('stored', 0)}")
    print(f"  queued:   {summary.get('queued', 0)}")
    print(f"  retrying: {summary.get('retrying', 0)}")
    print(f"  failed:   {summary.get('failed', 0)}")
    if items:
        print("\n详情:")
        for it in items:
            err = f" [{it['error'][:50]}]" if it.get("error") else ""
            print(f"  - {it['meeting_id']}/{it['doc_kind']}: {it['status']} (attempts={it.get('attempts', 0)}){err}")
    # failed > 0 时 exit 2 (给 cron / 监控用)
    if summary.get("failed", 0) > 0:
        return 2
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

    # kb-status (2026-06-22)
    p_kbs = sub.add_parser("kb-status", help="查 KB 写入状态(stored/failed/queued)")
    p_kbs.add_argument("--meeting", help="只看某个会议")
    p_kbs.set_defaults(func=cmd_kb_status)

    # version
    p_ver = sub.add_parser("version", help="打印版本")
    p_ver.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口 — `vpbuddy` 命令"""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
