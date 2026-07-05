"""
VPBuddy FastAPI UI Server — 替代 BaseHTTPRequestHandler 的 FastAPI 实现

从 ui_server.py 导入所有业务函数，用 FastAPI 注册等价路由树。
用法:
    python -m vpbuddy.server.fastapi_app [--port 8765] [--host 0.0.0.0]
    vpbuddy ui --fastapi
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.parse import parse_qs, urlparse

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ── 从 ui_server 导入业务函数和常量 ──
from ..ui_server import (
    # 常量
    DOC_KINDS,
    DOC_LABELS,
    DOCS_DIR,
    DATA_DIR,
    UI_DIR,
    ASR_CLEAN_WINDOW_SIZE,
    ASR_CLEAN_WINDOW_TIMEOUT_S,
    ASR_CLEAN_MAX_CHARS,
    ASR_CLEAN_DEFAULT_MODEL,
    # 元数据操作
    _load_stream_meta,
    _save_stream_meta,
    _stream_meta_path,
    # 文本处理
    _norm_text,
    _is_duplicate_segment,
    _parse_multipart,
    # 文档路径和负载
    _doc_path,
    _doc_payload,
    # Chat 历史
    _chat_path,
    _load_chat_history,
    _save_chat_history,
    _append_chat_message,
    # Chat agent
    _meeting_context_for_chat,
    _get_chat_agent,
    _run_vp_chat,
    # ASR 后处理
    _get_clean_agent,
    _run_asr_clean,
    # State / Meetings / Timeline / Status
    _state_payload,
    _validate_meeting_id,
    list_meetings,
    get_timeline,
    get_status,
)

# ── FastAPI 应用 ──
app = FastAPI(
    title="VPBuddy API",
    version="0.9.0",
    description="VPBuddy 实时会议 AI 后端 API (FastAPI 移植版)",
)

# ── CORS 配置 (默认允许所有 origin, 可通过 VPBUDDY_CORS_ORIGIN 设置) ──
_cors_origin = os.environ.get("VPBUDDY_CORS_ORIGIN", "*")
if _cors_origin == "*":
    _cors_origins = ["*"]
else:
    _cors_origins = [o.strip() for o in _cors_origin.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── uvicorn startup event: warmup 日志 ──
@app.on_event("startup")
async def startup_warmup():
    """Server 启动时预热: KB Chroma 加载 + 版本日志."""
    import logging

    logger = logging.getLogger("vpbuddy.fastapi")
    try:
        from .._version import __version__
    except Exception:
        __version__ = "unknown"
    logger.info("VPBuddy FastAPI server starting (version=%s)", __version__)
    print(f"[fastapi_app] startup: VPBuddy FastAPI v{__version__}", flush=True)

    # KB Chroma 预热
    try:
        from ..rag_backend import get_rag

        count = get_rag().count()
        logger.info("KB Chroma 预热完成, count=%d", count)
        print(f"[fastapi_app] KB Chroma 预热完成, count={count}", flush=True)
    except Exception as e:
        logger.warning("KB Chroma 预热跳过: %s", e)
        print(f"[fastapi_app] KB Chroma 预热跳过: {e}", flush=True)


# =============================================================================
# GET Routes
# =============================================================================


@app.get("/api/meetings")
def get_meetings():
    """GET /api/meetings — 会议列表"""
    meetings = list_meetings()
    return {"meetings": meetings, "count": len(meetings)}


@app.get("/api/meetings/check_id")
def get_meetings_check_id(id: str = Query(..., description="meeting_id")):
    """GET /api/meetings/check_id — 校验 meeting_id 是否可用 (ADR-0022)"""
    mid = id.strip()
    if not mid:
        raise HTTPException(status_code=400, detail="id 必填")
    ok, err = _validate_meeting_id(mid)
    if not ok:
        raise HTTPException(status_code=400, detail={"id": mid, "valid": False, "error": err})
    meeting_data_path = DATA_DIR / f"{mid}.json"
    return {"id": mid, "valid": True, "exists": meeting_data_path.exists()}


@app.get("/api/timeline")
def get_timeline_api():
    """GET /api/timeline — 全部累积项按时间倒序"""
    events = get_timeline()
    return {"events": events, "count": len(events)}


@app.get("/api/kb/search")
def get_kb_search(
    q: str = Query(""),
    meeting_id: str = Query(None),
):
    """GET /api/kb/search — 跨会议 RAG 检索 (快捷 GET 版)"""
    if not q.strip():
        return {"results": []}
    from ..kb_api import handle_kb_search

    params = {"q": [q]}
    if meeting_id:
        params["meeting_id"] = [meeting_id]
    result = handle_kb_search(params, b"")
    return result


@app.get("/api/kb/list")
def get_kb_list(
    meeting_id: str = Query(None),
):
    """GET /api/kb/list — 列出 KB 文档"""
    from ..kb_api import handle_kb_list

    params = {}
    if meeting_id:
        params["meeting_id"] = [meeting_id]
    return handle_kb_list(params)


@app.delete("/api/kb/{doc_id}")
def delete_kb_doc(doc_id: str):
    """DELETE /api/kb/{doc_id} — 删除 KB 文档"""
    from ..kb_api import handle_kb_delete

    return handle_kb_delete(f"/api/kb/{doc_id}")


@app.get("/api/status")
def get_status_api():
    """GET /api/status — Controller + 数据状态"""
    return get_status()


@app.get("/api/meetings/{meeting_id}/state")
def get_meeting_state(meeting_id: str):
    """GET /api/meetings/{id}/state — 单场会议状态 / 事实 / 转写历史 / 指标"""
    from ..storage import MeetingStorage

    try:
        storage = MeetingStorage(DATA_DIR)
        if not storage.exists(meeting_id):
            raise HTTPException(status_code=404, detail=f"meeting {meeting_id} not found")
        state = storage.load(meeting_id)
        meta = _load_stream_meta(meeting_id)
        return {
            "state": _state_payload(state, include_items=True),
            "transcript_segments": meta.get("transcript_segments", [])[-300:],
            "metrics": meta.get("metrics", [])[-100:],
            "processed_chunks": meta.get("processed_chunks", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/meetings/{meeting_id}/chat/history")
def get_meeting_chat_history(meeting_id: str):
    """GET /api/meetings/{id}/chat/history — VP Chat 历史"""
    return {
        "meeting_id": meeting_id,
        "messages": _load_chat_history(meeting_id),
    }


@app.get("/api/meetings/{meeting_id}/collab")
def get_meeting_collab(meeting_id: str):
    """GET /api/meetings/{id}/collab — 协作提问文档 (ADR-0028)"""
    try:
        from ..collab import collab_stats, list_answered, list_pending, read_collab

        return {
            "meeting_id": meeting_id,
            "collab": read_collab(meeting_id),
            "pending": list_pending(meeting_id),
            "answered": list_answered(meeting_id),
            "stats": collab_stats(meeting_id),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/meetings/{meeting_id}/aggregate")
def get_meeting_aggregate(meeting_id: str):
    """GET /api/meetings/{id}/aggregate — 会议聚合 DTO (BFF 端点)"""
    result: dict[str, Any] = {"meeting_id": meeting_id}

    # 1. State
    try:
        from ..storage import MeetingStorage

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
        from ..collab import collab_stats, list_answered, list_pending, read_collab

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
        from ..experience_store import load_experiences

        result["experiences"] = [it.to_dict() for it in load_experiences(meeting_id)]
    except Exception:
        result["experiences"] = []

    return result


@app.get("/api/client/device-status")
def get_client_device_status():
    """GET /api/client/device-status — 设备状态"""
    try:
        from .._version import __version__
    except Exception:
        __version__ = "unknown"
    status: dict[str, Any] = {
        "version": __version__,
        "audio": {
            "available": True,
            "platform": sys.platform,
        },
        "recording": {
            "active_meetings": len(
                [
                    p.stem
                    for p in DATA_DIR.glob("*.json")
                    if not p.name.endswith(".stream.json")
                    and not p.name.endswith(".chat.json")
                ]
            ),
        },
    }
    return status


@app.get("/api/meetings/{meeting_id}/docs")
def get_meeting_docs(meeting_id: str):
    """GET /api/meetings/{id}/docs — 单场会议 6 类文档正文"""
    docs = [_doc_payload(meeting_id, kind) for kind in DOC_KINDS]
    return {"meeting_id": meeting_id, "docs": docs}


@app.get("/api/meetings/{meeting_id}/docs/{kind}")
def get_meeting_doc(meeting_id: str, kind: str):
    """GET /api/meetings/{id}/docs/{kind} — 单场会议某一类文档正文"""
    if kind not in DOC_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown doc kind: {kind}")
    return _doc_payload(meeting_id, kind)


@app.get("/api/meetings/{meeting_id}/demo/versions")
def get_demo_versions(meeting_id: str):
    """GET /api/meetings/{id}/demo/versions — demo 版本列表 (ADR-0024)"""
    from ..demo_version import list_versions

    versions = list_versions(meeting_id)
    return {"meeting_id": meeting_id, "versions": versions, "count": len(versions)}


# =============================================================================
# SSE 实时事件流
# =============================================================================


@app.get("/api/meetings/{meeting_id}/events")
async def get_meeting_events(meeting_id: str, request: Request):
    """GET /api/meetings/{id}/events — SSE 实时事件流

    使用 StreamingResponse 包装 realtime_server.sse_generator (同步生成器)。
    FastAPI 自动在线程池中运行同步生成器，不阻塞事件循环。
    """
    from ..realtime_server import sse_generator

    def event_stream():
        for chunk in sse_generator(meeting_id):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# POST Routes
# =============================================================================


@app.post("/api/meetings/stream_start")
async def post_stream_start(request: Request):
    """POST /api/meetings/stream_start — 创建长连接会议

    参数通过 query string 传递:
        meeting_id: str (可选, ADR-0022 — 复用已有会议)
        audio_source: str (可选, ADR-0021 — microphone|loopback|both)
    """
    query_params = dict(request.query_params)
    from ..state import AudioSourceKind
    from ..storage import MeetingStorage

    # meeting_id (ADR-0022)
    meeting_id_in = (query_params.get("meeting_id") or "").strip()
    if meeting_id_in:
        ok, err = _validate_meeting_id(meeting_id_in)
        if not ok:
            raise HTTPException(status_code=400, detail=f"meeting_id 非法: {err}")

    # audio_source (ADR-0021)
    audio_source_str = (query_params.get("audio_source") or AudioSourceKind.MICROPHONE.value).strip().lower()
    try:
        audio_source = AudioSourceKind(audio_source_str)
    except ValueError:
        audio_source = AudioSourceKind.MICROPHONE

    # 决定最终 meeting_id
    if meeting_id_in:
        meeting_id = meeting_id_in
    else:
        meeting_id = (
            f"STREAM_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )

    # 写 state (复用 or 创建)
    from ..state import MeetingState, Platform

    storage = MeetingStorage(DATA_DIR)
    reused = bool(meeting_id_in) and storage.exists(meeting_id)
    try:
        if reused:
            state = storage.load(meeting_id)
            state.audio_source = audio_source
            state.last_updated = datetime.now().isoformat()
            storage.save(state)
        else:
            state = MeetingState(
                meeting_id=meeting_id,
                platform=Platform.LOCAL,
                audio_source=audio_source,
                project_name=f"长连接会议 {meeting_id}",
            )
            storage.save(state)
        _save_stream_meta(
            meeting_id,
            {
                "processed_chunks": [],
                "transcript_segments": [],
                "metrics": [],
                "created_at": datetime.now().isoformat(),
                "audio_source": audio_source.value,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create state failed: {e}")

    return {
        "meeting_id": meeting_id,
        "chunk_interval_sec": 30,
        "audio_source": audio_source.value,
        "reused": reused,
        "message": "Stream started, send 30s WAV chunks to /api/meetings/{id}/stream_chunk",
    }


@app.post("/api/meetings/{meeting_id}/stream_chunk")
async def post_stream_chunk(
    meeting_id: str,
    request: Request,
    sync: bool = Query(True, description="同步模式 (默认 true, ?sync=false 走异步)"),
):
    """POST /api/meetings/{id}/stream_chunk — 接收 30s WAV 切片

    multipart/form-data:
        chunk_index: int
        chunk_start_sec: float
        overlap_sec: float
        client_sent_at: float
        audio: binary (WAV 文件)
    """
    content_type = request.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=400, detail="Content-Type must be multipart/form-data")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    try:
        fields, file_data = _parse_multipart(body, content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not file_data:
        raise HTTPException(status_code=400, detail="No audio file in form")

    chunk_index = int(fields.get("chunk_index", "0") or "0")
    chunk_start_sec = float(fields.get("chunk_start_sec", "0") or "0")
    overlap_sec = float(fields.get("overlap_sec", "0") or "0")
    client_sent_at = float(fields.get("client_sent_at", "0") or "0")

    meta = _load_stream_meta(meeting_id)
    if chunk_index in meta.get("processed_chunks", []):
        return {
            "meeting_id": meeting_id,
            "chunk_index": chunk_index,
            "new_segments": [],
            "duplicate_chunk": True,
            "docs_triggered": False,
        }

    # 保存临时 WAV 文件
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(file_data)
        tmp_path = tmp.name

    sync_mode = str(sync).lower() != "false"

    if not sync_mode:
        # 异步模式: 后台线程处理, 立即返回 accepted
        threading.Thread(
            target=_process_chunk_background,
            args=(
                meeting_id, tmp_path, chunk_index, chunk_start_sec,
                overlap_sec, client_sent_at,
            ),
            daemon=True,
        ).start()
        meta.setdefault("processed_chunks", []).append(chunk_index)
        meta["processed_chunks"] = sorted(set(meta["processed_chunks"]))
        _save_stream_meta(meeting_id, meta)
        return {
            "meeting_id": meeting_id,
            "chunk_index": chunk_index,
            "status": "accepted",
            "message": "Chunk accepted, processing in background. Subscribe to /api/meetings/{id}/events for results.",
            "duplicate_chunk": False,
            "docs_triggered": True,
        }

    # 同步模式: 阻塞处理并返回完整结果
    try:
        return _process_chunk_sync(
            meeting_id, tmp_path, chunk_index, chunk_start_sec,
            overlap_sec, client_sent_at,
        )
    except Exception as e:
        import traceback

        raise HTTPException(
            status_code=500,
            detail={"error": f"Stream chunk failed: {e}", "trace": traceback.format_exc()},
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _process_chunk_sync(
    meeting_id: str,
    tmp_path: str,
    chunk_index: int,
    chunk_start_sec: float,
    overlap_sec: float,
    client_sent_at: float,
) -> dict:
    """同步处理 WAV chunk (同步模式)."""
    import time as _time

    from ..ingest import _classify, infer_speaker_map
    from ..scripts.gpu_transcribe import process
    from ..state import MeetingState, Platform, Priority
    from ..storage import MeetingStorage
    from ..task_manager import get_task_manager
    from ..sub_session_controller import _dispatch_kind, BATCH_DOCS_KIND, DEMO_KIND

    started = _time.time()
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
        existing_texts = {
            _norm_text(getattr(item, "text", ""))
            for item in (
                state.requirements + state.goals + state.features
                + state.risks + state.open_questions
            )
        }
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
    processing_ms = int((_time.time() - started) * 1000)
    end_to_end_ms = int((_time.time() - client_sent_at) * 1000) if client_sent_at else None
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

    # 通过 task_manager 提交文档生成任务
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

    # 推送 SSE 事件
    try:
        from ..realtime_server import push_event

        for s in new_segs:
            push_event(meeting_id, "transcript-segment", {
                "start_sec": s["start_sec"],
                "end_sec": s["end_sec"],
                "text": s["text"],
                "speaker_id": s["speaker_id"],
                "chunk_index": chunk_index,
                "speaker_name": spk_map.get(s["speaker_id"], "UNKNOWN"),
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
            "kinds": DOC_KINDS,
            "message": "6 docs generation triggered",
        })
    except Exception:
        pass

    return {
        "meeting_id": meeting_id,
        "chunk_index": chunk_index,
        "new_segments": [
            {
                "start_sec": s["start_sec"],
                "end_sec": s["end_sec"],
                "text": s["text"],
                "speaker_id": s["speaker_id"],
                "chunk_index": s.get("chunk_index", chunk_index),
            }
            for s in new_segs
        ],
        "state_items": _state_payload(state, include_items=False),
        "metrics": meta.get("metrics", [])[-1],
        "docs_triggered": True,
    }


def _process_chunk_background(
    meeting_id: str,
    tmp_path: str,
    chunk_index: int,
    chunk_start_sec: float,
    overlap_sec: float,
    client_sent_at: float,
):
    """后台 daemon thread 处理 WAV chunk (异步模式).

    逻辑与 _process_chunk_sync 完全一致, 但异常仅记日志.
    """
    try:
        import time as _time

        from ..ingest import _classify, infer_speaker_map
        from ..scripts.gpu_transcribe import process
        from ..state import MeetingState, Platform, Priority
        from ..storage import MeetingStorage
        from ..task_manager import get_task_manager
        from ..sub_session_controller import _dispatch_kind, BATCH_DOCS_KIND, DEMO_KIND

        started = _time.time()
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
            existing_texts = {
                _norm_text(getattr(item, "text", ""))
                for item in (
                    state.requirements + state.goals + state.features
                    + state.risks + state.open_questions
                )
            }
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
        processing_ms = int((_time.time() - started) * 1000)
        end_to_end_ms = int((_time.time() - client_sent_at) * 1000) if client_sent_at else None
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

        def _doc_runner_bg(gen_id: int, mid: str) -> dict:
            kinds = [BATCH_DOCS_KIND, DEMO_KIND]
            results = {}
            for kind in kinds:
                try:
                    r = _dispatch_kind(mid, kind, dry_run=False)
                    results[kind] = {"triggered": r.get("triggered"), "error": r.get("error")}
                except Exception as e:
                    results[kind] = {"triggered": False, "error": str(e)}
            return results

        get_task_manager().submit(meeting_id, _doc_runner_bg)

        try:
            from ..realtime_server import push_event

            # ASR 后处理窗口
            now_ts = _time.time()
            meta.setdefault("pending_clean", [])
            for s in new_segs:
                meta["pending_clean"].append({"seg": s, "received_ts": now_ts})

            should_clean = False
            if len(meta["pending_clean"]) >= ASR_CLEAN_WINDOW_SIZE:
                should_clean = True
            elif meta["pending_clean"]:
                oldest_ts = meta["pending_clean"][0]["received_ts"]
                if (now_ts - oldest_ts) >= ASR_CLEAN_WINDOW_TIMEOUT_S:
                    should_clean = True

            if should_clean:
                pending = meta.pop("pending_clean")
                pending_segs = [item["seg"] for item in pending]
                prev_cleaned = "\n".join(meta.get("cleaned_segments", [])[-3:])
                cleaned_text = _run_asr_clean(meeting_id, pending_segs, prev_cleaned)
                meta.setdefault("cleaned_segments", []).append(cleaned_text)
                truncated = "[...已截断" in cleaned_text
                meta.setdefault("cleaned_windows", []).append({
                    "window_id": len(meta.get("cleaned_windows", [])) + 1,
                    "start_sec": pending_segs[0].get("start_sec", 0),
                    "end_sec": pending_segs[-1].get("end_sec", 0),
                    "raw_segments": pending_segs,
                    "cleaned_text": cleaned_text,
                    "truncated": truncated,
                    "cleaned_at": datetime.now().isoformat(),
                    "window_segments": len(pending_segs),
                })
                _save_stream_meta(meeting_id, meta)
                first_seg = pending_segs[0]
                last_seg = pending_segs[-1]
                push_event(meeting_id, "transcript-segment", {
                    "start_sec": first_seg["start_sec"],
                    "end_sec": last_seg["end_sec"],
                    "text": cleaned_text,
                    "raw_texts": [s["text"] for s in pending_segs],
                    "speaker_ids": list({s["speaker_id"] for s in pending_segs}),
                    "chunk_index": chunk_index,
                    "speaker_name": spk_map.get(first_seg["speaker_id"], "UNKNOWN"),
                    "cleaned": True,
                    "window_segments": len(pending_segs),
                })
            else:
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
                "kinds": DOC_KINDS,
                "message": "6 docs generation triggered",
            })
        except Exception as e:
            print(f"[stream_chunk/bg] push_event error: {e}")

        print(
            f"[stream_chunk/bg] {meeting_id}/{chunk_index} done in {processing_ms}ms, "
            f"{len(new_segs)} new segments"
        )
    except Exception as e:
        import traceback

        print(f"[stream_chunk/bg] ERROR chunk_index={chunk_index}: {e}")
        print(traceback.format_exc())
        try:
            from ..realtime_server import push_event

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


@app.post("/api/meetings/{meeting_id}/upload_audio")
async def post_upload_audio(
    meeting_id: str,
    request: Request,
):
    """POST /api/meetings/{id}/upload_audio — 上传音频自动转写+入库+触发 6 docs

    multipart/form-data:
        audio: binary (或 file: binary)
    """
    content_type = request.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=400, detail="Content-Type must be multipart/form-data")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty body")

    fields, file_data = _parse_multipart(body, content_type)
    if not file_data:
        raise HTTPException(status_code=400, detail="No audio file in form (field name: 'audio' or 'file')")

    # 保存临时文件
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(file_data)
        tmp_path = tmp.name

    try:
        from ..ingest import ingest_transcript
        from ..scripts.gpu_transcribe import process
        from ..state import Platform

        transcript = process(tmp_path)
        state = ingest_transcript(
            meeting_id=meeting_id,
            transcript=transcript,
            project_name=f"上传会议 {meeting_id}",
            platform=Platform.LOCAL,
        )

        # 通过 task_manager 触发文档生成 (v0.9.0: 替代旧 controller subprocess)
        try:
            from ..task_manager import get_task_manager
            from ..sub_session_controller import _dispatch_kind, BATCH_DOCS_KIND, DEMO_KIND
            def _doc_runner(gen_id: int, mid: str) -> dict:
                results = {}
                for kind in [BATCH_DOCS_KIND, DEMO_KIND]:
                    try:
                        r = _dispatch_kind(mid, kind, dry_run=False)
                        results[kind] = {"triggered": r.get("triggered"), "error": r.get("error")}
                    except Exception as e:
                        results[kind] = {"triggered": False, "error": str(e)}
                return results
            get_task_manager().submit(meeting_id, _doc_runner)
        except Exception as e:
            print(f"[fastapi] 文档生成任务提交失败: {e}")

        return {
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
        }
    except Exception as e:
        import traceback

        raise HTTPException(
            status_code=500,
            detail={"error": f"Processing failed: {e}", "trace": traceback.format_exc()},
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.post("/api/meetings/{meeting_id}/stream_stop")
def post_stream_stop(meeting_id: str):
    """POST /api/meetings/{id}/stream_stop — 停止录音, 关闭 SSE 订阅者 (ADR-0022)"""
    try:
        from ..realtime_server import close_meeting

        closed = close_meeting(meeting_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "meeting_id": meeting_id,
        "closed_subscribers": closed,
        "message": "Stream stopped, SSE subscribers closed",
    }


@app.post("/api/meetings/{meeting_id}/chat")
async def post_chat(meeting_id: str, request: Request):
    """POST /api/meetings/{id}/chat — VP 自由输入接 chat agent

    支持 JSON 路径和 multipart/form-data 路径 (ADR-0023).
    """
    from ..kb_api import handle_chat_upload

    content_type = request.headers.get("Content-Type", "")

    # Multipart 分支 (ADR-0023 Phase 6)
    if content_type.startswith("multipart/form-data"):
        body = await request.body()
        upload_result = handle_chat_upload(body, content_type, meeting_id)
        if upload_result.get("error"):
            status_code = upload_result.get("status", 400)
            if status_code != 200:
                raise HTTPException(status_code=status_code, detail=upload_result)

        text = upload_result.get("text", "")
        files_meta = upload_result.get("files", [])
        if files_meta:
            attach_summary = ", ".join(
                f.get("filename", "?")
                for f in files_meta
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
            from ..realtime_server import push_event

            push_event(meeting_id, "chat-message", user_msg)
        except Exception:
            pass

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
            from ..realtime_server import push_event

            push_event(meeting_id, "chat-message", assistant_msg)
        except Exception:
            pass

        return {
            "meeting_id": meeting_id,
            "upload": upload_result,
            "user_message": user_msg,
            "assistant_message": assistant_msg,
            "status": result["status"],
            "source": result["source"],
            "error": result.get("error"),
        }

    # JSON 路径 (原行为, 向后兼容)
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = str(payload.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    client_context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    user_msg = _append_chat_message(
        meeting_id,
        "user",
        message,
        source="client",
        extra={"context": client_context},
    )
    try:
        from ..realtime_server import push_event

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
        from ..realtime_server import push_event

        push_event(meeting_id, "chat-message", assistant_msg)
    except Exception:
        pass

    return {
        "meeting_id": meeting_id,
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        "status": result["status"],
        "source": result["source"],
        "error": result.get("error"),
    }


@app.post("/api/meetings/{meeting_id}/close")
def post_meeting_close(meeting_id: str):
    """POST /api/meetings/{id}/close — 主动结束会议 (ADR-0022)

    1. push_event("meeting-complete")
    2. close_meeting (SSE 订阅者退出)
    3. clear proactive throttle
    4. 经验蒸馏 (v0.9.0 #1)
    """
    try:
        from ..realtime_server import close_meeting, push_event

        push_event(meeting_id, "meeting-complete", {
            "meeting_id": meeting_id,
            "status": "user_closed",
            "note": "用户主动结束 (ADR-0022)",
        })
        closed = close_meeting(meeting_id)

        # 清 proactive 节流
        try:
            from ..agent_proactive import clear_throttle

            cleared = clear_throttle(meeting_id)
        except Exception:
            cleared = 0

        # 经验蒸馏
        extracted_count = 0
        try:
            from ..experience_store import extract_from_meeting_state, save_experiences
            from ..storage import MeetingStorage

            storage = MeetingStorage(DATA_DIR)
            if storage.exists(meeting_id):
                state = storage.load(meeting_id)
                items = extract_from_meeting_state(meeting_id, state, meeting_title=meeting_id)
                if items:
                    save_experiences(meeting_id, items)
                    extracted_count = len(items)
        except Exception:
            pass

        return {
            "meeting_id": meeting_id,
            "closed_subscribers": closed,
            "proactive_cleared": cleared,
            "experiences_extracted": extracted_count,
            "status": "closed",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/meetings/{meeting_id}/collab/ask")
def post_collab_ask(
    meeting_id: str,
    section: str = Query(..., description="文档章节"),
    question: str = Query(..., description="问题内容"),
    asker: str = Query("agent", description="提问方"),
):
    """POST /api/meetings/{id}/collab/ask — 协作提问 (ADR-0028)"""
    if not section or not question:
        raise HTTPException(status_code=400, detail="section 和 question 必填")

    from ..collab import ask_question

    result = ask_question(meeting_id, section, question, asker=asker)
    if not result.get("ok"):
        status_code = 400
        if result.get("status") == "duplicate":
            status_code = 200
        raise HTTPException(status_code=status_code, detail=result)

    try:
        from ..realtime_server import push_event

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

    return result


@app.post("/api/meetings/{meeting_id}/collab/answer")
def post_collab_answer(
    meeting_id: str,
    qid: str = Query(..., description="问题 ID"),
    answer: str = Query(..., description="回答内容"),
    answerer: str = Query("VP", description="回答方"),
):
    """POST /api/meetings/{id}/collab/answer — 协作回答 (ADR-0028)"""
    if not qid or not answer:
        raise HTTPException(status_code=400, detail="qid 和 answer 必填")

    from ..collab import answer_question

    result = answer_question(meeting_id, qid, answer, answerer=answerer)
    if not result.get("ok"):
        status_code = 404 if result.get("status") == "not_found" else 400
        raise HTTPException(status_code=status_code, detail=result)

    try:
        from ..realtime_server import push_event

        push_event(meeting_id, "collab-update", {
            "action": "answer",
            "qid": qid,
            "answer": answer,
            "answerer": answerer,
            "status": "answered",
        })
    except Exception:
        pass

    return result


@app.post("/api/kb/search")
async def post_kb_search(request: Request):
    """POST /api/kb/search — KB 检索 (POST with JSON body)"""
    body = await request.body()
    query_params = dict(request.query_params)
    from ..kb_api import handle_kb_search

    result = handle_kb_search(query_params, body)
    return result


@app.post("/api/kb/upload")
async def post_kb_upload(request: Request):
    """POST /api/kb/upload — 上传文件进 KB (multipart)"""
    content_type = request.headers.get("Content-Type", "")
    body = await request.body()
    from ..kb_api import handle_kb_upload

    result = handle_kb_upload(body, content_type)
    status = result.get("status", 200)
    if status != 200:
        raise HTTPException(status_code=status, detail=result)
    return result


# =============================================================================
# 内部端点 (env-guarded)
# =============================================================================


@app.post("/api/_e2e/check_docs_complete")
def post_e2e_check_docs_complete(
    mid: str = Query(..., description="meeting_id"),
):
    """POST /api/_e2e/check_docs_complete — E2E 测试端点 (env-guarded)"""
    if os.environ.get("VPBUDDY_E2E") != "1":
        raise HTTPException(status_code=404, detail="Not found")
    from ..ui_server_helpers import check_all_docs_stored_notify

    ok = check_all_docs_stored_notify(mid)
    return {"meeting_id": mid, "all_stored": ok}


# =============================================================================
# 前端契约路由别名 (v0.9.0 BFF bridge) — 适配前端 vpbuddy-frontend API.md
# 前端路径: /meetings/... → 内部映射到 /api/meetings/...
# =============================================================================

# GET /meetings — 会议工作台列表
@app.get("/meetings")
async def fe_list_meetings():
    """GET /meetings → GET /api/meetings (wrap)"""
    from ..ui_server import list_meetings
    meetings = list_meetings()
    return {"meetings": meetings, "count": len(meetings)}

# GET /meetings/{meeting_id} — 会议详情聚合
@app.get("/meetings/{meeting_id}")
async def fe_get_meeting(meeting_id: str):
    """GET /meetings/:id → 聚合 state + docs + collab + experiences"""
    from ..storage import MeetingStorage, StorageError
    result: dict[str, Any] = {"id": meeting_id}

    try:
        storage = MeetingStorage(DATA_DIR)
        if storage.exists(meeting_id):
            state = storage.load(meeting_id)
            result["state"] = state.model_dump(mode="json")
        else:
            result["state"] = None
    except Exception as e:
        result["state_error"] = str(e)

    # Docs
    try:
        docs = [{"kind": k, "label": DOC_LABELS.get(k, k), "path": str(DOCS_DIR / meeting_id / f"{k}.md")}
                for k in DOC_KINDS]
        result["docs"] = docs
    except Exception as e:
        result["docs_error"] = str(e)

    # Transcript segments (from stream meta)
    try:
        meta_path = DATA_DIR / f"{meeting_id}.stream.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        result["transcript_segments"] = meta.get("transcript_segments", [])
    except Exception:
        result["transcript_segments"] = []

    return result

# GET /meetings/{meeting_id}/transcript-segments — 转写片段
@app.get("/meetings/{meeting_id}/transcript-segments")
async def fe_transcript_segments(meeting_id: str):
    """GET /meetings/:id/transcript-segments → 从 stream meta 提取"""
    meta_path = DATA_DIR / f"{meeting_id}.stream.json"
    if not meta_path.exists():
        return {"segments": [], "count": 0}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        segments = meta.get("transcript_segments", [])
        return {"segments": segments, "count": len(segments)}
    except Exception:
        return {"segments": [], "count": 0}

# POST /meetings/{meeting_id}/recording/start — 开始录音
@app.post("/meetings/{meeting_id}/recording/start")
async def fe_recording_start(meeting_id: str):
    """POST /meetings/:id/recording/start → POST /api/meetings/stream_start"""
    from ..ui_server import _handle_stream_start
    # 复用 stream_start handler
    result = _handle_stream_start(meeting_id=meeting_id)
    return {"status": "recording", "started_at": datetime.now().isoformat(), "detail": result}

# POST /meetings/{meeting_id}/recording/stop — 停止录音
@app.post("/meetings/{meeting_id}/recording/stop")
async def fe_recording_stop(meeting_id: str):
    """POST /meetings/:id/recording/stop → POST /api/meetings/:id/stream_stop"""
    from ..ui_server import _handle_stream_stop
    result = _handle_stream_stop(meeting_id)
    return {"status": "stopped", "ended_at": datetime.now().isoformat(), "detail": result}

# GET /meetings/{meeting_id}/deliverables — 交付物列表
@app.get("/meetings/{meeting_id}/deliverables")
async def fe_list_deliverables(meeting_id: str):
    """GET /meetings/:id/deliverables → GET /api/meetings/:id/docs (wrap)"""
    from ..ui_server import _doc_payload
    docs = []
    for kind in DOC_KINDS:
        payload = _doc_payload(meeting_id, kind)
        docs.append({
            "id": f"del-{meeting_id}-{kind}",
            "meetingId": meeting_id,
            "type": kind,
            "name": DOC_LABELS.get(kind, kind),
            "version": payload.get("version", "1"),
            "status": "draft",
            "updatedAt": payload.get("updated_at", ""),
        })
    return {"deliverables": docs, "count": len(docs)}

# GET /deliverables/{deliverable_id} — 交付物详情
@app.get("/deliverables/{deliverable_id}")
async def fe_get_deliverable(deliverable_id: str):
    """GET /deliverables/:id → parse {meetingId}:{kind} → file content"""
    # 格式: del-{meeting_id}-{kind}
    parts = deliverable_id.split("-", 2)
    if len(parts) < 3:
        raise HTTPException(status_code=400, detail=f"Invalid deliverable_id: {deliverable_id}, expected del-{meeting_id}-{kind}")
    meeting_id, kind = parts[1], parts[2]
    doc_path = DOCS_DIR / meeting_id / f"{kind}.md"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"Deliverable {kind} not found for meeting {meeting_id}")
    content = doc_path.read_text(encoding="utf-8")
    return {
        "id": deliverable_id,
        "meetingId": meeting_id,
        "type": kind,
        "name": DOC_LABELS.get(kind, kind),
        "content": content,
        "updatedAt": datetime.fromtimestamp(doc_path.stat().st_mtime).isoformat(),
    }

# GET /meetings/{meeting_id}/events — 会议事件 (SSE)
# 已通过 GET /api/meetings/{meeting_id}/events 提供 SSE

# GET /client/device-status — 设备状态 (已通过 /api/client/device-status 提供)

# POST /meetings/{meeting_id}/archive — 结束会议并归档
@app.post("/meetings/{meeting_id}/archive")
async def fe_archive_meeting(meeting_id: str):
    """POST /meetings/:id/archive → POST /api/meetings/:id/close + 归档信息"""
    from ..ui_server import _close_meeting
    close_result = _close_meeting(meeting_id)
    # 附加归档信息
    docs_list = []
    for kind in DOC_KINDS:
        doc_path = DOCS_DIR / meeting_id / f"{kind}.md"
        if doc_path.exists():
            docs_list.append({"kind": kind, "label": DOC_LABELS.get(kind, kind), "size": doc_path.stat().st_size})
    return {
        "meetingId": meeting_id,
        "status": "archived",
        "closed": close_result,
        "deliverables": docs_list,
        "summary": f"Meeting {meeting_id} archived with {len(docs_list)} deliverables",
    }


# =============================================================================
# 静态文件服务
# =============================================================================

# UI 目录: 根路径 / → index.html
@app.get("/")
async def serve_ui_root():
    """GET / — 返回 UI shell (index.html)"""
    index_path = UI_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="UI not found")
    return FileResponse(str(index_path))


# 文档目录: /docs/* → StaticFiles
_docs_static_path = DOCS_DIR
if _docs_static_path.exists() and _docs_static_path.is_dir():
    app.mount("/docs", StaticFiles(directory=str(_docs_static_path)), name="docs")


# =============================================================================
# main() 入口
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    """FastAPI UI server 主入口 — `python -m vpbuddy.server.fastapi_app`

    支持参数:
        --port PORT    端口 (默认 8765)
        --host HOST    绑定地址 (默认 0.0.0.0)
        --fastapi      (占位参数, 与 vpbuddy ui --fastapi 兼容)
    """
    parser = argparse.ArgumentParser(description="VPBuddy FastAPI UI server")
    parser.add_argument("--port", type=int, default=8765, help="端口(默认 8765)")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址(默认 0.0.0.0)")
    parser.add_argument(
        "--fastapi",
        action="store_true",
        default=True,
        help="占位参数, 与 vpbuddy ui --fastapi 兼容",
    )
    args = parser.parse_args(argv)

    # KB Chroma 预热
    try:
        from ..rag_backend import get_rag

        get_rag().count()
    except Exception:
        pass

    # 打印版本
    try:
        from .._version import __version__
    except Exception:
        __version__ = "unknown"
    print(f"VPBuddy FastAPI UI server version: {__version__}", flush=True)
    print("VPBuddy FastAPI UI server 启动", flush=True)
    print(f"   UI:    http://{args.host}:{args.port}/", flush=True)
    print(f"   DATA:  {DATA_DIR}", flush=True)
    print(f"   DOCS:  {DOCS_DIR}", flush=True)

    # uvicorn 启动
    uvicorn.run(
        "vpbuddy.server.fastapi_app:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )
    return 0


# =============================================================================
# __all__ 导出
# =============================================================================

__all__ = [
    "app",
    "main",
]

if __name__ == "__main__":
    sys.exit(main())
