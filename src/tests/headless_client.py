#!/usr/bin/env python3
"""VPBuddy 无头客户端。

用途:
- 模拟桌面客户端完整链路: stream_start -> SSE -> state/docs 查询
- 不依赖 Tauri、GUI、麦克风或系统音频设备
- 可用于本地端到端测试和 CI smoke test

运行示例:
    PYTHONPATH=src python src/tests/headless_client.py --server http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SseEvent:
    event: str
    data: Any
    event_id: Optional[str] = None


@dataclass
class HeadlessVPBuddyClient:
    server: str
    meeting_id: Optional[str] = None
    events: list[SseEvent] = field(default_factory=list)
    last_event_id: Optional[str] = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def start_meeting(self) -> str:
        body = self._post_json("/api/meetings/stream_start", {"platform": "headless_client"})
        self.meeting_id = body["meeting_id"]
        return self.meeting_id

    def connect_sse(self) -> None:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._sse_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def get_state(self) -> dict:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        return self._get_json(f"/api/meetings/{self.meeting_id}/state")

    def get_docs(self) -> dict:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        return self._get_json(f"/api/meetings/{self.meeting_id}/docs")

    def send_chat(self, message: str, context: Optional[dict[str, Any]] = None) -> dict:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        return self._post_json(
            f"/api/meetings/{self.meeting_id}/chat",
            {"message": message, "context": context or {"source": "headless_client"}},
        )

    def get_chat_history(self) -> dict:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        return self._get_json(f"/api/meetings/{self.meeting_id}/chat/history")

    def wait_for_events(self, required: set[str], timeout_sec: float = 10.0) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            seen = {event.event for event in self.events}
            if required.issubset(seen):
                return True
            time.sleep(0.1)
        return False

    def run_smoke(self, chat_message: Optional[str] = None) -> dict:
        meeting_id = self.start_meeting()
        self.connect_sse()
        time.sleep(0.3)

        self.wait_for_events({"connected"}, timeout_sec=3)
        chat_response = None
        if chat_message:
            chat_response = self.send_chat(chat_message)
            self.wait_for_events({"chat-message"}, timeout_sec=5)
        state = self.get_state()
        docs = self.get_docs()
        chat_history = self.get_chat_history()
        return {
            "meeting_id": meeting_id,
            "chat_response": chat_response,
            "chat_history": chat_history,
            "events": [{"event": e.event, "data": e.data, "id": e.event_id} for e in self.events],
            "state": state,
            "docs": docs,
        }

    def _sse_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._read_sse_once()
            except Exception:
                if not self._stop.is_set():
                    time.sleep(0.5)

    def _read_sse_once(self) -> None:
        assert self.meeting_id
        parsed = urllib.request.urlparse(self.server)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = f"/api/meetings/{self.meeting_id}/events"
        if self.last_event_id:
            path += f"?last_event_id={urllib.request.quote(self.last_event_id)}"

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.sendall(request.encode())

        buf = b""
        while b"\r\n\r\n" not in buf:
            data = sock.recv(1024)
            if not data:
                break
            buf += data
        body = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""

        sock.settimeout(1)
        try:
            while not self._stop.is_set():
                while b"\n\n" in body:
                    raw, body = body.split(b"\n\n", 1)
                    event = self._parse_sse_event(raw.decode("utf-8", errors="replace"))
                    if event:
                        self.events.append(event)
                        if event.event_id:
                            self.last_event_id = event.event_id
                try:
                    data = sock.recv(2048)
                    if not data:
                        break
                    body += data
                except socket.timeout:
                    continue
        finally:
            sock.close()

    @staticmethod
    def _parse_sse_event(raw: str) -> Optional[SseEvent]:
        event_type = "message"
        event_id = None
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("id: "):
                event_id = line[4:].strip()
            elif line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        if not data_lines:
            return None
        data_text = "\n".join(data_lines)
        try:
            data = json.loads(data_text)
        except json.JSONDecodeError:
            data = data_text
        return SseEvent(event=event_type, data=data, event_id=event_id)

    def _url(self, path: str) -> str:
        return self.server.rstrip("/") + path

    def _post_json(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def _get_json(self, path: str) -> dict:
        with urllib.request.urlopen(self._url(path), timeout=10) as resp:
            return json.loads(resp.read().decode())

def main() -> int:
    parser = argparse.ArgumentParser(description="VPBuddy 无头客户端 smoke test")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="VPBuddy 服务端地址")
    parser.add_argument("--chat", default="", help="发送一条 VP Chat 消息")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    client = HeadlessVPBuddyClient(args.server)
    try:
        result = client.run_smoke(
            chat_message=args.chat or None,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            event_types = [e["event"] for e in result["events"]]
            print(f"meeting_id={result['meeting_id']}")
            print(f"events={event_types}")
            print(f"docs={len(result['docs'].get('docs', []))}")
            if result.get("chat_response"):
                print(f"chat_status={result['chat_response'].get('status')}")
        return 0
    finally:
        client.stop()


if __name__ == "__main__":
    raise SystemExit(main())
