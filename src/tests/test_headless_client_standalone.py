#!/usr/bin/env python3
"""无头客户端端到端测试。

运行:
    PYTHONPATH=src python src/tests/test_headless_client_standalone.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from headless_client import HeadlessVPBuddyClient
from vpbuddy import ui_server


TEST_HOST = "127.0.0.1"
TEST_PORT = 18766
TEST_DATA_DIR = Path("/tmp/vpbuddy_headless_data")
TEST_DOCS_DIR = Path("/tmp/vpbuddy_headless_docs")


def install_fakes() -> None:
    """安装 fake ASR 和 fake 文档生成器，让无头测试不依赖 numpy/funasr/hermes。"""
    fake_run_agent = types.ModuleType("run_agent")

    class FakeAIAgent:
        def __init__(self, session_id: str, **_kwargs):
            self.session_id = session_id

        def chat(self, _prompt: str) -> str:
            return f"[fake hermes:{self.session_id}] Demo 方向调整已收到,建议同步更新 demo 子 agent。"

    fake_run_agent.AIAgent = FakeAIAgent
    sys.modules["run_agent"] = fake_run_agent

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
    sub_session_controller.DOCS_DIR = TEST_DOCS_DIR

    def fake_trigger(meeting_id: str, doc_kind: str, dry_run: bool = False) -> dict:
        doc_path = sub_session_controller.get_doc_path(meeting_id, doc_kind)
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        if doc_kind == "demo":
            content = "<!doctype html><html><body><h1>VPBuddy Demo</h1><p>实时预览正常</p></body></html>"
        else:
            content = f"# {doc_kind} 文档\n\n- 来自无头客户端测试\n"
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
        return {"triggered": True, "doc_path": str(doc_path), "doc_size": len(content.encode("utf-8"))}

    sub_session_controller.trigger_sub_session = fake_trigger


def setup_server() -> str:
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEST_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ui_server.DATA_DIR = TEST_DATA_DIR
    ui_server.DOCS_DIR = TEST_DOCS_DIR

    install_fakes()
    t = threading.Thread(
        target=ui_server.main,
        args=(["--host", TEST_HOST, "--port", str(TEST_PORT)],),
        daemon=True,
    )
    t.start()
    time.sleep(1)
    return f"http://{TEST_HOST}:{TEST_PORT}"


def test_headless_client_full_flow(server: str) -> None:
    print("\n[Test] 无头客户端完整链路...")
    client = HeadlessVPBuddyClient(server)
    try:
        result = client.run_smoke(
            chunks=1,
            chunk_duration_sec=1.0,
            chat_message="把 Demo 改成面向企业管理员的后台视角",
        )
        event_types = [event["event"] for event in result["events"]]
        chunk = result["chunk_responses"][0]
        docs = result["docs"]["docs"]
        chat = result["chat_response"]

        assert result["meeting_id"].startswith("STREAM_"), result["meeting_id"]
        assert len(chunk["new_segments"]) == 2, json.dumps(chunk, ensure_ascii=False)
        assert "connected" in event_types, event_types
        assert "transcript-segment" in event_types, event_types
        assert "state-update" in event_types, event_types
        assert "metrics-update" in event_types, event_types
        assert "doc-update" in event_types, event_types
        assert "chat-message" in event_types, event_types
        assert result["state"]["state"], result["state"]
        assert len(docs) == 6, docs
        assert any(doc["kind"] == "demo" and "VPBuddy Demo" in doc["content"] for doc in docs), docs
        assert chat["source"] == "hermes", chat
        assert len(result["chat_history"]["messages"]) >= 2, result["chat_history"]
        print(f"  PASS: meeting={result['meeting_id']}, events={event_types}")
    finally:
        client.stop()


def main() -> int:
    print("=" * 60)
    print("VPBuddy 无头客户端端到端测试")
    print("=" * 60)
    server = setup_server()
    print(f"测试服务器: {server}")
    try:
        test_headless_client_full_flow(server)
        print("\n所有无头客户端测试通过!")
        return 0
    except AssertionError as e:
        print(f"\n测试失败: {e}")
        return 1
    except Exception as e:
        print(f"\n测试异常: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
