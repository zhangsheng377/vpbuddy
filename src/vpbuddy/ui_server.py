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
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

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
# v0.9.0: CONTROLLER_PID_FILE / CONTROLLER_LOG 已删除 (controller 架构移除)

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

# 2026-07-05 fix(#4): per-meeting 文件锁 for stream_meta + chat_history
_meta_locks: dict[str, threading.Lock] = {}
_chat_locks: dict[str, threading.Lock] = {}
_file_lock_global = threading.Lock()


def _get_meta_lock(meeting_id: str) -> threading.Lock:
    with _file_lock_global:
        if meeting_id not in _meta_locks:
            _meta_locks[meeting_id] = threading.Lock()
        return _meta_locks[meeting_id]


def _get_chat_lock(meeting_id: str) -> threading.Lock:
    with _file_lock_global:
        if meeting_id not in _chat_locks:
            _chat_locks[meeting_id] = threading.Lock()
        return _chat_locks[meeting_id]

# 2026-06-28: ASR 后处理 agent cache — 同 (mid) 复用, 上下文拼接之前的整理结果
# 设计: 客户端只看到整理后的 transcript-segment, 原始 segments 仍存 meta["transcript_segments"]
_CLEAN_AGENT_CACHE: dict[str, Any] = {}
_CLEAN_AGENT_LOCK = threading.Lock()
# 2026-06-28: ASR 后处理窗口 — 累积 5 段 或 30s 超时 (任一满足即触发 LLM 整理)
# 5 段阈值: 单段太短 LLM 推理开销不划算; 30s 超时: 与 funasr batch 节奏对齐, 零额外延迟
ASR_CLEAN_WINDOW_SIZE = 5
ASR_CLEAN_WINDOW_TIMEOUT_S = 30.0
# 2026-06-29: 截断保护 — LLM 整理超长时截断到 N 字 (防 8b num_predict 用尽丢原话)
ASR_CLEAN_MAX_CHARS = 2000

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


def _meeting_context_for_chat(meeting_id: str) -> dict[str, Any]:
    """构建 VP Chat 上下文 (v0.10.0: 使用 cleaned_text 替代旧 facts)."""
    state_payload: dict[str, Any] = {"meeting_id": meeting_id, "cleaned_text": "", "items": []}
    try:
        from .storage import MeetingStorage
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


# 2026-06-28: ASR 后处理 agent — 复用 AIAgent 模式, 同 (mid) 跨次调用复用上下文
# prompt 从 src/vpbuddy/prompts/asr_clean.md 加载 (跟 6 子 session 一样)
def _get_clean_agent(meeting_id: str):
    session_id = f"meeting:{meeting_id}:asr-clean"
    with _CLEAN_AGENT_LOCK:
        if session_id in _CLEAN_AGENT_CACHE:
            return _CLEAN_AGENT_CACHE[session_id]
        from run_agent import AIAgent  # type: ignore

        # 加载 prompt 模板
        prompt_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "prompts", "asr_clean.md"
        )
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
    prompt_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts", "asr_clean.md"
    )
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


def list_meetings() -> list[dict]:
    """列出所有会议 + 统计"""
    if not DATA_DIR.exists():
        return []
    out = []
    # 2026-07-01: 只列 STREAM_*.json (长连接会议) + 其它 *.json 排除 stream meta / chat history
    # 实际: stream_start 创建 MeetingState, 存到 {mid}.json; chat history 存到 {mid}.chat.json
    # 老格式 (2026-06 之前) 也用 {mid}.json. 所以 glob 全部 *.json, 跳过 *.chat.json
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


def _validate_meeting_id(mid: str) -> tuple[bool, str]:
    """校验 meeting_id 格式 (ADR-0022). 返 (ok, err_msg)."""
    import re
    if not (3 <= len(mid) <= 48):
        return False, "会议名长度 3-48 字符"
    if not re.match(r"^[A-Za-z0-9_\-]+$", mid):
        return False, "会议名只能含字母数字下划线连字符, 无空格/中文"
    return True, ""


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
    # v0.9.0: controller 状态报告已删除 (controller 架构移除)

    # 数据统计
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
        from .rag_backend import get_rag
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
            # 2026-07-02: KB_PATH undefined 历史遗留 — KB 改 Chroma 嵌入式 (ADR-0019)
            # 已无独立 KB_PATH 文件, 字段保留回传空字符串让前端兼容
            "kb_path": "",
            "ui_dir": str(UI_DIR),
        },
        "meetings": meetings[:5],  # 最近 5 个
    }


# ── 模块级会议关闭函数 (v0.9.0: 供 FastAPI + VPBuddyHandler 共用) ──

def _close_meeting(meeting_id: str) -> dict:
    """关闭会议: SSE complete 事件 + 经验蒸馏 + 文档生成触发.

    被 ui_server.VPBuddyHandler._handle_meeting_close 和 FastAPI 路由共用.
    不依赖 self, 返回 dict 由调用方序列化.

    v0.9.0: 新增 task_manager 提交文档生成 (替代旧 controller 轮询).
    """
    from .realtime_server import close_meeting, push_event
    from .task_manager import get_task_manager
    from .sub_session_controller import _dispatch_kind, BATCH_DOCS_KIND, DEMO_KIND

    try:
        push_event(meeting_id, "meeting-complete", {
            "meeting_id": meeting_id,
            "status": "user_closed",
            "note": "用户主动结束 (ADR-0022)",
        })
        closed = close_meeting(meeting_id)

        # 清 proactive 节流
        try:
            from .agent_proactive import clear_throttle
            cleared = clear_throttle(meeting_id)
        except Exception:
            cleared = 0

        # 经验蒸馏 (#1)
        extracted_count = 0
        try:
            from .storage import MeetingStorage
            from .experience_store import extract_from_meeting_state, save_experiences
            storage = MeetingStorage(DATA_DIR)
            if storage.exists(meeting_id):
                state = storage.load(meeting_id)
                items = extract_from_meeting_state(meeting_id, state, meeting_title=meeting_id)
                if items:
                    save_experiences(meeting_id, items)
                    extracted_count = len(items)
                    print(f"[experience] 会议 {meeting_id} 提取 {extracted_count} 条经验候选")
        except Exception as e:
            print(f"[experience] 会议 {meeting_id} 经验提取失败: {e}")

        # v0.9.0: 通过 task_manager 提交文档生成 (替代旧 controller 轮询)
        doc_task_submitted = False
        try:
            def _doc_runner(gen_id: int, mid: str) -> dict:
                kinds = [BATCH_DOCS_KIND, DEMO_KIND]
                results = {}
                for kind in kinds:
                    try:
                        r = _dispatch_kind(mid, kind, dry_run=False)
                        results[kind] = {"triggered": r.get("triggered"), "error": r.get("error")}
                    except Exception as e:
                        results[kind] = {"triggered": False, "error": str(e)}
                return results
            get_task_manager().submit(meeting_id, _doc_runner)
            doc_task_submitted = True
        except Exception as e:
            print(f"[close_meeting] 文档生成任务提交失败: {e}")

        print(f"[close_meeting] {meeting_id}: 关闭 {closed} 个 SSE, 清 {cleared} 个 throttle, "
              f"提取 {extracted_count} 条经验, doc_task={doc_task_submitted}")
        return {
            "meeting_id": meeting_id,
            "closed_subscribers": closed,
            "proactive_cleared": cleared,
            "experiences_extracted": extracted_count,
            "doc_task_submitted": doc_task_submitted,
            "status": "closed",
        }
    except Exception as e:
        return {"error": str(e), "status": "close_failed"}

    def _500(self, msg):
        self.send_response(500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"500: {msg}".encode())

    # === v0.9.0 #9 BFF API ===

    def _handle_meeting_aggregate(self, meeting_id: str):
        """GET /api/meetings/{id}/aggregate — 会议聚合 DTO.

        一次返回: state, docs, collab, chat, 经验.
        """
        result: dict[str, Any] = {"meeting_id": meeting_id}

        # 1. State
        try:
            from .storage import MeetingStorage
            storage = MeetingStorage(DATA_DIR)
            if storage.exists(meeting_id):
                state = storage.load(meeting_id)
                result["state"] = state.model_dump(mode="json")
            else:
                result["state"] = None
        except Exception as e:
            result["state_error"] = str(e)

        # 2. Documents
        try:
            docs = [_doc_payload(meeting_id, kind) for kind in DOC_KINDS]
            result["docs"] = docs
        except Exception as e:
            result["docs_error"] = str(e)

        # 3. Collab
        try:
            from .collab import collab_stats, list_pending, list_answered, read_collab
            result["collab"] = {
                "collab": read_collab(meeting_id),
                "pending": list_pending(meeting_id),
                "answered": list_answered(meeting_id),
                "stats": collab_stats(meeting_id),
            }
        except Exception as e:
            result["collab_error"] = str(e)

        # 4. Experiences
        try:
            from .experience_store import load_experiences
            result["experiences"] = [it.to_dict() for it in load_experiences(meeting_id)]
        except Exception:
            result["experiences"] = []

        return self._json(result)

    def _handle_device_status(self):
        """GET /api/client/device-status — 设备状态.

        返回麦克风/录音/客户端版本等前端设置页需要的信息.
        """
        status: dict[str, Any] = {
            "version": __import__("..__init__", fromlist=["__version__"]).__version__,
            "audio": {
                "available": True,
                "platform": __import__("sys").platform,
            },
            "recording": {
                "active_meetings": len([
                    p.stem for p in DATA_DIR.glob("*.json")
                    if not p.name.endswith(".stream.json") and not p.name.endswith(".chat.json")
                ]),
            },
        }
        return self._json(status)


