"""
VPBuddy UI Server — 实时会议 AI 后端 API

提供:
- GET /                     → UI shell
- GET /docs/*               → 静态文档
- GET /api/meetings         → 会议列表
- GET /api/timeline         → 全部累积项按时间倒序
- GET /api/kb/search?q=     → 跨会议 RAG 检索
- GET /api/status           → Controller + 数据状态
- POST /api/meetings/upload → 上传音频自动转写+入库+触发 6 docs
- POST /api/meetings/stream_start → 创建流式会议
- POST /api/meetings/{id}/stream_chunk → 接收音频切片
- GET  /api/meetings/{id}/events → SSE 实时推送转写/文档/状态更新

用法: python -m vpbuddy.ui_server [--port 8765]
"""
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
from typing import Any, List, Optional
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 🔒 HF 模型离线铁律 (2026-06-23 ADR-0011):
# 国内 huggingface.co 被墙,启动时强制默认走本地 cache。
# 用户装新模型时临时设 HF_HUB_OFFLINE=0 即可。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# 默认路径(可通过环境变量覆盖)
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", "/home/zsd/vpbuddy/docs"))
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", "/home/zsd/vpbuddy/data/meetings"))
UI_DIR = Path(os.environ.get("VPBUDDY_UI_DIR", "/home/zsd/vpbuddy/ui"))
KB_PATH = Path(os.environ.get("VPBUDDY_KB_DB", "/home/zsd/vpbuddy/data/knowledge.db"))
CONTROLLER_PID_FILE = Path("/tmp/vpbuddy_controller.pid")
CONTROLLER_LOG = Path("/tmp/vpbuddy_controller.log")

DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]
DOC_LABELS = {
    "req": "需求文档",
    "arch": "架构文档",
    "tasks": "任务拆解",
    "api": "API 设计",
    "risk": "风险分析",
    "demo": "Demo",
}
_CHAT_AGENT_CACHE: dict[str, Any] = {}
_CHAT_AGENT_LOCK = threading.Lock()


def _stream_meta_path(meeting_id: str) -> Path:
    return DATA_DIR / f"{meeting_id}.stream.json"


def _load_stream_meta(meeting_id: str) -> dict:
    path = _stream_meta_path(meeting_id)
    if not path.exists():
        return {"processed_chunks": [], "transcript_segments": [], "metrics": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_chunks": [], "transcript_segments": [], "metrics": []}


def _save_stream_meta(meeting_id: str, meta: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _stream_meta_path(meeting_id).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip("，。,.!?！？；;：:")


def _is_duplicate_segment(segment: dict, seen_segments: list[dict]) -> bool:
    text = _norm_text(segment.get("text", ""))
    if not text:
        return True
    for old in seen_segments[-30:]:
        old_text = _norm_text(old.get("text", ""))
        if not old_text:
            continue
        if text == old_text or text in old_text or old_text in text:
            return True
    return False


def _parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], Optional[bytes]]:
    boundary_match = re.search(r'boundary=(?:"([^"]+)"|([^\s;]+))', content_type)
    if not boundary_match:
        raise ValueError("Missing boundary")
    boundary = (boundary_match.group(1) or boundary_match.group(2)).encode()
    fields: dict[str, str] = {}
    file_data = None
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        header, data = part.split(b"\r\n\r\n", 1)
        data = data.rstrip(b"\r\n")
        name_match = re.search(rb'name="([^"]+)"', header)
        if not name_match:
            continue
        name = name_match.group(1).decode("utf-8", "ignore")
        if b"filename=" in header or name in ("audio", "file"):
            if data:
                file_data = data
        else:
            fields[name] = data.decode("utf-8", "ignore")
    return fields, file_data


def _doc_path(meeting_id: str, kind: str) -> Path:
    if kind == "demo":
        return DOCS_DIR / meeting_id / "demo" / "demo.html"
    return DOCS_DIR / meeting_id / f"{kind}.md"


def _doc_payload(meeting_id: str, kind: str) -> dict[str, Any]:
    path = _doc_path(meeting_id, kind)
    exists = path.exists()
    content = ""
    updated_at = None
    if exists:
        content = path.read_text(encoding="utf-8", errors="replace")
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    return {
        "meeting_id": meeting_id,
        "kind": kind,
        "label": DOC_LABELS.get(kind, kind),
        "status": "stored" if exists else "pending",
        "path": str(path),
        "content": content,
        "updated_at": updated_at,
        "doc_size": path.stat().st_size if exists else 0,
    }


def _chat_path(meeting_id: str) -> Path:
    return DATA_DIR / f"{meeting_id}.chat.json"


def _load_chat_history(meeting_id: str) -> list[dict[str, Any]]:
    path = _chat_path(meeting_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return data.get("messages", [])
    except Exception:
        return []


def _save_chat_history(meeting_id: str, messages: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _chat_path(meeting_id).write_text(
        json.dumps(messages[-500:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _append_chat_message(
    meeting_id: str,
    role: str,
    content: str,
    *,
    source: str = "vp-chat",
    status: str = "ok",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    message = {
        "id": f"chat-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
        "meeting_id": meeting_id,
        "role": role,
        "content": content,
        "source": source,
        "status": status,
        "created_at": datetime.now().isoformat(),
    }
    if extra:
        message.update(extra)
    history = _load_chat_history(meeting_id)
    history.append(message)
    _save_chat_history(meeting_id, history)
    return message


def _meeting_context_for_chat(meeting_id: str) -> dict[str, Any]:
    state_payload: dict[str, Any] = {"meeting_id": meeting_id, "items": []}
    try:
        from .storage import MeetingStorage
        storage = MeetingStorage(DATA_DIR)
        if storage.exists(meeting_id):
            state_payload = _state_payload(storage.load(meeting_id), include_items=True)
    except Exception as e:
        state_payload["error"] = str(e)

    docs = []
    for kind in DOC_KINDS:
        doc = _doc_payload(meeting_id, kind)
        docs.append({
            "kind": kind,
            "label": doc["label"],
            "status": doc["status"],
            "doc_size": doc["doc_size"],
            "content_preview": doc["content"][:1200],
        })
    meta = _load_stream_meta(meeting_id)
    return {
        "meeting_id": meeting_id,
        "state": state_payload,
        "docs": docs,
        "recent_transcript": meta.get("transcript_segments", [])[-20:],
        "recent_metrics": meta.get("metrics", [])[-5:],
    }


def _get_chat_agent(meeting_id: str):
    session_id = f"meeting:{meeting_id}:vp-chat"
    with _CHAT_AGENT_LOCK:
        if session_id in _CHAT_AGENT_CACHE:
            return _CHAT_AGENT_CACHE[session_id]
        from run_agent import AIAgent  # type: ignore

        _CHAT_AGENT_CACHE[session_id] = AIAgent(
            session_id=session_id,
            enabled_toolsets=["terminal", "file"],
            platform="subagent",
            quiet_mode=True,
            max_iterations=20,
            model=os.environ.get("VPBUDDY_LLM_MODEL", "MiniMax-M3"),
            ephemeral_system_prompt="\n".join([
                "你是 VPBuddy 的 VP Chat 主控 agent。",
                f"session_id 固定 = {session_id}。",
                "你的职责是帮助 VP 理解会议、追问风险、调整方向,并在必要时调度 6 个子 agent。",
                "6 个固定子 agent session 是 req、arch、tasks、api、risk、demo。",
                "你可以建议或触发内部文档/Demo更新,但禁止主动外发、投屏或调用外部会议软件。",
                "固定交付物只有 req/arch/tasks/api/risk/demo,不能创造第 7 类固定交付物。",
                "回答要简洁、明确,并说明你是否建议更新哪个交付物。",
            ]),
        )
        return _CHAT_AGENT_CACHE[session_id]


def _run_vp_chat(meeting_id: str, message: str, client_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    ctx = _meeting_context_for_chat(meeting_id)
    prompt = "\n".join([
        "VP 在 VPBuddy 客户端输入了下面这句话。请结合当前会议上下文回答。",
        "",
        f"VP 输入:\n{message}",
        "",
        f"客户端上下文:\n{json.dumps(client_context or {}, ensure_ascii=False)}",
        "",
        f"当前会议上下文 JSON:\n{json.dumps(ctx, ensure_ascii=False)[:12000]}",
        "",
        "如果 VP 的意图是修改某个交付物,请明确指出目标 kind(req/arch/tasks/api/risk/demo),并给出下一步建议。",
    ])

    holder: dict[str, Any] = {"done": False, "response": None, "error": None}

    def _runner():
        try:
            agent = _get_chat_agent(meeting_id)
            holder["response"] = agent.chat(prompt)
        except Exception as e:
            holder["error"] = e
        finally:
            holder["done"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=int(os.environ.get("VPBUDDY_CHAT_TIMEOUT", "120")))

    if not holder["done"]:
        return {
            "status": "fallback",
            "source": "fallback",
            "content": "Hermes VP Chat 暂时超时。当前输入已记录,但未完成 Hermes 上下文推理或子 agent 调度。",
            "error": "AIAgent timeout",
        }
    if holder["error"]:
        return {
            "status": "fallback",
            "source": "fallback",
            "content": (
                "Hermes VP Chat 当前不可用。输入已记录,服务端没有静默执行外部动作。"
                "请确认 run_agent/AIAgent 或 hermes 运行环境可用后重试。"
            ),
            "error": f"{type(holder['error']).__name__}: {str(holder['error'])[:300]}",
        }
    return {
        "status": "ok",
        "source": "hermes",
        "content": str(holder["response"] or "").strip(),
        "error": None,
    }


def _state_payload(state, include_items: bool = True) -> dict[str, Any]:
    def _items(items, typ: str):
        return [
            {
                "id": getattr(item, "id", ""),
                "type": typ,
                "text": getattr(item, "text", ""),
                "priority": getattr(getattr(item, "priority", None), "value", ""),
                "status": getattr(getattr(item, "status", None), "value", ""),
                "speaker_name": getattr(item, "speaker_name", None) or getattr(item, "speaker_id", None),
                "created_at": getattr(item, "created_at", None),
            }
            for item in items
        ]

    payload = {
        "meeting_id": state.meeting_id,
        "requirements": len(state.requirements),
        "goals": len(state.goals),
        "features": len(state.features),
        "risks": len(state.risks),
        "questions": len(state.open_questions),
        "last_updated": state.last_updated,
    }
    if include_items:
        payload["items"] = (
            _items(state.requirements, "req")
            + _items(state.goals, "goal")
            + _items(state.features, "feat")
            + _items(state.risks, "risk")
            + _items(state.open_questions, "que")
        )[-100:]
    return payload


def list_meetings() -> list[dict]:
    """列出所有会议 + 统计"""
    if not DATA_DIR.exists():
        return []
    out = []
    for f in sorted(DATA_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            item_count = sum(len(data.get(k, [])) for k in
                             ["requirements", "goals", "features", "risks", "open_questions"])
            out.append({
                "meeting_id": data.get("meeting_id", f.stem),
                "platform": data.get("platform", "unknown"),
                "project_name": data.get("project_name"),
                "started_at": data.get("started_at"),
                "last_updated": data.get("last_updated"),
                "item_count": item_count,
            })
        except Exception:
            continue
    return out


def get_timeline() -> list[dict]:
    """全部累积项按 created_at 倒序(时间线)"""
    events = []
    if not DATA_DIR.exists():
        return []
    for f in DATA_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            mid = data.get("meeting_id", f.stem)
            for kind_key, kind_label in [
                ("requirements", "REQ"), ("goals", "GOAL"),
                ("features", "FEAT"), ("risks", "RISK"),
                ("open_questions", "QUE"),
            ]:
                for item in data.get(kind_key, []):
                    events.append({
                        "meeting_id": mid,
                        "kind": kind_label,
                        "id": item.get("id", "?"),
                        "text": item.get("text", ""),
                        "priority": item.get("priority", "?"),
                        "status": item.get("status", "?"),
                        "created_at": item.get("created_at"),
                        "speaker_name": item.get("speaker_name"),
                    })
        except Exception:
            continue
    events.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return events


def search_kb(query: str, top_k: int = 5) -> list[dict]:
    """跨会议 RAG 检索"""
    if not KB_PATH.exists() or not query.strip():
        return []
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from .knowledge_base import KnowledgeBase
        kb = KnowledgeBase(db_path=str(KB_PATH))
        results = kb.search(query, top_k=top_k)
        kb.close()
        return results
    except Exception as e:
        return [{"error": str(e)}]


def get_status() -> dict:
    """Controller + 数据状态"""
    # Controller 状态
    controller = {
        "running": False,
        "pid": None,
        "poll_interval": os.environ.get("VPBUDDY_POLL_INTERVAL", "30"),
        "last_log": None,
    }
    if CONTROLLER_PID_FILE.exists():
        pid = CONTROLLER_PID_FILE.read_text().strip()
        try:
            os.kill(int(pid), 0)
            controller["running"] = True
            controller["pid"] = pid
        except (OSError, ValueError):
            pass
    if CONTROLLER_LOG.exists():
        try:
            # 取最后一行
            with open(CONTROLLER_LOG) as f:
                lines = f.readlines()
                for line in reversed(lines):
                    if line.strip() and "Loading weights" not in line:
                        controller["last_log"] = line.strip()[:200]
                        break
        except Exception:
            pass

    # 数据统计
    meetings = list_meetings()
    total_docs = 0
    if DOCS_DIR.exists():
        for d in DOCS_DIR.iterdir():
            if d.is_dir() and d.name not in ("decisions", "research"):
                for f in d.rglob("*.md"):
                    total_docs += 1
                for f in d.rglob("*.html"):
                    total_docs += 1

    kb_docs = 0
    kb_failed = 0
    if KB_PATH.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(KB_PATH))
            cur = conn.execute("SELECT COUNT(*) FROM documents")
            kb_docs = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass
    # 2026-06-22 加 failed 计数 (sub_session_controller 的 _KB_STATUS)
    try:
        from .sub_session_controller import get_kb_status
        kb_failed = get_kb_status().get("summary", {}).get("failed", 0)
    except Exception:
        pass

    return {
        "controller": controller,
        "stats": {
            "active_meetings": len(meetings),
            "total_docs": total_docs,
            "kb_docs": kb_docs,
            "kb_failed": kb_failed,
        },
        "paths": {
            "data_dir": str(DATA_DIR),
            "docs_dir": str(DOCS_DIR),
            "kb_path": str(KB_PATH),
            "ui_dir": str(UI_DIR),
        },
        "meetings": meetings[:5],  # 最近 5 个
    }


# === HTTP Handler ===
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """安静点(不打印每次请求)"""
        pass

    def do_GET(self):
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

        # API: timeline
        if path == "/api/timeline":
            events = get_timeline()
            return self._json({"events": events, "count": len(events)})

        # API: kb search
        if path == "/api/kb/search":
            q = params.get("q", [""])[0]
            top_k = int(params.get("top_k", ["5"])[0])
            if not q.strip():
                return self._json({"query": "", "results": []})
            results = search_kb(q, top_k=top_k)
            return self._json({"query": q, "results": results, "count": len(results)})

        # API: kb status (2026-06-22 — 跨会议 KB 写入状态)
        if path == "/api/kb/status":
            from .sub_session_controller import get_kb_status
            meeting_id = params.get("meeting_id", [None])[0]
            return self._json(get_kb_status(meeting_id=meeting_id))

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

        # API: 单场会议 6 类文档正文
        if path.startswith("/api/meetings/") and path.endswith("/docs"):
            meeting_id = path.split("/")[3]
            return self._handle_meeting_docs(meeting_id)

        # API: 单场会议某一文档正文
        doc_match = re.match(r"^/api/meetings/([^/]+)/docs/([^/]+)$", path)
        if doc_match:
            meeting_id, kind = doc_match.group(1), doc_match.group(2)
            return self._handle_meeting_doc(meeting_id, kind)

        # API: SSE 实时事件流 /api/meetings/{id}/events
        if path.startswith("/api/meetings/") and path.endswith("/events"):
            meeting_id = path.split("/")[3]  # /api/meetings/{id}/events
            return self._handle_sse_events(meeting_id)

        return self._404(path)

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path

        # API: upload audio → auto transcribe + ingest + trigger 6 docs
        if path == "/api/meetings/upload":
            return self._handle_upload_audio()

        # API: 流式 start — 创建长连接会议 (Tauri 客户端调用)
        if path == "/api/meetings/stream_start":
            return self._handle_stream_start()

        # API: 流式 chunk — 接收 30s 切片 + 立即触发 6 docs
        if path.startswith("/api/meetings/") and path.endswith("/stream_chunk"):
            meeting_id = path.split("/")[3]  # /api/meetings/{id}/stream_chunk
            return self._handle_stream_chunk(meeting_id)

        # API: VP Chat — VP 自由输入接 Hermes 主控 agent
        if path.startswith("/api/meetings/") and path.endswith("/chat"):
            meeting_id = path.split("/")[3]
            return self._handle_chat(meeting_id)

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
        """VP 自由输入 → Hermes VP Chat 主控 agent → SSE 回流。"""
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
        """Tauri 客户端调用: 创建"持续接收"会议, 后续每 30s 推 chunk"""
        meeting_id = f"STREAM_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        # 占位 state (空), 后续 chunk 会更新
        try:
            from .storage import MeetingStorage
            from .state import MeetingState, Platform
            storage = MeetingStorage(DATA_DIR)
            state = MeetingState(
                meeting_id=meeting_id,
                platform=Platform.LOCAL,
                project_name=f"长连接会议 {meeting_id}",
            )
            storage.save(state)
            _save_stream_meta(meeting_id, {
                "processed_chunks": [],
                "transcript_segments": [],
                "metrics": [],
                "created_at": datetime.now().isoformat(),
            })
        except Exception as e:
            return self._json({"error": f"create state failed: {e}"}, 500)
        return self._json({
            "meeting_id": meeting_id,
            "chunk_interval_sec": 30,
            "message": "Stream started, send 30s WAV chunks to /api/meetings/{id}/stream_chunk",
        })

    def _handle_stream_chunk(self, meeting_id: str):
        """Tauri 客户端调: 接收 30s WAV → funasr 转写 → ingest 累加 → 触发 controller"""
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
            from .storage import MeetingStorage
            from .state import MeetingState, Platform, Priority
            from .ingest import _classify, infer_speaker_map
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
        self.wfile.write(f"404 Not Found: {what}".encode("utf-8"))

    def _handle_sse_events(self, meeting_id: str):
        """SSE 实时事件流: 客户端连接后持续接收转写/文档/状态更新"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        # 强制 flush HTTP 响应头, 避免缓冲
        self.wfile.flush()

        try:
            from .realtime_server import sse_generator
            last_event_id = self.headers.get("Last-Event-ID") or parse_qs(urlparse(self.path).query).get("last_event_id", [None])[0]
            for chunk in sse_generator(meeting_id, last_event_id=last_event_id):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # 客户端断开, 正常
            pass
        except Exception as e:
            print(f"[SSE] {meeting_id} error: {e}")

    def _500(self, msg):
        self.send_response(500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"500: {msg}".encode("utf-8"))


def main(argv: Optional[List[str]] = None) -> int:
    """UI server 主入口 — `python -m vpbuddy.ui_server` 或 `vpbuddy ui`"""
    parser = argparse.ArgumentParser(description="VPBuddy UI server")
    parser.add_argument("--port", type=int, default=8765, help="端口(默认 8765)")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址(默认 0.0.0.0)")
    args = parser.parse_args(argv)

    # KB embedding 模型首次加载慢,启动时预热
    if KB_PATH.exists():
        try:
            print(f"预热 KB embedding 模型...", flush=True)
            from .knowledge_base import KnowledgeBase
            kb = KnowledgeBase(db_path=str(KB_PATH))
            _ = kb._get_model()  # 触发加载
            kb.close()
            print(f"✅ KB 模型预热完成", flush=True)
        except Exception as e:
            print(f"⚠️ KB 预热失败(忽略): {e}", flush=True)

    print(f"🚀 VPBuddy UI server 启动", flush=True)
    print(f"   UI:    http://{args.host}:{args.port}/", flush=True)
    print(f"   DATA:  {DATA_DIR}", flush=True)
    print(f"   DOCS:  {DOCS_DIR}", flush=True)
    print(f"   KB:    {KB_PATH}", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 退出", flush=True)
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
