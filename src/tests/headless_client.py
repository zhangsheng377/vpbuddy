#!/usr/bin/env python3
"""VPBuddy 无头客户端。

用途:
- 模拟桌面客户端完整链路: stream_start -> SSE -> stream_chunk -> state/docs 查询
- 不依赖 Tauri、GUI、麦克风或系统音频设备
- 可用于本地端到端测试和 CI smoke test

运行示例:
    PYTHONPATH=src python src/tests/headless_client.py --server http://127.0.0.1:8765 --chunks 2
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


def make_wav(duration_sec: float = 1.0, sample_rate: int = 16000, tone_hz: float = 440.0) -> bytes:
    """生成 16kHz mono i16 WAV。duration 很短即可触发服务端测试链路。"""
    n = int(sample_rate * duration_sec)
    samples = bytearray()
    for i in range(n):
        v = int(1200 * math.sin(2 * math.pi * tone_hz * i / sample_rate))
        samples.extend(v.to_bytes(2, "little", signed=True))

    data_size = len(samples)
    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[4:8] = (36 + data_size).to_bytes(4, "little")
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    header[16:20] = (16).to_bytes(4, "little")
    header[20:22] = (1).to_bytes(2, "little")
    header[22:24] = (1).to_bytes(2, "little")
    header[24:28] = sample_rate.to_bytes(4, "little")
    header[28:32] = (sample_rate * 2).to_bytes(4, "little")
    header[32:34] = (2).to_bytes(2, "little")
    header[34:36] = (16).to_bytes(2, "little")
    header[36:40] = b"data"
    header[40:44] = data_size.to_bytes(4, "little")
    return bytes(header) + bytes(samples)


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

    def upload_chunk(
        self,
        wav_data: bytes,
        chunk_index: int,
        chunk_start_sec: float,
        overlap_sec: float = 2.0,
    ) -> dict:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        fields = {
            "chunk_index": str(chunk_index),
            "chunk_start_sec": f"{chunk_start_sec:.3f}",
            "overlap_sec": f"{overlap_sec:.3f}",
            "client_sent_at": f"{time.time():.3f}",
        }
        return self._upload_multipart(
            f"/api/meetings/{self.meeting_id}/stream_chunk",
            wav_data,
            fields,
        )

    def get_state(self) -> dict:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        return self._get_json(f"/api/meetings/{self.meeting_id}/state")

    def get_docs(self) -> dict:
        if not self.meeting_id:
            raise RuntimeError("请先调用 start_meeting()")
        return self._get_json(f"/api/meetings/{self.meeting_id}/docs")

    def wait_for_events(self, required: set[str], timeout_sec: float = 10.0) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            seen = {event.event for event in self.events}
            if required.issubset(seen):
                return True
            time.sleep(0.1)
        return False

    def run_smoke(self, chunks: int = 1, chunk_duration_sec: float = 1.0) -> dict:
        meeting_id = self.start_meeting()
        self.connect_sse()
        time.sleep(0.3)

        responses = []
        for i in range(chunks):
            wav = make_wav(duration_sec=chunk_duration_sec, tone_hz=440.0 + i * 30)
            responses.append(self.upload_chunk(wav, i, i * 28.0, overlap_sec=2.0))

        self.wait_for_events({"connected"}, timeout_sec=3)
        state = self.get_state()
        docs = self.get_docs()
        return {
            "meeting_id": meeting_id,
            "chunk_responses": responses,
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

    def _upload_multipart(self, path: str, wav_data: bytes, fields: dict[str, str]) -> dict:
        boundary = "----VPBuddyHeadlessBoundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="chunk.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode() + wav_data + b"\r\n"
        for key, value in fields.items():
            body += (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        body += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            self._url(path),
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="VPBuddy 无头客户端 smoke test")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="VPBuddy 服务端地址")
    parser.add_argument("--chunks", type=int, default=1, help="上传音频分片数量")
    parser.add_argument("--duration", type=float, default=1.0, help="每个测试 WAV 的秒数")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    client = HeadlessVPBuddyClient(args.server)
    try:
        result = client.run_smoke(chunks=args.chunks, chunk_duration_sec=args.duration)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            event_types = [e["event"] for e in result["events"]]
            print(f"meeting_id={result['meeting_id']}")
            print(f"chunks={len(result['chunk_responses'])}")
            print(f"events={event_types}")
            print(f"docs={len(result['docs'].get('docs', []))}")
        return 0
    finally:
        client.stop()


if __name__ == "__main__":
    raise SystemExit(main())
