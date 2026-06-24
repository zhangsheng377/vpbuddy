#!/usr/bin/env python3
"""VPBuddy 无头端到端测试服务端。

用途:
- 作为独立进程启动 VPBuddy UI server
- 在进程内安装 fake ASR 和 fake 文档生成器
- 让另一个独立进程运行 headless_client.py 做真实 HTTP/SSE 端到端测试

运行:
    PYTHONPATH=src python src/tests/headless_test_server.py --host 127.0.0.1 --port 18767
"""
from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy import ui_server


def install_fakes(docs_dir: Path) -> None:
    """安装 fake ASR 和 fake 文档生成器，避免测试依赖 GPU/funasr/hermes。"""
    fake_gpu = types.ModuleType("vpbuddy.scripts.gpu_transcribe")

    def fake_process(_path: str) -> dict:
        return {
            "segments": [
                {
                    "start_sec": 0.5,
                    "end_sec": 1.6,
                    "text": "必须实时展示会议转写和六类文档",
                    "speaker_id": "SPEAKER_00",
                },
                {
                    "start_sec": 2.0,
                    "end_sec": 3.2,
                    "text": "需要支持 Demo 实时预览",
                    "speaker_id": "SPEAKER_01",
                },
            ],
            "num_speakers": 2,
        }

    fake_gpu.process = fake_process
    sys.modules["vpbuddy.scripts.gpu_transcribe"] = fake_gpu

    import vpbuddy.sub_session_controller as sub_session_controller

    sub_session_controller.DOCS_DIR = docs_dir

    def fake_trigger(meeting_id: str, doc_kind: str, dry_run: bool = False) -> dict:
        doc_path = sub_session_controller.get_doc_path(meeting_id, doc_kind)
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        if doc_kind == "demo":
            content = "<!doctype html><html><body><h1>VPBuddy Demo</h1><p>独立进程端到端测试通过</p></body></html>"
        else:
            content = f"# {doc_kind} 文档\n\n- 来自独立进程无头端到端测试\n"
        doc_path.write_text(content, encoding="utf-8")

        from vpbuddy.realtime_server import push_event

        push_event(
            meeting_id,
            "doc-update",
            {
                "meeting_id": meeting_id,
                "kind": doc_kind,
                "status": "stored",
                "doc_size": len(content.encode("utf-8")),
                "content": content,
                "is_demo": doc_kind == "demo",
            },
        )
        return {
            "triggered": True,
            "doc_path": str(doc_path),
            "doc_size": len(content.encode("utf-8")),
        }

    sub_session_controller.trigger_sub_session = fake_trigger


def main() -> int:
    parser = argparse.ArgumentParser(description="VPBuddy 无头测试服务端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18767)
    parser.add_argument("--data-dir", default="/tmp/vpbuddy_headless_proc_data")
    parser.add_argument("--docs-dir", default="/tmp/vpbuddy_headless_proc_docs")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    docs_dir = Path(args.docs_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    ui_server.DATA_DIR = data_dir
    ui_server.DOCS_DIR = docs_dir
    install_fakes(docs_dir)
    return ui_server.main(["--host", args.host, "--port", str(args.port)])


if __name__ == "__main__":
    raise SystemExit(main())
