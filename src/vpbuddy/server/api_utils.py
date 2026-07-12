"""API utility functions — state payload, lists, timeline, chat, ASR clean.
Service layer extracted from ui_server.py (P1#2 2026-07-04, completed 2026-07-08).
All functions below are the canonical implementations, originally from ui_server.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .config import (
    ASR_CLEAN_MAX_CHARS,
    ASR_CLEAN_WINDOW_SIZE,
    ASR_CLEAN_WINDOW_TIMEOUT_S,
    DATA_DIR,
    DOCS_DIR,
    DOC_KINDS,
    DOC_LABELS,
    UI_DIR,
    _CHAT_AGENT_LOCK,
)

# ── Module-level caches and locks ──
_CHAT_AGENT_CACHE: dict[str, Any] = {}

# 2026-07-05 fix(#4): per-meeting 文件锁 for stream_meta + chat_history
# 带 LRU 淘汰: 超过 _MAX_LOCKS 时清理最久未访问的锁, 防止内存泄漏
_MAX_LOCKS = 500
_LOCK_ACCESS_ORDER: list[str] = []  # 最近访问的 meeting_id 有序列表
_meta_locks: dict[str, threading.Lock] = {}
_chat_locks: dict[str, threading.Lock] = {}
_file_lock_global = threading.Lock()

# 2026-06-28: ASR 后处理 agent cache
_CLEAN_AGENT_CACHE: dict[str, Any] = {}
_CLEAN_AGENT_LOCK = threading.Lock()


def _evict_locks_if_needed() -> None:
    """当锁数量超过 _MAX_LOCKS 时, 淘汰最久未访问的一半."""
    if len(_meta_locks) + len(_chat_locks) < _MAX_LOCKS * 2:
        return
    # 淘汰最近未访问的 50%
    evict_count = _MAX_LOCKS // 2
    to_evict = _LOCK_ACCESS_ORDER[:evict_count]
    for mid in to_evict:
        _meta_locks.pop(mid, None)
        _chat_locks.pop(mid, None)
    del _LOCK_ACCESS_ORDER[:evict_count]


def _touch_lock_order(meeting_id: str) -> None:
    """更新锁访问顺序 (移到末尾)."""
    if meeting_id in _LOCK_ACCESS_ORDER:
        _LOCK_ACCESS_ORDER.remove(meeting_id)
    _LOCK_ACCESS_ORDER.append(meeting_id)


def _get_meta_lock(meeting_id: str) -> threading.Lock:
    with _file_lock_global:
        if meeting_id not in _meta_locks:
            _meta_locks[meeting_id] = threading.RLock()
        _touch_lock_order(meeting_id)
        _evict_locks_if_needed()
        return _meta_locks[meeting_id]


def _get_chat_lock(meeting_id: str) -> threading.Lock:
    with _file_lock_global:
        if meeting_id not in _chat_locks:
            _chat_locks[meeting_id] = threading.Lock()
        _touch_lock_order(meeting_id)
        _evict_locks_if_needed()
        return _chat_locks[meeting_id]


# ── Safe SSE push ──

_log = logging.getLogger(__name__)


def safe_push_event(meeting_id: str, event_type: str, data: Any) -> None:
    """安全推送 SSE 事件, 失败时仅 log warning 不抛异常.

    替代散落在各路由中的 try/except push_event 模式.
    """
    try:
        from ..realtime_server import push_event
        push_event(meeting_id, event_type, data)
    except Exception:
        _log.warning("non-critical error pushing %s event", event_type, exc_info=True)


# ── Stream metadata ──

def _stream_meta_path(meeting_id: str) -> Path:
    return DATA_DIR / f"{meeting_id}.stream.json"


def _load_stream_meta(meeting_id: str) -> dict:
    lock = _get_meta_lock(meeting_id)
    with lock:
        path = _stream_meta_path(meeting_id)
        if not path.exists():
            return {"processed_chunks": [], "transcript_segments": [], "metrics": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"processed_chunks": [], "transcript_segments": [], "metrics": []}


def _save_stream_meta(meeting_id: str, meta: dict) -> None:
    lock = _get_meta_lock(meeting_id)
    with lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _stream_meta_path(meeting_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ── Text utilities ──

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


# ── Multipart parser ──

def _parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], bytes | None]:
    """用 python-multipart 解析 multipart/form-data (P1#3 2026-07-04).

    返回 (fields, file_data)，fields 中自动包含:
        _filename: 上传文件的原始文件名
        _content_type: 上传文件的 Content-Type
    """
    from io import BytesIO
    from multipart import parse_form

    fields = {}
    file_data: bytes | None = None

    def on_field(f):
        fields[f.field_name.decode()] = f.value.decode("utf-8", "replace")

    def on_file(f):
        nonlocal file_data
        f.file_object.seek(0)
        data = f.file_object.read()
        if data:
            file_data = data
            # 提取文件名和 Content-Type
            if f.file_name:
                fields["_filename"] = f.file_name.decode("utf-8", "replace")
            if f.content_type:
                fields["_content_type"] = f.content_type

    parse_form({"Content-Type": content_type.encode()}, BytesIO(body),
               on_field=on_field, on_file=on_file)
    return fields, file_data


# ── Doc path / payload ──

def _doc_path(meeting_id: str, kind: str) -> Path:
    if kind == "demo":
        return DOCS_DIR / meeting_id / "demo" / "demo.html"
    return DOCS_DIR / meeting_id / f"{kind}.md"


def _doc_payload(meeting_id: str, kind: str) -> dict[str, object]:
    """返回单文档 DTO.

    v0.19.0: version 字段从 demo_version.get_deliverable_version 读取,
    替代旧的 per-kind .version 文件方式。元数据统一存储在 .deliverables.json.
    """
    import time as _time
    from ..demo_version import get_deliverable_version

    path = _doc_path(meeting_id, kind)
    exists = path.exists()
    status = "stored" if exists else "pending"

    raw = path.read_text(encoding="utf-8") if exists else ""
    content = raw[:2000] if kind != "demo" else ""
    version = get_deliverable_version(meeting_id, kind)

    return {
        "meeting_id": meeting_id,
        "kind": kind,
        "label": DOC_LABELS.get(kind, kind),
        "content": content,
        "version": version,
        "doc_size": len(raw),
        "status": status,
        "updated_at": _time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            _time.gmtime(path.stat().st_mtime if exists else _time.time()),
        ),
    }


# ── Chat history ──

def _chat_path(meeting_id: str) -> Path:
    return DATA_DIR / f"{meeting_id}.chat.json"


def _save_chat_history(meeting_id: str, messages: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _chat_path(meeting_id).write_text(
        json.dumps(messages[-500:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def _append_chat_message(
    meeting_id: str,
    role: str,
    content: str,
    *,
    source: str = "vp-chat",
    status: str = "ok",
    extra: dict[str, Any] | None = None,
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
    # fix(#4): per-meeting lock for atomic read-modify-write
    lock = _get_chat_lock(meeting_id)
    with lock:
        history = _load_chat_history(meeting_id)
        history.append(message)
        _save_chat_history(meeting_id, history)
    return message


# ── Meeting context for chat ──

def _meeting_context_for_chat(meeting_id: str) -> dict[str, Any]:
    """构建 VP Chat 上下文 (v0.10.0: 使用 cleaned_text 替代旧 facts)."""
    state_payload: dict[str, Any] = {"meeting_id": meeting_id, "cleaned_text": "", "items": []}
    try:
        from ..storage import MeetingStorage
        storage = MeetingStorage(DATA_DIR)
        if storage.exists(meeting_id):
            state = storage.load(meeting_id)
            # 只传递 cleaned_text, 不再传递旧 5 类 facts 的分项计数
            state_payload["meeting_id"] = state.meeting_id
            state_payload["cleaned_text"] = state.cleaned_text[:5000] if state.cleaned_text else ""
            state_payload["cleaned_text_length"] = len(state.cleaned_text)
            state_payload["speaker_map"] = state.speaker_map
            state_payload["last_updated"] = state.last_updated
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

    # 最近 chat history（含 material-upload 事件）
    recent_chat: list[dict[str, Any]] = []
    try:
        history = _load_chat_history(meeting_id)
        recent_chat = history[-10:]
    except Exception:
        pass

    # 上传目录下的文件列表（只给路径，agent 自己用 read_file 读）
    recent_uploads: list[dict[str, Any]] = []
    try:
        upload_dir = DATA_DIR / "uploads" / meeting_id
        if upload_dir.is_dir():
            for f in sorted(upload_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
                if not f.is_file():
                    continue
                recent_uploads.append({
                    "filename": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                })
    except Exception:
        pass

    return {
        "meeting_id": meeting_id,
        "state": state_payload,
        "docs": docs,
        "recent_transcript": meta.get("transcript_segments", [])[-20:],
        "recent_metrics": meta.get("metrics", [])[-5:],
        "recent_chat_history": recent_chat,
        "recent_uploads": recent_uploads,
    }


# ── Chat agent ──

def _get_chat_agent(meeting_id: str):
    session_id = f"meeting:{meeting_id}:vp-chat"
    with _CHAT_AGENT_LOCK:
        if session_id in _CHAT_AGENT_CACHE:
            return _CHAT_AGENT_CACHE[session_id]
        from run_agent import AIAgent  # type: ignore

        # v0.22.6: Hermes vision 路由修复 — 主 chat agent 也需要 monkeypatch
        # 根因: resolve_runtime_provider("custom") 永远回到 OpenRouter (硬编码常量) →
        # _try_custom_endpoint → None → Anthropic SDK → MiniMax key → 401
        # 同 sub_session_controller.py 的修复, 但主 chat 也走这里
        _OPENROUTER_BAK = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            from hermes_cli import runtime_provider as _rhp
            _rhp.resolve_runtime_provider = lambda requested="auto", **kw: None
        except Exception:
            pass

        _CHAT_AGENT_CACHE[session_id] = AIAgent(
            session_id=session_id,
            enabled_toolsets=["terminal", "file", "vision", "web"],
            platform="subagent",
            quiet_mode=True,
            max_iterations=20,
            # 2026-07-04 (ADR-0041): 跟 doc agent 统一用 OPENAI_BASE_URL, 不用 VPBUDDY_LLM_API_BASE.
            # 这样 chat 和 doc 走同一个 LLM endpoint, parent_session_id fork 时 provider 一致.
            # ADR-0049: 模型从 .env MODEL=minimax-m3 (Hermes 统一配置)
            model=os.environ.get("MODEL"),
            base_url=os.environ.get("OPENAI_BASE_URL") or os.environ.get("VPBUDDY_LLM_API_BASE", "http://localhost:11434/v1"),
            api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY"),
            # ADR-0049: 不传 model — Hermes AIAgent 从 .env MODEL=minimax-m3 自己读
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


def _run_vp_chat(meeting_id: str, message: str, client_context: dict[str, Any] | None = None) -> dict[str, Any]:
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

    timeout = int(os.environ.get("VPBUDDY_CHAT_TIMEOUT", "300"))

    def _do_chat(p: str) -> dict[str, Any]:
        holder: dict[str, Any] = {"done": False, "response": None, "error": None}

        def _runner():
            try:
                agent = _get_chat_agent(meeting_id)
                holder["response"] = agent.chat(p)
            except Exception as e:
                holder["error"] = e
            finally:
                holder["done"] = True

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if not holder["done"]:
            return {"status": "timeout", "error": "AIAgent timeout"}
        if holder["error"]:
            return {"status": "error", "error": f"{type(holder['error']).__name__}: {str(holder['error'])[:300]}"}
        return {"status": "ok", "content": str(holder["response"] or "").strip()}

    result = _do_chat(prompt)

    if result["status"] == "timeout":
        fallback_prompt = "\n".join([
            "VP 输入了下面这句话。请直接回答，不需要过多上下文推理。",
            f"VP 输入:\n{message}",
            "如果问题跟会议内容相关,简要回答即可;如果无法确定,直接说不知道。",
        ])
        retry_result = _do_chat(fallback_prompt)
        if retry_result["status"] == "ok":
            return {
                "status": "ok",
                "source": "hermes-retry",
                "content": retry_result.get("content", ""),
                "error": None,
            }
        return {
            "status": "fallback",
            "source": "fallback",
            "content": "Hermes VP Chat 暂时超时。当前输入已记录,但未完成 Hermes 上下文推理或子 agent 调度。",
            "error": "AIAgent timeout (retry also failed)",
        }
    if result["status"] == "error":
        return {
            "status": "fallback",
            "source": "fallback",
            "content": (
                "Hermes VP Chat 当前不可用。输入已记录,服务端没有静默执行外部动作。"
                "请确认 run_agent/AIAgent 或 hermes 运行环境可用后重试。"
            ),
            "error": result.get("error", "unknown"),
        }
    return {
        "status": "ok",
        "source": "hermes",
        "content": result.get("content", ""),
        "error": None,
    }


# ── ASR 后处理 agent ──

def _get_clean_agent(meeting_id: str):
    """ASR 后处理 agent — 复用 AIAgent 模式, 同 (mid) 跨次调用复用上下文.
    prompt 从 src/vpbuddy/prompts/asr_clean.md 加载 (跟 6 子 session 一样)
    """
    session_id = f"meeting:{meeting_id}:asr-clean"
    with _CLEAN_AGENT_LOCK:
        if session_id in _CLEAN_AGENT_CACHE:
            return _CLEAN_AGENT_CACHE[session_id]
        from run_agent import AIAgent  # type: ignore

        # 加载 prompt 模板
        _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prompt_path = os.path.join(_base_dir, "prompts", "asr_clean.md")
        try:
            with open(prompt_path, encoding="utf-8") as f:
                prompt_template = f.read()
        except FileNotFoundError:
            prompt_template = "你是 VPBuddy 会议转写整理助手。"  # 兜底

        _CLEAN_AGENT_CACHE[session_id] = AIAgent(
            session_id=session_id,
            enabled_toolsets=["file"],
            platform="subagent",
            quiet_mode=True,
            max_iterations=10,
            # ADR-0049: 模型从 .env MODEL=minimax-m3 (Hermes 统一配置)
            model=os.environ.get("MODEL"),
            base_url=os.environ.get("OPENAI_BASE_URL") or os.environ.get("VPBUDDY_LLM_API_BASE", "http://localhost:11434/v1"),
            api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY"),
            ephemeral_system_prompt=prompt_template,
        )
        return _CLEAN_AGENT_CACHE[session_id]


def _run_asr_clean(meeting_id: str, raw_segments: list[dict], previous_cleaned: str = "") -> str:
    """调 LLM 整理一段 funasr ASR 原始 segments.

    输入: raw_segments 列表 (每个含 start_sec, speaker_id, text)
         previous_cleaned 上一次的整理结果 (拼接上下文)
    输出: LLM 整理后的纯文本
    失败: 返回原始拼接 (fallback, 不阻塞流)

    2026-06-29: 直接调 ollama HTTP API (/api/chat), 不走 AIAgent
    原因: AIAgent dispatch 内部固定 OpenAI 协议走 minimaxi 云端,
         `qwen3:8b` 这种本地 ollama 模型名会 400.
         ASR 整理是单轮 LLM call, 不需要 agent 框架的 tool calling.
    """
    if not raw_segments:
        return ""
    # 拼成 prompt 期望的 [MM:SS] SPEAKER_XX: text 格式
    lines = []
    for s in raw_segments:
        start = float(s.get("start_sec", 0))
        mm = int(start // 60)
        ss = start - mm * 60
        spk = s.get("speaker_id", "UNKNOWN")
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        lines.append(f"[{mm:02d}:{ss:04.1f}] {spk}: {txt}")
    raw_block = "\n".join(lines)

    # 加载 prompt
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(_base_dir, "prompts", "asr_clean.md")
    try:
        with open(prompt_path, encoding="utf-8") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        system_prompt = "你是 VPBuddy 会议转写整理助手。"

    user_msg = "\n".join([
        "请整理下面这段 funasr ASR 原始输出。",
        "",
        f"之前的整理结果 (供上下文参考):\n{previous_cleaned[:2000] if previous_cleaned else '(无, 这是会议开始)'}\n",
        f"原始 funasr segments:\n{raw_block}\n",
        "直接输出整理后的文本, 不要带 markdown 标题或解释。",
    ])

    # 直接调 ollama /api/chat
    ollama_url = os.environ.get("VPBUDDY_OLLAMA_URL", "http://localhost:11434/api/chat")
    timeout = int(os.environ.get("VPBUDDY_ASR_CLEAN_TIMEOUT", "30"))
    model = os.environ.get("VPBUDDY_LLM_MODEL")  # ADR-0049: env 配置,不写死
    if not model:
        raise RuntimeError("VPBUDDY_LLM_MODEL not set in environment")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"num_predict": 4096, "temperature": 0.1},
    }

    holder: dict[str, Any] = {"done": False, "response": None, "error": None}

    def _runner():
        try:
            req = Request(
                ollama_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            holder["response"] = data.get("message", {}).get("content", "")
        except Exception as e:
            holder["error"] = e
        finally:
            holder["done"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout + 5)  # 留 5s 余量

    if not holder["done"] or holder["error"]:
        print(f"[asr_clean] {meeting_id} LLM 整理失败/超时, fallback 原始拼接: {holder.get('error')}")
        return raw_block  # fallback: 直接返回原始拼接, 不阻塞流

    text = str(holder["response"] or "").strip()
    if not text:
        print(f"[asr_clean] {meeting_id} LLM 返回空, fallback 原始拼接")
        return raw_block

    # 2026-06-29: 截断保护 — 防 LLM 超出 num_predict 丢原话
    if len(text) > ASR_CLEAN_MAX_CHARS:
        print(f"[asr_clean] {meeting_id} 整理超长 {len(text)}>{ASR_CLEAN_MAX_CHARS}, 截断")
        text = text[:ASR_CLEAN_MAX_CHARS] + f"\n[...已截断, 原始 {len(raw_segments)} 段在 cleaned_windows 回查]"
    return text


# ── State payload ──

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
        "owner_id": getattr(state, "owner_id", ""),  # 2026-07-07 ADR-0047
        "cleaned_text_length": len(state.cleaned_text),
        "last_updated": state.last_updated,
    }
    # 2026-07-01 ADR-0021: 音频源类型, 默认 microphone
    _as = getattr(state, "audio_source", None)
    payload["audio_source"] = _as.value if _as else "microphone"
    payload["platform"] = state.platform.value if hasattr(state, "platform") else "local"
    if include_items:
        payload["items"] = (
            _items(state.requirements, "req")
            + _items(state.goals, "goal")
            + _items(state.features, "feat")
            + _items(state.risks, "risk")
            + _items(state.open_questions, "que")
        )[-100:]
    return payload


# ── Meeting list / timeline / status ──

def _validate_meeting_id(mid: str) -> tuple[bool, str]:
    """校验 meeting_id 格式 (ADR-0022). 返 (ok, err_msg)."""
    import re
    if not (3 <= len(mid) <= 48):
        return False, "会议名长度 3-48 字符"
    if not re.match(r"^[A-Za-z0-9_\-]+$", mid):
        return False, "会议名只能含字母数字下划线连字符, 无空格/中文"
    return True, ""


def list_meetings() -> list[dict]:
    """列出所有会议 + 统计"""
    if not DATA_DIR.exists():
        return []
    out = []
    for f in sorted(DATA_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix != ".json":
            continue
        # 跳过 chat history
        if f.name.endswith(".chat.json"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # 必须是 meeting state 格式 (有 meeting_id 字段)
            if "meeting_id" not in data:
                continue
            item_count = sum(len(data.get(k, [])) for k in
                             ["requirements", "goals", "features", "risks", "open_questions"])
            cleaned_len = len(data.get("cleaned_text", ""))
            out.append({
                "meeting_id": data.get("meeting_id", f.stem),
                "owner_id": data.get("owner_id", ""),  # 2026-07-07 ADR-0047
                "platform": data.get("platform", "unknown"),
                "audio_source": data.get("audio_source", "microphone"),  # 2026-07-01 ADR-0021
                "project_name": data.get("project_name"),
                "started_at": data.get("started_at"),
                "last_updated": data.get("last_updated"),
                "item_count": item_count,
                "cleaned_text_length": cleaned_len,
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


def get_status() -> dict:
    """返回 server-wide 状态 (v0.9.0)."""
    meetings = list_meetings()
    total_docs = 0
    if DOCS_DIR.exists():
        for d in DOCS_DIR.iterdir():
            if d.is_dir() and d.name not in ("decisions", "research"):
                for _ in d.rglob("*.md"):
                    total_docs += 1
                for _ in d.rglob("*.html"):
                    total_docs += 1

    kb_docs = 0
    try:
        from ..rag_backend import get_rag
        kb_docs = get_rag().count()
    except Exception:
        pass

    return {
        "stats": {
            "active_meetings": len(meetings),
            "total_docs": total_docs,
            "kb_docs": kb_docs,
        },
        "paths": {
            "data_dir": str(DATA_DIR),
            "docs_dir": str(DOCS_DIR),
            # 2026-07-02: KB_PATH undefined 历史遗留
            "kb_path": "",
            "ui_dir": str(UI_DIR),
        },
        "meetings": meetings[:5],  # 最近 5 个
    }
