"""VPBuddy UI Server - real-time meeting AI backend API"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from python_multipart import parse_form  # P1#3 (2026-07-04)
from python_multipart import parse_form
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

# Auto-computed project root. P1#1 (2026-07-04)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# === Server modules (P1#2 2026-07-04) ===
from .server.config import (
    DATA_DIR, DOCS_DIR, UI_DIR, DOC_KINDS, DOC_LABELS,
    CONTROLLER_PID_FILE, CONTROLLER_LOG,
    _CHAT_AGENT_LOCK, _CLEAN_AGENT_LOCK,
    ASR_CLEAN_WINDOW_SIZE, ASR_CLEAN_WINDOW_TIMEOUT_S,
    ASR_CLEAN_MAX_CHARS, ASR_CLEAN_DEFAULT_MODEL,
)
from .server.stream_meta import _stream_meta_path, _load_stream_meta, _save_stream_meta
from .server.asr_clean import _get_clean_agent, _run_asr_clean
from .server.chat_engine import (
    _chat_path, _load_chat_history, _save_chat_history,
    _append_chat_message, _meeting_context_for_chat,
    _get_chat_agent, _run_vp_chat, _doc_path, _doc_payload,
)
from .server.api_utils import (
    _norm_text, _is_duplicate_segment, _state_payload,
    _validate_meeting_id, list_meetings, get_timeline, get_status,
)

def _parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], bytes | None]:
    """P1#3 (2026-07-04): Use python-multipart instead of hand-written parser."""
    _, fields_list, files = parse_form(body, content_type)
    fields = {f.name: f.data.decode("utf-8", errors="replace") for f in fields_list}
    file_data = None
    for f in files:
        if f.name in ("audio", "file") or f.filename:
            file_data = f.data
            break
    return fields, file_data

    def log_message(self, format, *args):
        """安静点(不打印每次请求)"""
        pass

    def do_OPTIONS(self):  # noqa: N802 (BaseHTTPRequestHandler)
        # CORS 预检 (2026-06-26): Tauri webview / 任何浏览器对
        # POST application/json 会先发 OPTIONS;没有这个 handler 就 501
        # → 前端 "Failed to fetch"
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):  # noqa: N802 (BaseHTTPRequestHandler)
        # HEAD 跟 GET 走一样的路由,只回头不回 body
        # (curl -I / 健康检查用)
        self.do_GET() if False else None  # 简化:直接 200 + 空 body
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler)
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        # 根 → UI shell
        if path == "/" or path == "/index.html":
            return self._serve_file(UI_DIR / "index.html", "text/html")

        # 静态文档
        if path.startswith("/docs/"):
            rel = path[6:]  # 去掉 /docs/
            f = DOCS_DIR / rel
            if f.is_file():
                mime = "text/html" if f.suffix == ".html" else "text/markdown"
                return self._serve_file(f, mime)
            return self._404(f"docs/{rel}")

        # API: meetings
        if path == "/api/meetings":
            meetings = list_meetings()
            return self._json({"meetings": meetings, "count": len(meetings)})

        # API: meeting ID 重名校验 (ADR-0022 — 首页建会议前先查)
        # GET /api/meetings/check_id?id=XXX → {"id": "XXX", "exists": bool}
        if path == "/api/meetings/check_id":
            mid = params.get("id", [""])[0].strip()
            if not mid:
                return self._json({"error": "id 必填", "status": 400}, 400)
            ok, err = _validate_meeting_id(mid)
            if not ok:
                return self._json({"id": mid, "valid": False, "error": err}, 400)
            meeting_data_path = DATA_DIR / f"{mid}.json"
            return self._json({"id": mid, "valid": True, "exists": meeting_data_path.exists()})

        # API: timeline
        if path == "/api/timeline":
            events = get_timeline()
            return self._json({"events": events, "count": len(events)})

        # API: kb search
        if path == "/api/kb/search":
            if self.command == "GET":
                q = params.get("q", [""])[0]
                meeting_id = params.get("meeting_id", [None])[0]
                if not q.strip():
                    return self._json({"results": []})
                from .kb_api import handle_kb_search
                result = handle_kb_search(params, b"")
                return self._json(result)
            else:
                # POST body handled in do_POST
                return self._json({"error": "use POST with JSON body"}, 405)

        # API: kb list
        if path == "/api/kb/list":
            from .kb_api import handle_kb_list
            return self._json(handle_kb_list(params))

        # API: kb delete
        kb_del_match = re.match(r"^/api/kb/([a-zA-Z0-9:_-]+)$", path)
        if kb_del_match and self.command == "DELETE":
            from .kb_api import handle_kb_delete
            return self._json(handle_kb_delete(path))

        # API: status
        if path == "/api/status":
            return self._json(get_status())

        # API: 单场会议状态/事实 + 转写历史
        if path.startswith("/api/meetings/") and path.endswith("/state"):
            meeting_id = path.split("/")[3]
            return self._handle_meeting_state(meeting_id)

        # API: 单场会议 VP Chat 历史
        if path.startswith("/api/meetings/") and path.endswith("/chat/history"):
            meeting_id = path.split("/")[3]
            return self._handle_chat_history(meeting_id)

        # API: 单场会议 协作提问文档 (ADR-0028)
        # GET /api/meetings/{id}/collab → {collab, pending, answered, stats}
        if path.startswith("/api/meetings/") and path.endswith("/collab"):
            meeting_id = path.split("/")[3]
            return self._handle_collab_get(meeting_id)

        # API: 单场会议 6 类文档正文
        if path.startswith("/api/meetings/") and path.endswith("/docs"):
            meeting_id = path.split("/")[3]
            return self._handle_meeting_docs(meeting_id)

        # API: 单场会议某一文档正文
        doc_match = re.match(r"^/api/meetings/([^/]+)/docs/([^/]+)$", path)
        if doc_match:
            meeting_id, kind = doc_match.group(1), doc_match.group(2)
            return self._handle_meeting_doc(meeting_id, kind)

        # API: 单场会议 demo 版本列表 (ADR-0024)
        # GET /api/meetings/{id}/demo/versions → {versions: [{version, created_at, summary, file_size}, ...]}
        demo_ver_match = re.match(r"^/api/meetings/([^/]+)/demo/versions$", path)
        if demo_ver_match:
            from .demo_version import list_versions
            meeting_id = demo_ver_match.group(1)
            versions = list_versions(meeting_id)
            return self._json({"meeting_id": meeting_id, "versions": versions, "count": len(versions)})

        # API: SSE 实时事件流 /api/meetings/{id}/events
        if path.startswith("/api/meetings/") and path.endswith("/events"):
            meeting_id = path.split("/")[3]  # /api/meetings/{id}/events
            return self._handle_sse_events(meeting_id)

        return self._404(path)

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler)
        url = urlparse(self.path)
        path = url.path

        # API: upload audio → auto transcribe + ingest + trigger 6 docs
        if path == "/api/meetings/upload":
            return self._handle_upload_audio()

        # API: 流式 start — 创建长连接会议 (Tauri 客户端调用)
        if path == "/api/meetings/stream_start":
            return self._handle_stream_start()

        # 2026-06-27: stream_stop — 客户端 stop_capture 调, 关闭 SSE + 清残留
        # 路径: POST /api/meetings/{id}/stream_stop
        if path.startswith("/api/meetings/") and path.endswith("/stream_stop"):
            meeting_id = path.split("/")[3]
            return self._handle_stream_stop(meeting_id)

        # API: 手动 [结束会议] 按钮 (ADR-0022)
        # POST /api/meetings/{id}/close → 推 meeting-complete + close_meeting
        # 与 stream_stop 区别: stream_stop = 停录音 (SSE 还活), close = 真结束会议
        if path.startswith("/api/meetings/") and path.endswith("/close"):
            meeting_id = path.split("/")[3]
            return self._handle_meeting_close(meeting_id)

        # API: 流式 chunk — 接收 30s 切片 + 立即触发 6 docs
        # 2026-06-25: 默认走原同步模式 (向后兼容). 加 ?sync=false 走异步 fire-and-forget,
        # 立即返回 {"status":"accepted"}, 后台 daemon thread 跑 funasr+ingest+6 docs+push_event,
        # 客户端通过 SSE /api/meetings/{id}/events 收结果. (Tauri client 改用 ?sync=false)
        if path.startswith("/api/meetings/") and path.endswith("/stream_chunk"):
            meeting_id = path.split("/")[3]  # /api/meetings/{id}/stream_chunk
            # 解析 ?sync=true|false query (urlparse 在文件顶部已 import)
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            sync_mode = qs.get("sync", ["true"])[0].lower() != "false"  # 默认同步 (兼容)
            return self._handle_stream_chunk(meeting_id, sync_mode=sync_mode)

        # API: VP Chat — VP 自由输入接 Hermes 主控 agent
        if path.startswith("/api/meetings/") and path.endswith("/chat"):
            meeting_id = path.split("/")[3]
            return self._handle_chat(meeting_id)

        # API: 协作提问文档 (ADR-0028)
        # POST /api/meetings/{id}/ask_question?section=X&question=Y&asker=Z
        if path.startswith("/api/meetings/") and path.endswith("/ask_question"):
            meeting_id = path.split("/")[3]
            return self._handle_collab_ask(meeting_id)

        # POST /api/meetings/{id}/answer_question?qid=X&answer=Y
        if path.startswith("/api/meetings/") and path.endswith("/answer_question"):
            meeting_id = path.split("/")[3]
            return self._handle_collab_answer(meeting_id)

        # API: KB upload (multipart)
        if path == "/api/kb/upload":
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            from .kb_api import handle_kb_upload
            result = handle_kb_upload(body, content_type)
            return self._json(result, result.get("status", 200))

        # API: KB search (POST with JSON body)
        if path == "/api/kb/search":
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            from .kb_api import handle_kb_search
            result = handle_kb_search(qs, body)
            return self._json(result)

        # 2026-07-02 e2e 端点 (env-guarded, 默认 404): 触发 ui_server_helpers.check_all_docs_stored_notify
        # 在**生产 server 进程内部**跑 check, 让 push_event 推给真 SSE 订阅者.
        # 用法: curl -X POST 'http://gpu:8765/api/_e2e/check_docs_complete?mid=XXX'
        # env: VPBUDDY_E2E=1 才暴露 (生产 deploy 不设这个 env, 默认 404)
        if path == "/api/_e2e/check_docs_complete":
            if os.environ.get("VPBUDDY_E2E") != "1":
                return self._404(path)
            from .ui_server_helpers import check_all_docs_stored_notify
            qs = parse_qs(urlparse(self.path).query)
            mid = qs.get("mid", [None])[0]
            if not mid:
                return self._json({"error": "missing ?mid=XXX"}, 400)
            ok = check_all_docs_stored_notify(mid)
            return self._json({"meeting_id": mid, "all_stored": ok})

        return self._404(path)

    def _handle_meeting_state(self, meeting_id: str):
        """返回单场会议的实时状态、事实列表、转写历史和性能指标。"""
        try:
            from .storage import MeetingStorage
            storage = MeetingStorage(DATA_DIR)
            if not storage.exists(meeting_id):
                return self._json({"error": f"meeting {meeting_id} not found"}, 404)
            state = storage.load(meeting_id)
            meta = _load_stream_meta(meeting_id)
            return self._json({
                "state": _state_payload(state, include_items=True),
                "transcript_segments": meta.get("transcript_segments", [])[-300:],
                "metrics": meta.get("metrics", [])[-100:],
                "processed_chunks": meta.get("processed_chunks", []),
            })
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _handle_meeting_docs(self, meeting_id: str):
        """返回单场会议 6 类文档正文。"""
        docs = [_doc_payload(meeting_id, kind) for kind in DOC_KINDS]
        return self._json({"meeting_id": meeting_id, "docs": docs})

    def _handle_meeting_doc(self, meeting_id: str, kind: str):
        """返回单场会议某一类文档正文。"""
        if kind not in DOC_KINDS:
            return self._json({"error": f"unknown doc kind: {kind}"}, 400)
        return self._json(_doc_payload(meeting_id, kind))

    def _handle_chat_history(self, meeting_id: str):
        """返回 VP Chat 历史。"""
        return self._json({
            "meeting_id": meeting_id,
            "messages": _load_chat_history(meeting_id),
        })

    def _handle_chat(self, meeting_id: str):
        """VP 自由输入 → Hermes VP Chat 主控 agent → SSE 回流.

        2026-07-01 ADR-0023: 支持 multipart/form-data (上传文件/图片).
        - JSON 路径: 原行为, text-only.
        - Multipart 路径: 调 handle_chat_upload, 文本/文件入 KB, 图片 → base64 data URI
          然后走 chat agent 答 (本期简化: 多模态 vision 暂不接, 仅当 files 含图片时
          把图片总数告知 chat agent; KB 文本类已入, agent 可通过 kb_search 检索).
        """
        content_type = self.headers.get("Content-Type", "")

        # Multipart 分支 (ADR-0023 Phase 6)
        if content_type.startswith("multipart/form-data"):
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            from .kb_api import handle_chat_upload
            upload_result = handle_chat_upload(body, content_type, meeting_id)
            if upload_result.get("error"):
                return self._json(upload_result, upload_result.get("status", 400))

            text = upload_result.get("text", "")
            files_meta = upload_result.get("files", [])
            # 把"上传了 N 个文件"作为 user message 进 chat 历史
            if files_meta:
                attach_summary = ", ".join(
                    f.get("filename", "?") for f in files_meta
                    if f.get("status") in ("kb-stored", "image", "empty")
                )
                if attach_summary:
                    text = text or f"[上传了 {len(files_meta)} 个文件]"
            user_msg = _append_chat_message(
                meeting_id,
                "user",
                text,
                source="client-upload",
                extra={"attachments": files_meta},
            )
            try:
                from .realtime_server import push_event
                push_event(meeting_id, "chat-message", user_msg)
            except Exception:
                pass
            # 触发 agent 答 (复用现有 _run_vp_chat, 文本已拼进 history)
            result = _run_vp_chat(meeting_id, text or "(用户只上传了文件, 没问文本)")
            assistant_msg = _append_chat_message(
                meeting_id,
                "assistant",
                result["content"],
                source=result["source"],
                status=result["status"],
                extra={"error": result.get("error"), "attachment_count": len(files_meta)},
            )
            try:
                from .realtime_server import push_event
                push_event(meeting_id, "chat-message", assistant_msg)
            except Exception:
                pass
            return self._json({
                "meeting_id": meeting_id,
                "upload": upload_result,
                "user_message": user_msg,
                "assistant_message": assistant_msg,
                "status": result["status"],
                "source": result["source"],
                "error": result.get("error"),
            })

        # JSON 路径 (原行为, 向后兼容)
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._json({"error": "Invalid JSON"}, 400)

        message = str(payload.get("message", "")).strip()
        if not message:
            return self._json({"error": "message is required"}, 400)

        client_context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        user_msg = _append_chat_message(
            meeting_id,
            "user",
            message,
            source="client",
            extra={"context": client_context},
        )
        try:
            from .realtime_server import push_event
            push_event(meeting_id, "chat-message", user_msg)
        except Exception:
            pass

        result = _run_vp_chat(meeting_id, message, client_context)
        assistant_msg = _append_chat_message(
            meeting_id,
            "assistant",
            result["content"],
            source=result["source"],
            status=result["status"],
            extra={"error": result.get("error")},
        )
        try:
            from .realtime_server import push_event
            push_event(meeting_id, "chat-message", assistant_msg)
        except Exception:
            pass

        return self._json({
            "meeting_id": meeting_id,
            "user_message": user_msg,
            "assistant_message": assistant_msg,
            "status": result["status"],
            "source": result["source"],
            "error": result.get("error"),
        })

    def _handle_stream_start(self):
        """Tauri 客户端调用: 创建"持续接收"会议, 后续每 30s 推 chunk

        2026-07-01:
        - ADR-0022: 接受 ?meeting_id=XXX 参数, 如已存在 → 复用 state, 不创建.
            UI 选/建的会议, 客户端传过来直接用. 不传则保持原行为 (服务端自建 STREAM_xxx).
        - ADR-0021: 接受 ?audio_source=microphone|loopback|both, 默认 microphone.
            老客户端不传 → 向后兼容.
        """
        # 1. 解析 audio_source + meeting_id (query string)
        from urllib.parse import parse_qs, urlparse

        from .state import AudioSourceKind  # 动态 import, ui_server 顶层不依赖 state
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        # meeting_id (2026-07-01 ADR-0022)
        meeting_id_in = qs.get("meeting_id", [""])[0].strip()
        if meeting_id_in:
            ok, err = _validate_meeting_id(meeting_id_in)
            if not ok:
                return self._json({"error": f"meeting_id 非法: {err}", "status": 400}, 400)

        # audio_source (2026-07-01 ADR-0021)
        audio_source_str = qs.get("audio_source", [""])[0].strip().lower() or AudioSourceKind.MICROPHONE.value
        try:
            audio_source = AudioSourceKind(audio_source_str)
        except ValueError:
            print(f"[ui_server] stream_start: invalid audio_source={audio_source_str!r}, fallback to microphone")
            audio_source = AudioSourceKind.MICROPHONE

        # 2. 决定最终 meeting_id (ADR-0022: 复用 UI 选/建的; 老调用方: 服务端自建)
        if meeting_id_in:
            meeting_id = meeting_id_in
        else:
            meeting_id = f"STREAM_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 3. 写 state (复用 or 创建)
        from .state import MeetingState, Platform
        from .storage import MeetingStorage
        storage = MeetingStorage(DATA_DIR)
        reused = bool(meeting_id_in) and storage.exists(meeting_id)
        try:
            if reused:
                # 复用: 读已有, 只更新 audio_source (用户可切换)
                state = storage.load(meeting_id)
                state.audio_source = audio_source
                state.last_updated = datetime.now().isoformat()
                storage.save(state)
            else:
                # 创建新
                state = MeetingState(
                    meeting_id=meeting_id,
                    platform=Platform.LOCAL,
                    audio_source=audio_source,
                    project_name=f"长连接会议 {meeting_id}",
                )
                storage.save(state)
            _save_stream_meta(meeting_id, {
                "processed_chunks": [],
                "transcript_segments": [],
                "metrics": [],
                "created_at": datetime.now().isoformat(),
                "audio_source": audio_source.value,
            })
        except Exception as e:
            return self._json({"error": f"create state failed: {e}"}, 500)
        return self._json({
            "meeting_id": meeting_id,
            "chunk_interval_sec": 30,
            "audio_source": audio_source.value,
            "reused": reused,
            "message": "Stream started, send 30s WAV chunks to /api/meetings/{id}/stream_chunk",
        })

    def _handle_stream_chunk(self, meeting_id: str, sync_mode: bool = False):
        """Tauri 客户端调: 接收 30s WAV → funasr 转写 → ingest 累加 → 触发 controller

        2026-06-25: 加 sync_mode 参数
        - sync_mode=False (默认, client 实际用): multipart 解析 + duplicate 检查立即返回
          {"status":"accepted", "chunk_index":X}; 后续 funasr/ingest/6 docs/push_event
          全部后台 daemon thread 跑完。客户端通过 SSE /api/meetings/{id}/events 实时收
        - sync_mode=True (E2E test 用): 跟原来一样阻塞跑完返回完整 new_segments + state_items
        """
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            return self._json({"error": "Content-Type must be multipart/form-data"}, 400)

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return self._json({"error": "Empty body"}, 400)
        body = self.rfile.read(content_length)

        try:
            fields, file_data = _parse_multipart(body, content_type)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        if not file_data:
            return self._json({"error": "No audio file in form"}, 400)

        chunk_index = int(fields.get("chunk_index", "0") or "0")
        chunk_start_sec = float(fields.get("chunk_start_sec", "0") or "0")
        overlap_sec = float(fields.get("overlap_sec", "0") or "0")
        client_sent_at = float(fields.get("client_sent_at", "0") or "0")

        meta = _load_stream_meta(meeting_id)
        if chunk_index in meta.get("processed_chunks", []):
            return self._json({
                "meeting_id": meeting_id,
                "chunk_index": chunk_index,
                "new_segments": [],
                "duplicate_chunk": True,
                "docs_triggered": False,
            })

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        if not sync_mode:
            # 异步模式 (2026-06-25): 后台 daemon thread 跑 funasr/ingest/docs/push_event
            # 立即返回 accepted, client 通过 SSE /events 收结果
            import threading
            threading.Thread(
                target=self._process_chunk_background,
                args=(meeting_id, tmp_path, chunk_index, chunk_start_sec,
                      overlap_sec, client_sent_at),
                daemon=True,
            ).start()
            # 标记 chunk_index 已 accepted (重复检查时不重入)
            meta.setdefault("processed_chunks", []).append(chunk_index)
            meta["processed_chunks"] = sorted(set(meta["processed_chunks"]))
            _save_stream_meta(meeting_id, meta)
            # ⚠️ async_mode 不删 tmp_path, 由后台线程自己读 + 自己 unlink
            # 同步模式才走下面的 try / finally (会 unlink)
            return self._json({
                "meeting_id": meeting_id,
                "chunk_index": chunk_index,
                "status": "accepted",
                "message": "Chunk accepted, processing in background. Subscribe to /api/meetings/{id}/events for results.",
                "duplicate_chunk": False,
                "docs_triggered": True,
            })

        try:
            started = time.time()
            # 1. funasr 转写
            from .scripts.gpu_transcribe import process
            transcript = process(tmp_path)
            raw_segs = transcript.get("segments", [])
            seen_segments = meta.get("transcript_segments", [])
            new_segs = []
            for s in raw_segs:
                abs_seg = dict(s)
                abs_seg["start_sec"] = round(float(s.get("start_sec", 0)) + chunk_start_sec, 3)
                abs_seg["end_sec"] = round(float(s.get("end_sec", 0)) + chunk_start_sec, 3)
                abs_seg["chunk_index"] = chunk_index
                if not _is_duplicate_segment(abs_seg, seen_segments + new_segs):
                    new_segs.append(abs_seg)

            # 2. 累加到 meeting state (load 已有 + 追加新 segments)
            from .ingest import _classify, infer_speaker_map
            from .state import MeetingState, Platform, Priority
            from .storage import MeetingStorage
            storage = MeetingStorage(DATA_DIR)
            if storage.exists(meeting_id):
                state = storage.load(meeting_id)
            else:
                state = MeetingState(
                    meeting_id=meeting_id,
                    platform=Platform.LOCAL,
                    project_name=f"长连接会议 {meeting_id}",
                )

            # 追加新 segments
            spk_map = state.speaker_map
            if new_segs:
                # 保持已有 speaker_map 不变；新 speaker 只补新增映射，避免跨 chunk 漂移
                inferred = infer_speaker_map(new_segs)
                spk_map = dict(state.speaker_map or {})
                for spk_id, spk_name in inferred.items():
                    spk_map.setdefault(spk_id, spk_name)
                for spk_id, spk_name in spk_map.items():
                    state.register_speaker(spk_id, spk_name)
                existing_texts = {_norm_text(getattr(item, "text", "")) for item in (
                    state.requirements + state.goals + state.features + state.risks + state.open_questions
                )}
                for s in new_segs:
                    text = s["text"]
                    norm = _norm_text(text)
                    if not norm or norm in existing_texts:
                        continue
                    existing_texts.add(norm)
                    spk_name = spk_map.get(s["speaker_id"], "UNKNOWN")
                    kind, prio = _classify(text)
                    if kind == "requirement":
                        state.add_requirement(text, priority=prio, speaker_id=spk_name)
                    elif any(k in text for k in ["目标", "希望", "为了", "达成"]):
                        state.add_goal(text, speaker_id=spk_name)
                    elif any(k in text for k in ["功能", "支持", "能力", "可以"]):
                        state.add_feature(text, speaker_id=spk_name)
                    elif kind == "risk":
                        state.add_risk(text, priority=prio, speaker_id=spk_name)
                    elif kind == "question":
                        state.add_question(text, is_urgent=(prio == Priority.HIGH), speaker_id=spk_name)
            storage.save(state)

            meta.setdefault("processed_chunks", []).append(chunk_index)
            meta["processed_chunks"] = sorted(set(meta["processed_chunks"]))
            meta.setdefault("transcript_segments", []).extend(new_segs)
            processing_ms = int((time.time() - started) * 1000)
            end_to_end_ms = int((time.time() - client_sent_at) * 1000) if client_sent_at else None
            meta.setdefault("metrics", []).append({
                "chunk_index": chunk_index,
                "chunk_start_sec": chunk_start_sec,
                "overlap_sec": overlap_sec,
                "raw_segments": len(raw_segs),
                "new_segments": len(new_segs),
                "processing_ms": processing_ms,
                "end_to_end_ms": end_to_end_ms,
                "received_at": datetime.now().isoformat(),
            })
            _save_stream_meta(meeting_id, meta)

            # 3. 触发 6 个子 session (in-process, 复用 AIAgent 缓存, 跨 chunk 真"长驻")
            # ADR-0006 + ADR-0009: 同 (mid, kind) 跨次调 trigger_sub_session → 同一 AIAgent
            #   → 同一 session_id → 持久 LLM 上下文 (本次会议所有累积)
            # 不同会议起新 AIAgent, 上下文隔离
            # demo agent 单独拎出来, 跟其他 5 个并行触发 (2026-06-23 张胜东纠正)
            # 不禁止 fetch/eval (2026-06-23 二次纠正), 先看效果
            from concurrent.futures import ThreadPoolExecutor

            from .sub_session_controller import trigger_sub_session
            doc_kinds = DOC_KINDS

            def _run_sub(mid, kind):
                try:
                    r = trigger_sub_session(mid, kind, False)
                    return (kind, r.get("triggered"), r.get("error"))
                except Exception as e:
                    return (kind, False, str(e))

            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = [ex.submit(_run_sub, meeting_id, k) for k in doc_kinds]
                # 不等结果, fire-and-forget (前端立刻返回 new_segments)
                # 加 done callback 写日志
                def _log_done(fut):
                    try:
                        kind, triggered, err = fut.result(timeout=1)
                        msg = f"[stream_chunk] {kind} triggered={triggered}"
                        if err:
                            msg += f" err={err[:200]}"
                        print(msg)
                    except Exception as e:
                        print(f"[stream_chunk] callback err: {e}")
                for f in futures:
                    f.add_done_callback(_log_done)

            # 4. 推送实时事件到 SSE (让连接的客户端立即收到更新)
            try:
                from .realtime_server import push_event
                # 推送转写段
                for s in new_segs:
                    push_event(meeting_id, "transcript-segment", {
                        "start_sec": s["start_sec"],
                        "end_sec": s["end_sec"],
                        "text": s["text"],
                        "speaker_id": s["speaker_id"],
                        "chunk_index": chunk_index,
                        "speaker_name": spk_map.get(s["speaker_id"], "UNKNOWN"),
                    })
                # 推送状态更新
                push_event(meeting_id, "state-update", _state_payload(state, include_items=True))
                push_event(meeting_id, "metrics-update", {
                    "chunk_index": chunk_index,
                    "processing_ms": processing_ms,
                    "end_to_end_ms": end_to_end_ms,
                    "raw_segments": len(raw_segs),
                    "new_segments": len(new_segs),
                })
                # 推送文档触发事件
                push_event(meeting_id, "doc-update", {
                    "status": "triggered",
                    "kinds": doc_kinds,
                    "message": "6 docs generation triggered",
                })
            except Exception as e:
                print(f"[stream_chunk] push_event error: {e}")

            return self._json({
                "meeting_id": meeting_id,
                "chunk_index": chunk_index,
                "new_segments": [
                    {
                        "start_sec": s["start_sec"],
                        "end_sec": s["end_sec"],
                        "text": s["text"],
                        "speaker_id": s["speaker_id"],
                        "chunk_index": s.get("chunk_index", chunk_index),
                    } for s in new_segs
                ],
                "state_items": _state_payload(state, include_items=False),
                "metrics": meta.get("metrics", [])[-1],
                "docs_triggered": True,
            })
        except Exception as e:
            import traceback
            return self._json({"error": f"Stream chunk failed: {e}", "trace": traceback.format_exc()}, 500)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _process_chunk_background(self, meeting_id: str, tmp_path: str,
                                   chunk_index: int, chunk_start_sec: float,
                                   overlap_sec: float, client_sent_at: float):
        """2026-06-25: 后台 daemon thread 跑 funasr ASR + ingest + 6 docs + push_event.
        跑完 unlink tmp_path. 异常仅记日志, 不抛回主线程.
        逻辑跟 _handle_stream_chunk 同步模式 try 块完全一致, 抽出来共用.
        """
        try:
            started = time.time()
            from .scripts.gpu_transcribe import process
            transcript = process(tmp_path)
            raw_segs = transcript.get("segments", [])
            meta = _load_stream_meta(meeting_id)
            seen_segments = meta.get("transcript_segments", [])
            new_segs = []
            for s in raw_segs:
                abs_seg = dict(s)
                abs_seg["start_sec"] = round(float(s.get("start_sec", 0)) + chunk_start_sec, 3)
                abs_seg["end_sec"] = round(float(s.get("end_sec", 0)) + chunk_start_sec, 3)
                abs_seg["chunk_index"] = chunk_index
                if not _is_duplicate_segment(abs_seg, seen_segments + new_segs):
                    new_segs.append(abs_seg)

            from .ingest import _classify, infer_speaker_map
            from .state import MeetingState, Platform, Priority
            from .storage import MeetingStorage
            storage = MeetingStorage(DATA_DIR)
            if storage.exists(meeting_id):
                state = storage.load(meeting_id)
            else:
                state = MeetingState(
                    meeting_id=meeting_id,
                    platform=Platform.LOCAL,
                    project_name=f"长连接会议 {meeting_id}",
                )

            spk_map = state.speaker_map
            if new_segs:
                inferred = infer_speaker_map(new_segs)
                spk_map = dict(state.speaker_map or {})
                for spk_id, spk_name in inferred.items():
                    spk_map.setdefault(spk_id, spk_name)
                for spk_id, spk_name in spk_map.items():
                    state.register_speaker(spk_id, spk_name)
                existing_texts = {_norm_text(getattr(item, "text", "")) for item in (
                    state.requirements + state.goals + state.features + state.risks + state.open_questions
                )}
                for s in new_segs:
                    text = s["text"]
                    norm = _norm_text(text)
                    if not norm or norm in existing_texts:
                        continue
                    existing_texts.add(norm)
                    spk_name = spk_map.get(s["speaker_id"], "UNKNOWN")
                    kind, prio = _classify(text)
                    if kind == "requirement":
                        state.add_requirement(text, priority=prio, speaker_id=spk_name)
                    elif any(k in text for k in ["目标", "希望", "为了", "达成"]):
                        state.add_goal(text, speaker_id=spk_name)
                    elif any(k in text for k in ["功能", "支持", "能力", "可以"]):
                        state.add_feature(text, speaker_id=spk_name)
                    elif kind == "risk":
                        state.add_risk(text, priority=prio, speaker_id=spk_name)
                    elif kind == "question":
                        state.add_question(text, is_urgent=(prio == Priority.HIGH), speaker_id=spk_name)
            storage.save(state)

            meta.setdefault("transcript_segments", []).extend(new_segs)
            processing_ms = int((time.time() - started) * 1000)
            end_to_end_ms = int((time.time() - client_sent_at) * 1000) if client_sent_at else None
            meta.setdefault("metrics", []).append({
                "chunk_index": chunk_index,
                "chunk_start_sec": chunk_start_sec,
                "overlap_sec": overlap_sec,
                "raw_segments": len(raw_segs),
                "new_segments": len(new_segs),
                "processing_ms": processing_ms,
                "end_to_end_ms": end_to_end_ms,
                "received_at": datetime.now().isoformat(),
            })
            _save_stream_meta(meeting_id, meta)

            # 2026-06-30: 6 docs trigger fire-and-forget, 不阻塞 asr_clean + push_event
            # 原因: 6 docs 各跑 30-100s 调云端 LLM, 阻塞会拖慢 transcript-segment 推送
            # 张胜东反馈: "asr的前几句才出来, 但demo 早就出来了, 说明不是 asr 慢而是推送/接收慢"
            from concurrent.futures import ThreadPoolExecutor

            from .sub_session_controller import trigger_sub_session
            doc_kinds = DOC_KINDS

            def _run_sub(mid, kind):
                try:
                    r = trigger_sub_session(mid, kind, False)
                    return (kind, r.get("triggered"), r.get("error"))
                except Exception as e:
                    return (kind, False, str(e))

            def _log_done(fut, kind):
                try:
                    _, triggered, err = fut.result(timeout=1)
                    msg = f"[stream_chunk/bg] {kind} triggered={triggered}"
                    if err:
                        msg += f" err={err[:200]}"
                    print(msg)
                except Exception as e:
                    print(f"[stream_chunk/bg] {kind} callback err: {e}")

            # 2026-06-30: 独立 executor 不 await, fire-and-forget
            # 用 daemon=True 让线程不阻塞进程退出
            _bg_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="doc-trigger")
            for k in doc_kinds:
                fut = _bg_executor.submit(_run_sub, meeting_id, k)
                fut.add_done_callback(lambda f, kk=k: _log_done(f, kk))

            # push_event SSE
            try:
                from .realtime_server import push_event
                # 2026-06-28: ASR 后处理 — 累积窗口触发 LLM 整理
                # 原始 segments 仍存 meta["transcript_segments"], 整理版存 meta["cleaned_segments"]
                # 客户端只看到整理版 (text 字段被替换), 字段 cleaned=true 标记
                now_ts = time.time()
                meta.setdefault("pending_clean", [])  # List[Tuple[raw_seg, ts]]
                # 把这一批新 segments 加进 pending_clean 缓冲
                for s in new_segs:
                    meta["pending_clean"].append({"seg": s, "received_ts": now_ts})

                # 判断是否触发清理: 段数 >= 5 或 最旧段超过 30s
                should_clean = False
                if len(meta["pending_clean"]) >= ASR_CLEAN_WINDOW_SIZE:
                    should_clean = True
                elif meta["pending_clean"]:
                    oldest_ts = meta["pending_clean"][0]["received_ts"]
                    if (now_ts - oldest_ts) >= ASR_CLEAN_WINDOW_TIMEOUT_S:
                        should_clean = True

                if should_clean:
                    # 取出待整理的 segments
                    pending = meta.pop("pending_clean")
                    pending_segs = [item["seg"] for item in pending]
                    # 拼接之前的整理结果 (上下文)
                    prev_cleaned = "\n".join(meta.get("cleaned_segments", [])[-3:])  # 最近 3 窗口
                    cleaned_text = _run_asr_clean(meeting_id, pending_segs, prev_cleaned)
                    # 存整理版
                    meta.setdefault("cleaned_segments", []).append(cleaned_text)
                    # 2026-06-29: 存 cleaned_windows — 一一对应 raw_segments 和 cleaned_text
                    # 防 LLM 输出截断时丢原话, 回查用
                    truncated = "[...已截断" in cleaned_text
                    meta.setdefault("cleaned_windows", []).append({
                        "window_id": len(meta.get("cleaned_windows", [])) + 1,
                        "start_sec": pending_segs[0].get("start_sec", 0),
                        "end_sec": pending_segs[-1].get("end_sec", 0),
                        "raw_segments": pending_segs,  # 完整原始 (含 start_sec/speaker_id/text)
                        "cleaned_text": cleaned_text,
                        "truncated": truncated,
                        "cleaned_at": datetime.now().isoformat(),
                        "window_segments": len(pending_segs),
                    })
                    # 2026-06-29: 持久化 meta — 上面改了 cleaned_windows/cleaned_segments, 必须 save
                    _save_stream_meta(meeting_id, meta)
                    # 2026-06-28: 只推一次清理后的"整段"文本 (不是每个原始段都推)
                    # 用最早段的 start_sec, 整段 cleaned_text 当 1 个事件推, 客户端看到一整段
                    first_seg = pending_segs[0]
                    last_seg = pending_segs[-1]
                    push_event(meeting_id, "transcript-segment", {
                        "start_sec": first_seg["start_sec"],
                        "end_sec": last_seg["end_sec"],
                        "text": cleaned_text,  # 整段整理后文本
                        "raw_texts": [s["text"] for s in pending_segs],  # 保留所有原始
                        "speaker_ids": list({s["speaker_id"] for s in pending_segs}),  # 多人去重
                        "chunk_index": chunk_index,
                        "speaker_name": spk_map.get(first_seg["speaker_id"], "UNKNOWN"),
                        "cleaned": True,
                        "window_segments": len(pending_segs),  # 这一窗口 N 段
                    })
                    print(f"[stream_chunk/bg] {meeting_id} ASR 后处理: {len(pending_segs)} 段 → {len(cleaned_text)} 字")
                else:
                    # 未到窗口阈值, 先推送原始 (保证实时性)
                    for s in new_segs:
                        push_event(meeting_id, "transcript-segment", {
                            "start_sec": s["start_sec"],
                            "end_sec": s["end_sec"],
                            "text": s["text"],
                            "speaker_id": s["speaker_id"],
                            "chunk_index": chunk_index,
                            "speaker_name": spk_map.get(s["speaker_id"], "UNKNOWN"),
                            "cleaned": False,
                        })

                push_event(meeting_id, "state-update", _state_payload(state, include_items=True))
                push_event(meeting_id, "metrics-update", {
                    "chunk_index": chunk_index,
                    "processing_ms": processing_ms,
                    "end_to_end_ms": end_to_end_ms,
                    "raw_segments": len(raw_segs),
                    "new_segments": len(new_segs),
                })
                push_event(meeting_id, "doc-update", {
                    "status": "triggered",
                    "kinds": doc_kinds,
                    "message": "6 docs generation triggered",
                })
            except Exception as e:
                print(f"[stream_chunk/bg] push_event error: {e}")
            print(f"[stream_chunk/bg] {meeting_id}/{chunk_index} done in {processing_ms}ms, {len(new_segs)} new segments")
        except Exception as e:
            import traceback
            print(f"[stream_chunk/bg] ERROR chunk_index={chunk_index}: {e}")
            print(traceback.format_exc())
            # 失败也要推 SSE event, 客户端能感知
            try:
                from .realtime_server import push_event
                push_event(meeting_id, "doc-update", {
                    "status": "failed",
                    "chunk_index": chunk_index,
                    "error": str(e)[:500],
                })
            except Exception:
                pass
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _handle_upload_audio(self):
        """处理 multipart/form-data 音频上传 → funasr 转写 → ingest → trigger 6 docs"""
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            return self._json({"error": "Content-Type must be multipart/form-data"}, 400)

        # 简易 multipart parser (避免 cgi 在 Py3.13 被移除)
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return self._json({"error": "Empty body"}, 400)
        body = self.rfile.read(content_length)

        # 提取 boundary
        boundary_match = re.search(r'boundary=(?:"([^"]+)"|([^\s;]+))', content_type)
        if not boundary_match:
            return self._json({"error": "Missing boundary"}, 400)
        boundary = (boundary_match.group(1) or boundary_match.group(2)).encode()
        parts = body.split(b"--" + boundary)
        file_data = None
        for part in parts:
            if b'name="audio"' in part or b'name="file"' in part:
                # 拆 header/body
                header_end = part.find(b"\r\n\r\n")
                if header_end == -1:
                    continue
                file_data = part[header_end + 4:]
                # 去掉 trailing \r\n
                if file_data.endswith(b"\r\n"):
                    file_data = file_data[:-2]
                if file_data:
                    break
        if not file_data:
            return self._json({"error": "No audio file in form (field name: 'audio' or 'file')"}, 400)

        # 生成 meeting_id
        meeting_id = f"UPLOAD_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 保存临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        try:
            # 1. 调用 funasr 转写 (复用 gpu_transcribe.process)
            from .scripts.gpu_transcribe import process
            transcript = process(tmp_path)

            # 2. ingest 到 MeetingState (复用 ingest.ingest_transcript)
            from .ingest import ingest_transcript
            from .state import Platform
            state = ingest_transcript(
                meeting_id=meeting_id,
                transcript=transcript,
                project_name=f"上传会议 {meeting_id}",
                platform=Platform.LOCAL,
            )

            # 3. 触发 controller 一轮 (异步, 不阻塞)
            import subprocess
            controller_cmd = [
                sys.executable, "-m", "vpbuddy.controller",
                "--once", "--meeting", meeting_id
            ]
            subprocess.Popen(
                controller_cmd,
                cwd=str(Path(__file__).parent.parent.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # 4. 返回结果
            return self._json({
                "meeting_id": meeting_id,
                "transcript_segments": len(transcript.get("segments", [])),
                "num_speakers": transcript.get("num_speakers", 0),
                "state_items": {
                    "requirements": len(state.requirements),
                    "risks": len(state.risks),
                    "questions": len(state.open_questions),
                },
                "docs_ready_in_seconds": 30,
                "message": "Audio processed, docs will be ready in ~30s",
            })
        except Exception as e:
            import traceback
            return self._json({"error": f"Processing failed: {e}", "trace": traceback.format_exc()}, 500)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _serve_file(self, path: Path, mime: str):
        try:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            # 2026-07-03: 公网 GPU 部署下 /docs/* 静态文件被浏览器 CORS 拦截
            # (Tauri webview 同源, 但 vite preview + 任何外站 fetch 都 CORS fail).
            # 给静态文件也加 CORS header, 跟 _json / SSE 一致.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._500(str(e))

    def _json(self, obj, status_code: int = 200):
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _404(self, what):
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"404 Not Found: {what}".encode())

    def _handle_sse_events(self, meeting_id: str):
            """SSE 实时事件流: 客户端连接后持续接收转写/文档/状态更新"""
            # 2026-06-28: HTTP/1.1 + chunked transfer encoding + keep-alive。
            # Python BaseHTTP 默认 wfile.write() 不加 chunked 头, hyper/reqwest
            # 在 HTTP/1.1 + 无 Content-Length 下等不到 framing → stream.next()
            # 永远 Pending → 客户端 0 个 SSE 事件。
            # 手动 chunked: 每帧 "hex_len\r\ndata\r\n", 终止 "0\r\n\r\n"。
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Transfer-Encoding", "chunked")  # 2026-06-28: 显式声明
            self.send_header("X-Accel-Buffering", "no")  # 反向代理也别缓冲
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            # 强制 flush HTTP 响应头, 避免缓冲
            self.wfile.flush()

            try:
                from .realtime_server import sse_generator
                last_event_id = self.headers.get("Last-Event-ID") or parse_qs(urlparse(self.path).query).get("last_event_id", [None])[0]
                for chunk in sse_generator(meeting_id, last_event_id=last_event_id):
                    # 2026-06-28: 手动 chunked encoding
                    # 格式: "{hex长度}\r\n{data}\r\n"
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                # 终止 chunk: "0\r\n\r\n"
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # 客户端断开, 正常
                pass
            except Exception as e:
                print(f"[SSE] {meeting_id} error: {e}")

    def _handle_stream_stop(self, meeting_id: str):
        """2026-06-27: 客户端 stop_capture 调用, 关闭 SSE + 清残留

        ⚠️ 2026-07-01 ADR-0022 语义: stream_stop = 停录音 (SSE 还活)
        真结束会议走 _handle_meeting_close (POST /api/meetings/{id}/close).
        老调用方 (stop_capture) 走这里 — 兼容保留, 不主动推 meeting-complete.
        """
        try:
            from .realtime_server import close_meeting
            closed = close_meeting(meeting_id)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        return self._json({
            "meeting_id": meeting_id,
            "closed_subscribers": closed,
            "message": "Stream stopped, SSE subscribers closed",
        })

    def _handle_collab_get(self, meeting_id: str):
        """GET /api/meetings/{id}/collab — 返 collab.md + pending/answered 列表 + 统计.

        2026-07-01 ADR-0028: 3 个 agent 共享 collab.md, UI 端面板调这个端点拉全量.
        """
        try:
            from .collab import (
                collab_stats,
                list_answered,
                list_pending,
                read_collab,
            )
            return self._json({
                "meeting_id": meeting_id,
                "collab": read_collab(meeting_id),
                "pending": list_pending(meeting_id),
                "answered": list_answered(meeting_id),
                "stats": collab_stats(meeting_id),
            })
        except Exception as e:
            return self._json({"error": str(e)}, 500)


    def _handle_collab_ask(self, meeting_id: str):
        """POST /api/meetings/{id}/ask_question?section=X&question=Y[&asker=Z].

        任意 agent / 用户都能调. 节流: 同 (mid, section, 相似问题) 1 次会议只 1 次.
        成功后推 SSE `collab-update` 让前端实时刷新.
        """
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        section = (qs.get("section", [""])[0] or "").strip()
        question = (qs.get("question", [""])[0] or "").strip()
        asker = (qs.get("asker", ["agent"])[0] or "agent").strip()
        if not section or not question:
            return self._json({"error": "section 和 question 必填"}, 400)

        from .collab import ask_question
        result = ask_question(meeting_id, section, question, asker=asker)
        if not result["ok"]:
            return self._json(result, 400)

        # SSE: 让前端 + 其他 agent 实时看到 (已在 chat-message 流之外, 新事件类型)
        try:
            from .realtime_server import push_event
            push_event(meeting_id, "collab-update", {
                "action": "ask",
                "qid": result.get("qid"),
                "section": section,
                "status": result["status"],
                "question": question,
                "asker": asker,
            })
        except Exception:
            pass
        return self._json(result)


    def _handle_collab_answer(self, meeting_id: str):
        """POST /api/meetings/{id}/answer_question?qid=X&answer=Y[&answerer=Z].

        把 pending Q 标记为 answered, 推到 Answered 段. 推 SSE 通知前端 + agent.
        """
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        qid = (qs.get("qid", [""])[0] or "").strip()
        answer = (qs.get("answer", [""])[0] or "").strip()
        answerer = (qs.get("answerer", ["VP"])[0] or "VP").strip()
        if not qid or not answer:
            return self._json({"error": "qid 和 answer 必填"}, 400)

        from .collab import answer_question
        result = answer_question(meeting_id, qid, answer, answerer=answerer)
        if not result["ok"]:
            return self._json(result, result.get("status") == "not_found" and 404 or 400)

        # SSE: 通知前端 + 监听线程 (后续 Commit 3 监听回答触发增量 patch)
        try:
            from .realtime_server import push_event
            push_event(meeting_id, "collab-update", {
                "action": "answer",
                "qid": qid,
                "answer": answer,
                "answerer": answerer,
                "status": "answered",
            })
        except Exception:
            pass
        return self._json(result)


    def _handle_meeting_close(self, meeting_id: str):
        """2026-07-01 ADR-0022: 用户主动 [结束会议] 按钮调

        行为:
        1. push_event("meeting-complete", {...}) — 客户端 SSE 收到, 状态切 'closed'
        2. close_meeting(meeting_id) — 服务端 SSE 订阅者退出
        3. clear proactive throttle (ADR-0023 Phase 5: 下次开同 ID 会议, 主动消息能再触发)

        调用方: UI 手动按钮 / 客户端断开前 / 切会议时 (前一个会议).
        """
        try:
            from .realtime_server import close_meeting, push_event
            push_event(meeting_id, "meeting-complete", {
                "meeting_id": meeting_id,
                "status": "user_closed",
                "note": "用户主动结束 (ADR-0022)",
            })
            closed = close_meeting(meeting_id)
            # 2026-07-01 ADR-0023: 清 proactive 节流, 下次复用同 mid 时主动消息能再触发
            try:
                from .agent_proactive import clear_throttle
                cleared = clear_throttle(meeting_id)
            except Exception:
                cleared = 0
            print(f"[ui_server] 用户主动 close_meeting: {meeting_id}, 关闭 {closed} 个 SSE 订阅者, 清 {cleared} 个 proactive 节流")
            return self._json({
                "meeting_id": meeting_id,
                "closed_subscribers": closed,
                "proactive_cleared": cleared,
                "status": "closed",
            })
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _500(self, msg):
        self.send_response(500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"500: {msg}".encode())


def main(argv: list[str] | None = None) -> int:
    """UI server 主入口 — `python -m vpbuddy.ui_server` 或 `vpbuddy ui`"""
    parser = argparse.ArgumentParser(description="VPBuddy UI server")
    parser.add_argument("--port", type=int, default=8765, help="端口(默认 8765)")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址(默认 0.0.0.0)")
    args = parser.parse_args(argv)

    # KB Chroma 首次加载 embedding 模型 ~1s, 启动时预热
    try:
        from .rag_backend import get_rag
        get_rag().count()
    except Exception:
        pass
    # P0 (2026-07-04): ASR model warmup
    try:
        from .scripts.gpu_transcribe import warmup_models
        warmup_models()
    except Exception:
        pass
    # P0 (2026-07-04): ASR model warmup, avoids 30s first-call delay
    try:
        from .scripts.gpu_transcribe import warmup_models
        warmup_models()
    except Exception:
        pass

    # 2026-06-28: 启动时打印版本号 — 一眼看出是否最新 release
    try:
        from ._version import __version__
    except Exception:
        __version__ = "unknown"
    print(f"🏷️  VPBuddy UI server version: {__version__}", flush=True)
    print("🚀 VPBuddy UI server 启动", flush=True)
    print(f"   UI:    http://{args.host}:{args.port}/", flush=True)
    print(f"   DATA:  {DATA_DIR}", flush=True)
    print(f"   DOCS:  {DOCS_DIR}", flush=True)
    from .rag_backend import ChromaRAG
    print(f"   KB:    {ChromaRAG.__module__} (Chroma 嵌入式)", flush=True)
    # 2026-06-27: IPv6 dual-stack — 默认 --host=:: 让 v4+v6 同时可连
    # 老的 0.0.0.0 仅 IPv4; 用户的域名 gpu.zhangshengdong.com 只有 AAAA 记录
    if args.host == "0.0.0.0":
        args.host = "::"
    if ":" in args.host:
        # IPv6 地址 — 用 AF_INET6 + IPV6_V6ONLY=0 双栈
        import socket as _socket
        class DualStackServer(ThreadingHTTPServer):
            address_family = _socket.AF_INET6
        DualStackServer.allow_reuse_address = True
        sock = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.IPPROTO_IPV6, _socket.IPV6_V6ONLY, 0)
        sock.bind((args.host, args.port))
        sock.listen(128)
        server = DualStackServer(sock.getsockname(), Handler, bind_and_activate=False)
        server.socket = sock
        server.server_bind = lambda: None  # 已 bind
        server.server_activate = lambda: None  # 已 listen
    else:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 退出", flush=True)
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
