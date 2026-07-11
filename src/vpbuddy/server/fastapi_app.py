"""
VPBuddy FastAPI UI Server — 替代 BaseHTTPRequestHandler 的 FastAPI 实现

从 ui_server.py 导入所有业务函数，用 FastAPI 注册等价路由树。
用法:
    python -m vpbuddy.server.fastapi_app [--port 8765] [--host 0.0.0.0]
    vpbuddy ui --fastapi

错误响应格式约定:
  HTTPException detail 统一使用 dict: {"error": "描述", "status": HTTP状态码}
  FastAPI 自动将其序列化为 JSON。旧端点有少量直接用 str 的 legacy 格式，
  新端点一律遵循 dict 格式。
"""
from __future__ import annotations

import argparse
import json
import logging
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
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── 从 api_utils 导入业务函数和常量 (service layer extracted from ui_server) ──
from ..server.api_utils import (
    # 常量 (via config)
    DOC_KINDS,
    DOC_LABELS,
    DOCS_DIR,
    DATA_DIR,
    UI_DIR,
    ASR_CLEAN_WINDOW_SIZE,
    ASR_CLEAN_WINDOW_TIMEOUT_S,
    ASR_CLEAN_MAX_CHARS,
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
    # State / Meetings / Timeline / Status
    _state_payload,
    _validate_meeting_id,
    list_meetings,
    get_timeline,
    get_status,
    # 安全推送
    safe_push_event,
)
from .config import (
    MAX_CHAT_MESSAGE_LENGTH,
    MAX_MEETING_ID_LENGTH,
    MAX_UPLOAD_SIZE,
)

# ── 材料存储 ──
from ..server import material_storage

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
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── 速率限制中间件 (轻量级令牌桶, 无外部依赖) ──
from .rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)


# ── 统一异常处理器 ──
_fastapi_logger = logging.getLogger("vpbuddy.fastapi")


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """统一 HTTPException 响应格式: {"error": str, "status": int}"""
    detail = exc.detail
    if isinstance(detail, dict):
        # 已是 dict 格式, 确保有 status 字段
        detail.setdefault("status", exc.status_code)
        return JSONResponse(status_code=exc.status_code, content=detail)
    # str 格式 (legacy), 包装成统一 dict
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(detail), "status": exc.status_code},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常: 统一返回 500, 不泄露 traceback."""
    _fastapi_logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误", "status": 500},
    )


# ── 全局文档踢动器 (模块级 daemon thread, 独立于 WS 生命周期) ──
import threading as _threading_gkd
import hashlib as _hashlib_gkd
import time as _time_gkd
import logging as _logging_gkd
from concurrent.futures import ThreadPoolExecutor as _TPE_gkd
from ..task_manager import get_task_manager as _get_tm_gkd
from ..storage import MeetingStorage as _MS_gkd
from ..sub_session_controller import _dispatch_kind as _dk_gkd, BATCH_DOCS_KIND as _bk_gkd, DEMO_KIND as _dmk_gkd

_gkd_logger = _logging_gkd.getLogger("vpbuddy.gkd")
_gkd_st = _MS_gkd(DATA_DIR)
_gkd_last: dict[str, str] = {}
_gkd_first = True

def _gkd_runner(gen_id: int, mid: str):
    try:
        with _TPE_gkd(max_workers=2) as ex:
            futures = {k: ex.submit(_dk_gkd, mid, k, False) for k in [_bk_gkd, _dmk_gkd]}
            for f in futures.values():
                try:
                    f.result(timeout=300)
                except Exception:
                    pass
    except Exception:
        pass

def _gkd_loop():
    global _gkd_first
    print("[gkd] loop started", flush=True)
    _tm = _get_tm_gkd()
    while True:
        _time_gkd.sleep(6)
        try:
            all_mids = _gkd_st.list_meetings()
            recent = [m for m in all_mids if not m.endswith((".chat", ".stream"))][:20]
            print(f"[gkd] scanning {len(recent)} meetings (of {len(all_mids)} total)", flush=True)
            for mid in recent:
                try:
                    state = _gkd_st.load(mid)
                except Exception:
                    continue
                cur = state.cleaned_text or ""
                if len(cur) <= 50:
                    continue
                cur_hash = _hashlib_gkd.md5(cur.encode()).hexdigest()
                # v0.22.5 #35 P1: 也 hash 最新 demo 内容 — cleaned_text 微小变化不一定需要重新生成
                try:
                    from ..demo_version import latest_demo_content_hash as _ldh_gkd
                    _dh = _ldh_gkd(mid) or ""
                    cur_hash = _hashlib_gkd.md5((cur + _dh).encode()).hexdigest()
                except Exception:
                    pass
                prev = _gkd_last.get(mid, "")
                if cur_hash != prev or _gkd_first:
                    _gkd_last[mid] = cur_hash
                    print(f"[gkd] triggering docs for meeting={mid} len={len(cur)}", flush=True)
                    _tm.submit(mid, _gkd_runner)
            _gkd_first = False
            # v0.22.5 #35 P2: 每轮扫描顺便清理孤儿 SSE subscriber
            try:
                from ..realtime_server import cleanup_meetings_without_subscribers as _cu_gkd
                _orphan = _cu_gkd()
                if _orphan:
                    print(f"[gkd] cleaned {_orphan} orphan subscribers", flush=True)
            except Exception:
                pass
        except Exception:
            _gkd_logger.warning("loop error", exc_info=True)
            print(f"[gkd] loop error", flush=True)

_gkd_thread = _threading_gkd.Thread(target=_gkd_loop, daemon=True, name="gkd")
_gkd_thread.start()
_gkd_logger.info("daemon thread started, scanning every 6s")
print("[fastapi_app] global_kick_docs daemon thread started", flush=True)

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

    # 材料存储初始化
    try:
        material_storage.init(DATA_DIR)
    except Exception as e:
        logger.warning("材料存储初始化失败: %s", e)
        print(f"[fastapi_app] 材料存储初始化失败: {e}", flush=True)


# =============================================================================
# Auth dependency
# =============================================================================

_auth_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_auth_scheme),
) -> dict:
    """FastAPI dependency: 从 Bearer token 提取 user_id."""
    if credentials is None:
        raise HTTPException(status_code=401, detail={"error": "请先登录", "status": 401})
    from .auth import verify_token

    user = verify_token(credentials.credentials)
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "token 无效或已过期", "status": 401})
    return user


def _require_meeting_owner(meeting_id: str, user: dict, storage=None):
    """ADR-0050: 验证当前用户是会议 owner, 否则 403."""
    from ..storage import MeetingStorage as _MS
    storage = storage or _MS(DATA_DIR)
    if not storage.exists(meeting_id):
        raise HTTPException(status_code=404, detail=f"meeting {meeting_id} not found")
    state = storage.load(meeting_id)
    owner = getattr(state, "owner_id", "")
    if owner != user.get("user_id", ""):
        raise HTTPException(status_code=403, detail="access denied")
    return state


# =============================================================================
# Auth Routes
# =============================================================================


@app.post("/api/auth/register")
async def auth_register(request: Request):
    """POST /api/auth/register — 注册"""
    body = await request.json()
    email = (body.get("email") or "").strip()
    password = (body.get("password") or "").strip()
    from .auth import register_user

    result = register_user(email, password, data_dir=str(DATA_DIR))
    status = result.pop("status", 200)
    if status != 200:
        raise HTTPException(status_code=status, detail=result)
    return result


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """POST /api/auth/login — 登录"""
    body = await request.json()
    email = (body.get("email") or "").strip()
    password = (body.get("password") or "").strip()
    from .auth import login_user

    result = login_user(email, password, data_dir=str(DATA_DIR))
    status = result.pop("status", 200)
    if status != 200:
        raise HTTPException(status_code=status, detail=result)
    return result


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    """GET /api/auth/me — 验证当前用户"""
    from .auth import get_user_by_id

    info = get_user_by_id(user["user_id"], data_dir=str(DATA_DIR))
    if info is None:
        raise HTTPException(status_code=404, detail={"error": "用户不存在", "status": 404})
    return info


# =============================================================================
# GET Routes
# =============================================================================


@app.get("/api/meetings")
def get_meetings(user: dict = Depends(get_current_user)):
    """GET /api/meetings — 会议列表"""
    meetings = list_meetings()
    # 按 owner 过滤
    own = [m for m in meetings if m.get("owner_id") == user["user_id"]]
    return {"meetings": own, "count": len(own)}


# ══════════════════════════════════════════════════════════════════
# AI Settings (#21) — 每用户模型配置
# ══════════════════════════════════════════════════════════════════

@app.get("/api/settings/ai")
def get_ai_settings(user: dict = Depends(get_current_user)):
    """GET /api/settings/ai — 获取当前用户 AI 配置 (api_key 脱敏)."""
    from .ai_settings import load_settings, mask_key

    s = load_settings(user["user_id"])
    if s is None:
        return {
            "provider": "",
            "model": "",
            "base_url": "",
            "api_key_configured": False,
            "status": "not_configured",
        }
    return {
        "provider": s.get("provider", ""),
        "model": s.get("model", ""),
        "base_url": s.get("base_url", ""),
        "api_key_masked": mask_key(s.get("api_key", "")),
        "api_key_configured": bool(s.get("api_key")),
        "updated_at": s.get("updated_at"),
    }


@app.put("/api/settings/ai")
async def put_ai_settings(request: Request, user: dict = Depends(get_current_user)):
    """PUT /api/settings/ai — 保存当前用户 AI 配置."""
    from datetime import datetime, timezone
    from .ai_settings import save_settings

    body = await request.json()
    data = {
        "provider": str(body.get("provider", "")).strip(),
        "model": str(body.get("model", "")).strip(),
        "base_url": str(body.get("base_url", "")).strip().rstrip("/"),
        "api_key": str(body.get("api_key", "")).strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_settings(user["user_id"], data)
    return {"status": "saved", "updated_at": data["updated_at"]}


@app.post("/api/settings/ai/test")
async def post_ai_settings_test(user: dict = Depends(get_current_user)):
    """POST /api/settings/ai/test — 测试当前 AI 配置连接是否可用."""
    from .ai_settings import load_settings, mask_key

    s = load_settings(user["user_id"])
    if not s or not s.get("model"):
        return {"status": "failed", "error": "请先保存 AI 配置", "connected": False}

    t0 = __import__("time").time()
    try:
        from run_agent import AIAgent
        agent = AIAgent(
            session_id=f"ai-test-{user['user_id'][:12]}",
            model=s["model"],
            base_url=s.get("base_url") or None,
            api_key=s.get("api_key") or None,
            provider=s.get("provider") or None,
            quiet_mode=True,
            max_iterations=2,
            enabled_toolsets=[],
            ephemeral_system_prompt="只回复 OK。不要做任何其他事。不要调用工具。",
        )
        reply = agent.chat("回复 OK")
        elapsed = round(__import__("time").time() - t0, 2)
        return {
            "status": "connected",
            "connected": True,
            "model": s["model"],
            "provider": s.get("provider", ""),
            "elapsed_ms": int(elapsed * 1000),
        }
    except Exception as e:
        elapsed = round(__import__("time").time() - t0, 2)
        return {
            "status": "failed",
            "connected": False,
            "error": str(e)[:500],
            "model": s.get("model", ""),
            "elapsed_ms": int(elapsed * 1000),
        }


# ══════════════════════════════════════════════════════════════════
# Experience (#1) — 经验候选 API
# ══════════════════════════════════════════════════════════════════

@app.get("/api/experiences")
def get_experiences(user: dict = Depends(get_current_user)):
    """GET /api/experiences — 当前用户已确认经验列表."""
    from ..experience_store import get_approved_experiences
    items = get_approved_experiences()
    return {
        "experiences": [it.to_dict() for it in items],
        "count": len(items),
    }


@app.get("/api/experiences/candidates")
def get_experience_candidates(
    meeting_id: str = Query(..., description="会议 ID"),
    user: dict = Depends(get_current_user),
):
    """GET /api/experiences/candidates?meeting_id=X — 某会议的经验候选."""
    _require_meeting_owner(meeting_id, user)
    from ..experience_store import load_experiences
    items = load_experiences(meeting_id)
    return {
        "meeting_id": meeting_id,
        "candidates": [it.to_dict() for it in items],
        "count": len(items),
    }


@app.post("/api/experiences/{item_id}/approve")
async def post_approve_experience(item_id: str, request: Request, user: dict = Depends(get_current_user)):
    """POST /api/experiences/{id}/approve — 确认一条经验."""
    body = await request.json()
    meeting_id = body.get("meeting_id", "")
    if not meeting_id:
        raise HTTPException(status_code=400, detail="meeting_id is required")
    _require_meeting_owner(meeting_id, user)

    from ..experience_store import approve_experience
    ok = approve_experience(item_id, meeting_id)
    return {"approved": ok, "item_id": item_id}


@app.post("/api/experiences/{item_id}/reject")
async def post_reject_experience(item_id: str, request: Request, user: dict = Depends(get_current_user)):
    """POST /api/experiences/{id}/reject — 拒绝一条经验候选."""
    body = await request.json()
    meeting_id = body.get("meeting_id", "")
    if not meeting_id:
        raise HTTPException(status_code=400, detail="meeting_id is required")
    _require_meeting_owner(meeting_id, user)

    from ..experience_store import reject_experience
    ok = reject_experience(item_id, meeting_id)
    return {"rejected": ok, "item_id": item_id}


@app.get("/api/meetings/check_id")
def get_meetings_check_id(id: str = Query(..., description="meeting_id"), user: dict = Depends(get_current_user)):
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
def get_timeline_api(user: dict = Depends(get_current_user)):
    """GET /api/timeline — 全部累积项按时间倒序"""
    events = get_timeline()
    return {"events": events, "count": len(events)}


@app.get("/api/kb/search")
def get_kb_search(
    q: str = Query(""),
    meeting_id: str = Query(None),
    user: dict = Depends(get_current_user),
):
    """GET /api/kb/search — 跨会议 RAG 检索 (按 user_id 隔离)"""
    if not q.strip():
        return {"results": []}
    from ..kb_api import handle_kb_search

    params = {"q": [q]}
    if meeting_id:
        params["meeting_id"] = [meeting_id]
    result = handle_kb_search(params, b"", user_id=user["user_id"])
    return result


@app.get("/api/kb/list")
def get_kb_list(
    meeting_id: str = Query(None),
    user: dict = Depends(get_current_user),
):
    """GET /api/kb/list — 列出 KB 文档 (#20: 返回元数据含 scope/labels/meeting_callable)"""
    from ..kb_api import handle_kb_list

    params = {}
    if meeting_id:
        params["meeting_id"] = [meeting_id]
    return handle_kb_list(params, user_id=user["user_id"])


@app.delete("/api/kb/{doc_id}")
def delete_kb_doc(doc_id: str, user: dict = Depends(get_current_user)):
    """DELETE /api/kb/{doc_id} — 删除 KB 文档 (需归属校验)"""
    from ..kb_api import handle_kb_delete

    return handle_kb_delete(f"/api/kb/{doc_id}", user_id=user["user_id"])


@app.get("/api/kb/{doc_id}/file")
def get_kb_doc_file(doc_id: str, user: dict = Depends(get_current_user)):
    """GET /api/kb/{doc_id}/file — 下载 KB 文档原始文件 (需认证 + owner 校验)"""
    from ..kb_api import get_kb_file_path

    fp, meeting_id = get_kb_file_path(doc_id)
    if fp is None or not fp.exists():
        raise HTTPException(status_code=404, detail=f"KB file not found: {doc_id}")
    if meeting_id:
        _require_meeting_owner(meeting_id, user)
    return FileResponse(
        str(fp),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fp.name}"'},
    )


@app.get("/healthz")
def get_healthz():
    """GET /healthz — 健康检查 (无需认证)"""
    return {"ok": True}


@app.get("/api/status")
def get_status_api(user: dict = Depends(get_current_user)):
    """GET /api/status — Controller + 数据状态"""
    return get_status()


@app.get("/api/meetings/{meeting_id}/state")
def get_meeting_state(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/state — 单场会议状态 / 事实 / 转写历史 / 指标"""
    try:
        state = _require_meeting_owner(meeting_id, user)
        meta = _load_stream_meta(meeting_id)
        return {
            "state": _state_payload(state, include_items=True),
            "transcript_segments": meta.get("transcript_segments", [])[-300:],
            "metrics": meta.get("metrics", [])[-100:],
            "processed_chunks": meta.get("processed_chunks", []),
            "materials": material_storage.list_materials(meeting_id),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/meetings/{meeting_id}/chat/history")
def get_meeting_chat_history(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/chat/history — VP Chat 历史"""
    _require_meeting_owner(meeting_id, user)
    return {
        "meeting_id": meeting_id,
        "messages": _load_chat_history(meeting_id),
    }


@app.get("/api/meetings/{meeting_id}/collab")
def get_meeting_collab(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/collab — 协作提问文档 (ADR-0028)"""
    _require_meeting_owner(meeting_id, user)
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
def get_meeting_aggregate(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/aggregate — 会议聚合 DTO (BFF 端点)"""
    _require_meeting_owner(meeting_id, user)
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
def get_client_device_status(user: dict = Depends(get_current_user)):
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
def get_meeting_docs(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/docs — 单场会议 6 类文档正文"""
    _require_meeting_owner(meeting_id, user)
    docs = [_doc_payload(meeting_id, kind) for kind in DOC_KINDS]
    return {"meeting_id": meeting_id, "docs": docs}


@app.get("/api/meetings/{meeting_id}/docs/{kind}")
def get_meeting_doc(meeting_id: str, kind: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/docs/{kind} — 单场会议某一类文档正文"""
    _require_meeting_owner(meeting_id, user)
    if kind not in DOC_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown doc kind: {kind}")
    return _doc_payload(meeting_id, kind)


@app.get("/api/meetings/{meeting_id}/docs/{kind}/download")
def get_meeting_doc_download(meeting_id: str, kind: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/docs/{kind}/download — 下载文档文件 (归档/导出)"""
    if kind not in DOC_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown doc kind: {kind}")
    _require_meeting_owner(meeting_id, user)
    path = _doc_path(meeting_id, kind)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"doc file not found: {meeting_id}/{kind}")
    filename = "demo.html" if kind == "demo" else f"{kind}.md"
    return FileResponse(
        str(path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/meetings/{meeting_id}/demo/versions")
def get_demo_versions(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/demo/versions — demo 版本列表 (ADR-0024)"""
    from ..demo_version import list_versions

    versions = list_versions(meeting_id)
    return {"meeting_id": meeting_id, "versions": versions, "count": len(versions)}


# =============================================================================
# SSE 实时事件流
# =============================================================================


@app.get("/api/meetings/{meeting_id}/events")
async def get_meeting_events(meeting_id: str, request: Request, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/events — SSE 实时事件流

    使用 StreamingResponse 包装 realtime_server.sse_generator (同步生成器)。
    FastAPI 自动在线程池中运行同步生成器，不阻塞事件循环。
    """
    _require_meeting_owner(meeting_id, user)
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
# Material Routes (会议材料)
# =============================================================================


@app.get("/api/meetings/{meeting_id}/materials")
async def get_meeting_materials(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /api/meetings/{id}/materials — 列出会议材料 (ADR-0050: 需 owner)"""
    _require_meeting_owner(meeting_id, user)
    items = material_storage.list_materials(meeting_id)
    return {"meeting_id": meeting_id, "materials": items, "count": len(items)}


@app.post("/api/meetings/{meeting_id}/materials")
async def post_meeting_material(meeting_id: str, request: Request, user: dict = Depends(get_current_user)):
    """POST /api/meetings/{id}/materials — 上传会议材料

    multipart/form-data:
        file: binary (必需)

    上传后:
        1. 保存为 Material 实体
        2. 根据文件类型处理：
           - 文本 (.txt/.md/.csv等): 读取内容，喂给 Hermes
           - 图片: 调 MiniMax vision API 提取描述，喂给 Hermes
           - 其他 (pdf/pptx等): 告诉 Hermes 已存入知识库
        3. 同步进知识库（文本/图片描述进 KB）
    """
    content_type = request.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=400, detail="Content-Type must be multipart/form-data")

    body = await request.body()
    fields, file_data = _parse_multipart(body, content_type)

    # ADR-0050: 仅 meeting owner 可上传
    _require_meeting_owner(meeting_id, user)

    if not file_data:
        raise HTTPException(status_code=400, detail="No file in upload")

    # 上传大小限制
    if len(file_data) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail={"error": f"文件过大 ({len(file_data)} bytes, 上限 {MAX_UPLOAD_SIZE} bytes)", "status": 413},
        )

    # 提取文件名和 Content-Type
    filename = fields.get("_filename") or "material.bin"
    file_ct = fields.get("_content_type") or "application/octet-stream"

    # 1. 保存为 Material
    meta = material_storage.store_file(
        meeting_id=meeting_id,
        file_bytes=file_data,
        filename=filename,
        content_type=file_ct,
    )

    # 2. 判断文件类型，构造喂给 Hermes 的消息
    file_type = material_storage.classify_file(filename)
    chat_message = ""
    chat_extra = {"material": meta.to_dict()}

    if file_type == "text":
        content, truncated, err = material_storage.read_text_content(meta.material_id)
        if content:
            size_note = f"{len(content)} 字" + ("(截断)" if truncated else "")
            chat_message = (
                f"用户上传了会议材料：{filename}\n"
                f"\n---文件内容 ({size_note})---\n"
                f"{content}\n"
                f"---文件内容结束---\n"
                f"\n请将以上内容纳入会议上下文。"
            )
        else:
            chat_message = f"用户上传了会议材料：{filename}，已存入知识库。"
    elif file_type == "image":
        # 图片: 先返回基础消息, vision 分析移到后台线程异步执行
        import base64 as _base64
        file_path_on_disk = material_storage.get_file_path(meta.material_id)
        disk_path = str(file_path_on_disk) if file_path_on_disk else "(unknown)"

        chat_message = (
            f"用户上传了截图/图片：{filename}\n"
            f"服务器文件路径：{disk_path}\n"
            f"已存入知识库（可搜索 KB 获取内容）。"
        )

        # 异步 vision 分析: 后台线程处理, 完成后追加到 chat
        def _run_vision_async():
            try:
                base_url = os.environ.get("OPENAI_BASE_URL", "https://api.minimax.chat/v1")
                api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY") or ""
                if not api_key:
                    try:
                        with open(os.environ.get("HERMES_ENV_PATH", os.path.expanduser("~/.hermes/.env"))) as _f:
                            for _line in _f:
                                if "=" in _line and not _line.strip().startswith("#"):
                                    _k, _v = _line.strip().split("=", 1)
                                    if _k in ("OPENAI_API_KEY", "MINIMAX_API_KEY"):
                                        api_key = _v.strip()
                                        break
                    except Exception:
                        pass

                if not api_key:
                    print(f"[materials] Vision 跳过: 无 API key")
                    return

                import requests as _requests
                _vresp = _requests.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": os.environ.get("MODEL", "minimax-m3"),
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "请详细描述这张图片的内容，提取所有可识别的文字信息。"},
                                {"type": "image_url", "image_url": {
                                    "url": f"data:{file_ct or 'image/png'};base64,{_base64.b64encode(file_data).decode()}"
                                }},
                            ],
                        }],
                        "max_tokens": 2000,
                    },
                    timeout=60,
                )
                vision_text = _vresp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                if vision_text:
                    print(f"[materials] Vision 异步分析完成: {filename} ({len(vision_text)} chars)")
                    # 追加 vision 结果到 chat
                    vision_msg = _append_chat_message(
                        meeting_id, "assistant",
                        f"---图片 AI 分析结果 ({filename})---\n{vision_text}\n---分析结果结束---",
                        source="vision-analysis",
                    )
                    safe_push_event(meeting_id, "chat-message", vision_msg)
                else:
                    print(f"[materials] Vision 异步分析返回空: {filename}")
            except Exception as e:
                print(f"[materials] Vision 异步分析失败: {str(e)[:200]}")

        threading.Thread(target=_run_vision_async, daemon=True).start()

        chat_message += "\n\n请将以上内容纳入会议上下文。图片正在异步分析中，结果稍后推送。"
    else:
        # binary (pdf/pptx/docx 等): 告诉 Hermes 去 KB 搜
        chat_message = (
            f"用户上传了会议材料：{filename}，已存入知识库。"
            f"如需了解内容，可搜索当前会议的知识库。"
        )

    # 3. 进 Hermes 主会话
    if chat_message:
        try:
            user_msg = _append_chat_message(
                meeting_id, "user", f"[上传了材料: {filename}]",
                source="material-upload", extra=chat_extra,
            )
            safe_push_event(meeting_id, "chat-message", user_msg)

            result = _run_vp_chat(meeting_id, chat_message)
            if result.get("status") != "fallback" and result.get("content"):
                assistant_msg = _append_chat_message(
                    meeting_id, "assistant", result["content"],
                    source=result["source"], status=result["status"],
                )
                safe_push_event(meeting_id, "chat-message", assistant_msg)
        except Exception as e:
            print(f"[materials] Hermes 处理失败: {e}")

    # 4. 同步进知识库（文本/图片描述进 KB，二进制原本就异步）
    try:
        from ..kb_api import handle_kb_upload
        boundary = b"----material-kb-" + str(uuid.uuid4().hex[:8]).encode()
        # 对于图片，把 vision 描述存为 KB 文档（比纯二进制有意义）
        if file_type == "image" and chat_message:
            kb_content = f"[图片 AI 分析]\n{chat_message}".encode()
            parts = [
                b"--" + boundary + b"\r\n",
                b'Content-Disposition: form-data; name="meeting_id"\r\n\r\n' + meeting_id.encode() + b"\r\n",
                b"--" + boundary + b"\r\n",
                b'Content-Disposition: form-data; name="file"; filename="vision_desc_' + filename.encode() + b'.txt"\r\n',
                b"Content-Type: text/plain\r\n\r\n",
                kb_content,
                b"\r\n--" + boundary + b"--\r\n",
            ]
        else:
            parts = [
                b"--" + boundary + b"\r\n",
                b'Content-Disposition: form-data; name="meeting_id"\r\n\r\n' + meeting_id.encode() + b"\r\n",
                b"--" + boundary + b"\r\n",
                b'Content-Disposition: form-data; name="file"; filename="' + filename.encode() + b'"\r\n',
                b"Content-Type: " + file_ct.encode() + b"\r\n\r\n",
                file_data,
                b"\r\n--" + boundary + b"--\r\n",
            ]
        kb_body = b"".join(parts)
        kb_ct = f"multipart/form-data; boundary={boundary.decode()}"
        handle_kb_upload(kb_body, kb_ct, user_id=user.get("user_id", ""))
        print(f"[materials] KB 入库完成: {filename}")
    except Exception as e:
        print(f"[materials] KB 入库失败: {e}")

    return meta.to_dict()


@app.get("/api/materials/{material_id}")
async def get_material_detail(material_id: str, user: dict = Depends(get_current_user)):
    """GET /api/materials/{id} — 材料详情 (需认证 + owner 校验)"""
    meta = material_storage.get_material(material_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Material not found: {material_id}")
    if meta.meeting_id:
        _require_meeting_owner(meta.meeting_id, user)
    return meta.to_dict()


@app.delete("/api/materials/{material_id}")
async def delete_material(material_id: str, user: dict = Depends(get_current_user)):
    """DELETE /api/materials/{id} — 删除会议材料 (需认证 + owner 校验, v0.19.0)"""
    meta = material_storage.get_material(material_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Material not found: {material_id}")
    if meta.meeting_id:
        _require_meeting_owner(meta.meeting_id, user)
    ok = material_storage.delete_material(material_id)
    return {"deleted": ok, "material_id": material_id}


@app.get("/api/materials/{material_id}/file")
async def get_material_file(material_id: str, user: dict = Depends(get_current_user)):
    """GET /api/materials/{id}/file — 下载材料原文件 (需认证 + 会议 owner 校验)"""
    meta = material_storage.get_material(material_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Material not found: {material_id}")
    if meta.meeting_id:
        _require_meeting_owner(meta.meeting_id, user)
    fp = material_storage.get_file_path(material_id)
    if fp is None or not fp.exists():
        raise HTTPException(status_code=404, detail=f"Material file not found: {material_id}")
    return FileResponse(
        str(fp),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{meta.filename}"'},
    )


# =============================================================================
# POST Routes
# =============================================================================


@app.post("/api/meetings/stream_start")
async def post_stream_start(request: Request, user: dict = Depends(get_current_user)):
    """POST /api/meetings/stream_start — 创建长连接会议

    参数通过 query string 或 JSON body 传递:
        meeting_id: str (可选, ADR-0022 — 复用已有会议)
        audio_source: str (可选, ADR-0021 — microphone|loopback|both)
        project_name: str (可选, 会议名称，默认 "长连接会议 {id}")
    """
    query_params = dict(request.query_params)
    from ..state import AudioSourceKind
    from ..storage import MeetingStorage

    project_name = query_params.get("project_name", "").strip()
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not project_name:
        project_name = str(body.get("project_name", "")).strip()

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
            _require_meeting_owner(meeting_id, user, storage=storage)
            state = storage.load(meeting_id)
            state.audio_source = audio_source
            state.last_updated = datetime.now().isoformat()
            storage.save(state)
        else:
            state = MeetingState(
                meeting_id=meeting_id,
                platform=Platform.LOCAL,
                audio_source=audio_source,
                owner_id=user["user_id"],
                project_name=project_name or f"长连接会议 {meeting_id}",
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create state failed: {e}")

    return {
        "meeting_id": meeting_id,
        "audio_source": audio_source.value,
        "reused": reused,
        "message": "Stream started, connect via WebSocket /api/meetings/{id}/realtime_asr",
    }


@app.post("/api/meetings/{meeting_id}/chat")
async def post_chat(meeting_id: str, request: Request, user: dict = Depends(get_current_user)):
    """POST /api/meetings/{id}/chat — VP 自由输入接 chat agent

    支持 JSON 路径和 multipart/form-data 路径 (ADR-0023).
    """
    _require_meeting_owner(meeting_id, user)
    from ..kb_api import handle_chat_upload

    content_type = request.headers.get("Content-Type", "")

    # Multipart 分支 (ADR-0023 Phase 6)
    if content_type.startswith("multipart/form-data"):
        body = await request.body()
        upload_result = handle_chat_upload(body, content_type, meeting_id, user_id=user.get("user_id", ""))
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
        safe_push_event(meeting_id, "chat-message", user_msg)

        result = _run_vp_chat(meeting_id, text or "(用户只上传了文件, 没问文本)")
        assistant_msg = _append_chat_message(
            meeting_id,
            "assistant",
            result["content"],
            source=result["source"],
            status=result["status"],
            extra={"error": result.get("error"), "attachment_count": len(files_meta)},
        )
        safe_push_event(meeting_id, "chat-message", assistant_msg)

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
    if len(message) > MAX_CHAT_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail={"error": f"消息过长 ({len(message)} 字, 上限 {MAX_CHAT_MESSAGE_LENGTH})", "status": 400},
        )

    client_context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    user_msg = _append_chat_message(
        meeting_id,
        "user",
        message,
        source="client",
        extra={"context": client_context},
    )
    safe_push_event(meeting_id, "chat-message", user_msg)

    result = _run_vp_chat(meeting_id, message, client_context)
    assistant_msg = _append_chat_message(
        meeting_id,
        "assistant",
        result["content"],
        source=result["source"],
        status=result["status"],
        extra={"error": result.get("error")},
    )
    safe_push_event(meeting_id, "chat-message", assistant_msg)

    return {
        "meeting_id": meeting_id,
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        "status": result["status"],
        "source": result["source"],
        "error": result.get("error"),
    }


@app.post("/api/meetings/{meeting_id}/close")
def post_meeting_close(meeting_id: str, user: dict = Depends(get_current_user)):
    """POST /api/meetings/{id}/close — 结束会议并触发文档生成

    委托 ui_server._close_meeting 统一处理:
    1. push_event("meeting-complete")
    2. close_meeting (SSE 订阅者退出)
    3. clear proactive throttle
    4. 经验蒸馏
    5. task_manager.submit → batch_docs + demo 生成
    """
    _require_meeting_owner(meeting_id, user)
    from ..ui_server import _close_meeting
    try:
        result = _close_meeting(meeting_id)
        return {
            "meeting_id": meeting_id,
            "status": "closed",
            "details": result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
    _require_meeting_owner(meeting_id, user)
    import shutil

    deleted = {"state": False, "chat": False, "materials": 0, "docs": False, "stream_meta": False}

    try:
        from ..realtime_server import close_meeting
        close_meeting(meeting_id)
    except Exception:
        pass

    try:
        from ..task_manager import get_task_manager
        get_task_manager().cancel_meeting(meeting_id)
    except Exception:
        pass

    try:
        from ..storage import MeetingStorage
        storage = MeetingStorage(DATA_DIR)
        deleted["state"] = storage.delete(meeting_id)
    except Exception:
        pass

    try:
        chat_path = DATA_DIR / f"{meeting_id}.chat.json"
        if chat_path.exists():
            chat_path.unlink()
            deleted["chat"] = True
    except Exception:
        pass

    try:
        stream_meta_path = DATA_DIR / f"{meeting_id}.stream.json"
        if stream_meta_path.exists():
            stream_meta_path.unlink()
            deleted["stream_meta"] = True
    except Exception:
        pass

    try:
        for m in material_storage.list_materials(meeting_id):
            material_storage.delete_material(m.get("id", ""))
            deleted["materials"] += 1
    except Exception:
        pass

    try:
        docs_dir = DOCS_DIR / meeting_id
        if docs_dir.exists() and docs_dir.is_dir():
            shutil.rmtree(str(docs_dir))
            deleted["docs"] = True
    except Exception:
        pass

    return {"meeting_id": meeting_id, "deleted": deleted}


@app.patch("/api/meetings/{meeting_id}")
async def patch_meeting(meeting_id: str, request: Request, user: dict = Depends(get_current_user)):
    state = _require_meeting_owner(meeting_id, user)
    body = await request.json()
    project_name = str(body.get("project_name", "")).strip()
    if not project_name:
        raise HTTPException(status_code=400, detail={"error": "project_name is required", "status": 400})
    from ..storage import MeetingStorage
    storage = MeetingStorage(DATA_DIR)
    state.project_name = project_name
    state.last_updated = datetime.now().isoformat()
    storage.save(state)
    return {"meeting_id": meeting_id, "project_name": project_name}


@app.post("/api/meetings/{meeting_id}/collab/ask")
def post_collab_ask(
    meeting_id: str,
    section: str = Query(..., description="文档章节"),
    question: str = Query(..., description="问题内容"),
    asker: str = Query("agent", description="提问方"),
    user: dict = Depends(get_current_user),
):
    """POST /api/meetings/{id}/collab/ask — 协作提问 (ADR-0028)"""
    _require_meeting_owner(meeting_id, user)
    if not section or not question:
        raise HTTPException(status_code=400, detail="section 和 question 必填")

    from ..collab import ask_question

    result = ask_question(meeting_id, section, question, asker=asker)
    if not result.get("ok"):
        status_code = 400
        if result.get("status") == "duplicate":
            status_code = 200
        raise HTTPException(status_code=status_code, detail=result)

    safe_push_event(meeting_id, "collab-update", {
        "action": "ask",
        "qid": result.get("qid"),
        "section": section,
        "status": result["status"],
        "question": question,
        "asker": asker,
    })

    return result


@app.post("/api/meetings/{meeting_id}/collab/answer")
def post_collab_answer(
    meeting_id: str,
    qid: str = Query(..., description="问题 ID"),
    answer: str = Query(..., description="回答内容"),
    answerer: str = Query("VP", description="回答方"),
    user: dict = Depends(get_current_user),
):
    """POST /api/meetings/{id}/collab/answer — 协作回答 (ADR-0028)"""
    _require_meeting_owner(meeting_id, user)
    if not qid or not answer:
        raise HTTPException(status_code=400, detail="qid 和 answer 必填")

    from ..collab import answer_question

    result = answer_question(meeting_id, qid, answer, answerer=answerer)
    if not result.get("ok"):
        status_code = 404 if result.get("status") == "not_found" else 400
        raise HTTPException(status_code=status_code, detail=result)

    safe_push_event(meeting_id, "collab-update", {
        "action": "answer",
        "qid": qid,
        "answer": answer,
        "answerer": answerer,
        "status": "answered",
    })

    return result


@app.post("/api/kb/search")
async def post_kb_search(request: Request, user: dict = Depends(get_current_user)):
    """POST /api/kb/search — KB 检索 (POST with JSON body, 按 user_id 隔离)"""
    body = await request.body()
    query_params = dict(request.query_params)
    from ..kb_api import handle_kb_search

    result = handle_kb_search(query_params, body, user_id=user["user_id"])
    return result


@app.post("/api/kb/upload")
async def post_kb_upload(request: Request, user: dict = Depends(get_current_user)):
    """POST /api/kb/upload — 上传文件进 KB (按 user_id 存储)"""
    content_type = request.headers.get("Content-Type", "")
    body = await request.body()
    from ..kb_api import handle_kb_upload

    result = handle_kb_upload(body, content_type, user_id=user["user_id"])
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
# =============================================================================

# ── 百炼 Fun-ASR 实时转写 WebSocket ──

@app.websocket("/api/meetings/{meeting_id}/realtime_asr")
async def ws_realtime_asr(websocket: WebSocket, meeting_id: str):
    """WebSocket — 百炼 Fun-ASR-Realtime 实时转写 relay."""
    import logging as _logging
    _log = _logging.getLogger(__name__)
    await websocket.accept()

    token = websocket.query_params.get("token", "")
    from .auth import verify_token
    user = verify_token(token)
    if user is None:
        await websocket.send_json({"type": "error", "error": "token 无效或缺失"})
        await websocket.close()
        return

    _log.info("[ws_realtime_asr] client connected, meeting=%s user=%s", meeting_id, user.get("user_id"))

    from .bailian_asr import start_session, send_audio, stop_session

    session = None
    format_str = "pcm"
    sample_rate = 16000
    _doc_running = [False]
    _stop_received = False  # Issue #31: 区分主动停止 vs 断线

    try:
        # Phase 1: handshake — 收 JSON "start" 消息
        start_raw = await websocket.receive_text()
        start_msg = json.loads(start_raw)
        if start_msg.get("type") != "start":
            await websocket.send_json({"type": "error", "error": "expected start message"})
            return

        format_str = start_msg.get("format", "pcm")
        sample_rate = int(start_msg.get("sample_rate", 16000))

        async def _send_json(msg: dict):
            try:
                await websocket.send_json(msg)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("non-critical error sending json via websocket", exc_info=True)

        # Phase 2: 启动百炼识别
        import asyncio as _asyncio
        session = start_session(
            loop=_asyncio.get_running_loop(),
            meeting_id=meeting_id,
            send_json=_send_json,
            sample_rate=sample_rate,
            fmt=format_str,
            data_dir=DATA_DIR,
        )

        # 启动自驱动文档 generator: asyncio 定时轮询 ASR 文本, 有增量就提交

        _doc_last_hash = [""]
        _doc_running[0] = True

        def _doc_runner(gen_id: int, mid: str) -> dict:
            """一次性文档 runner — batch_docs + demo 并行触发 (各自独立读 state)."""
            from concurrent.futures import ThreadPoolExecutor
            from ..sub_session_controller import _dispatch_kind, BATCH_DOCS_KIND, DEMO_KIND

            kinds = [BATCH_DOCS_KIND, DEMO_KIND]
            results = {}

            def _run(kind):
                try:
                    r = _dispatch_kind(mid, kind, dry_run=False)
                    return kind, {"triggered": r.get("triggered"), "error": r.get("error")}
                except Exception as e:
                    return kind, {"triggered": False, "error": str(e)}

            with ThreadPoolExecutor(max_workers=2) as ex:
                futures = [ex.submit(_run, k) for k in kinds]
                for f in futures:
                    try:
                        kind, r = f.result(timeout=300)
                        results[kind] = r
                    except Exception:
                        pass

            return results

        # Issue #31: 自驱动文档调度 — hash-based 检测有意义变更, debounce 6s
        async def _kick_docs():
            import hashlib
            try:
                from ..task_manager import get_task_manager
                from ..storage import MeetingStorage
                st = MeetingStorage(DATA_DIR)
                debounce = 6
                # 第 1 轮: 等 debounce 后, 若有文本则触发
                await _asyncio.sleep(debounce)
                while _doc_running[0]:
                    if st.exists(meeting_id):
                        state = st.load(meeting_id)
                        cur = state.cleaned_text if state.cleaned_text else ""
                        cur_hash = hashlib.md5(cur.encode()).hexdigest()
                        if cur_hash != _doc_last_hash[0] and len(cur) > 10:
                            _doc_last_hash[0] = cur_hash
                            _log.info("[_kick_docs] meaningful change detected, len=%d hast=%s", len(cur), cur_hash[:8])
                            get_task_manager().submit(meeting_id, _doc_runner)
                    await _asyncio.sleep(debounce)
            except Exception:
                import logging
                logging.getLogger(__name__).warning("non-critical error in doc kick loop", exc_info=True)

        _asyncio.create_task(_kick_docs())

        # Phase 3: relay — 音频帧 → 百炼, 同时监听 stop
        from starlette.websockets import WebSocketState

        while session.running:
            try:
                data = await _asyncio.wait_for(websocket.receive(), timeout=0.5)
            except _asyncio.TimeoutError:
                continue

            if "text" in data:
                # JSON 控制消息
                msg = json.loads(data["text"])
                if msg.get("type") == "stop":
                    _stop_received = True
                    _log.info("[ws_realtime_asr] client sent stop, meeting=%s", meeting_id)
                    break
                elif msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            elif "bytes" in data:
                # 二进制音频帧
                send_audio(session, data["bytes"])

    except WebSocketDisconnect:
        _log.info("[ws_realtime_asr] client disconnected, meeting=%s", meeting_id)
    except Exception as e:
        _log.error("[ws_realtime_asr] error meeting=%s: %s", meeting_id, e)
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except Exception:
            import logging
            logging.getLogger(__name__).warning("non-critical error sending error via websocket", exc_info=True)
    finally:
        _doc_running[0] = False
        if session:
            stop_session(session)
            _log.info("[ws_realtime_asr] session stopped, meeting=%s sentences=%d noise=%d",
                       meeting_id, session.sentence_count, session.noise_count)
        try:
            await websocket.close()
        except Exception:
            import logging
            logging.getLogger(__name__).warning("non-critical error closing websocket", exc_info=True)

        # Issue #31: 只有客户端显式发送 stop 才 finalize 会议
        # 网络断连/WebSocketDisconnect 不触发 close_meeting, 保留会议数据待恢复
        if _stop_received:
            try:
                from ..ui_server import _close_meeting
                _log.info("[ws_realtime_asr] final close_meeting (stop received), meeting=%s", meeting_id)
                _close_meeting(meeting_id)
            except Exception as _ce:
                _log.error("[ws_realtime_asr] close_meeting failed: %s", _ce)
        else:
            _log.info("[ws_realtime_asr] connection lost, meeting %s kept open for potential reconnect", meeting_id)
            from ..realtime_server import push_event
            try:
                push_event(meeting_id, "recording-disconnected", {
                    "meeting_id": meeting_id,
                    "sentences": session.sentence_count if session else 0,
                })
            except Exception:
                pass


# =============================================================================
# 前端契约路由别名 (v0.9.0 BFF bridge) — 适配前端 vpbuddy-frontend API.md
# 前端路径: /meetings/... → 内部映射到 /api/meetings/...
# =============================================================================

# GET /meetings — 会议工作台列表
@app.get("/meetings")
async def fe_list_meetings(user: dict = Depends(get_current_user)):
    """GET /meetings → 按 owner 过滤后返回"""
    from ..server.api_utils import list_meetings
    meetings = list_meetings()
    # 按 owner 过滤 (align with GET /api/meetings)
    own = [m for m in meetings if m.get("owner_id") == user["user_id"]]
    return {"meetings": own, "count": len(own)}

# GET /meetings/{meeting_id} — 会议详情聚合
@app.get("/api/meetings/{meeting_id}")
async def fe_get_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /meetings/:id → 聚合 state + docs + collab + experiences"""
    _require_meeting_owner(meeting_id, user)
    from ..storage import MeetingStorage, StorageError
    result: dict[str, Any] = {"id": meeting_id}

    try:
            storage = MeetingStorage(DATA_DIR)
            if storage.exists(meeting_id):
                state = storage.load(meeting_id)
                result["state"] = state.model_dump(mode="json")
                result["cleaned_text_length"] = len(state.cleaned_text)
            else:
                result["state"] = None
                result["cleaned_text_length"] = 0
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

    # Materials
    try:
        result["materials"] = material_storage.list_materials(meeting_id)
    except Exception:
        result["materials"] = []

    return result

# GET /meetings/{meeting_id}/transcript-segments — 转写片段
@app.get("/meetings/{meeting_id}/transcript-segments")
async def fe_transcript_segments(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /meetings/:id/transcript-segments → 从 stream meta 提取"""
    _require_meeting_owner(meeting_id, user)
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
async def fe_recording_start(meeting_id: str, user: dict = Depends(get_current_user)):
    """POST /meetings/:id/recording/start → POST /api/meetings/stream_start"""
    _require_meeting_owner(meeting_id, user)
    from ..ui_server import _handle_stream_start
    # 复用 stream_start handler
    result = _handle_stream_start(meeting_id=meeting_id)
    return {"status": "recording", "started_at": datetime.now().isoformat(), "detail": result}

# POST /meetings/{meeting_id}/recording/stop — 停止录音
@app.post("/meetings/{meeting_id}/recording/stop")
async def fe_recording_stop(meeting_id: str, user: dict = Depends(get_current_user)):
    """POST /meetings/:id/recording/stop → POST /api/meetings/:id/stream_stop"""
    _require_meeting_owner(meeting_id, user)
    from ..ui_server import _handle_stream_stop
    result = _handle_stream_stop(meeting_id)
    return {"status": "stopped", "ended_at": datetime.now().isoformat(), "detail": result}

# GET /meetings/{meeting_id}/deliverables — 交付物列表
@app.get("/meetings/{meeting_id}/deliverables")
async def fe_list_deliverables(meeting_id: str, user: dict = Depends(get_current_user)):
    """GET /meetings/:id/deliverables → GET /api/meetings/:id/docs (wrap)"""
    _require_meeting_owner(meeting_id, user)
    from ..server.api_utils import _doc_payload
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
async def fe_get_deliverable(deliverable_id: str, user: dict = Depends(get_current_user)):
    """GET /deliverables/:id → parse {meetingId}:{kind} → file content"""
    # 格式: del-{meeting_id}-{kind}
    parts = deliverable_id.split("-", 2)
    if len(parts) < 3:
        raise HTTPException(status_code=400, detail=f"Invalid deliverable_id: {deliverable_id}, expected del-{meeting_id}-{kind}")
    meeting_id, kind = parts[1], parts[2]
    _require_meeting_owner(meeting_id, user)
    doc_path = DOCS_DIR / meeting_id / f"{kind}.md"
    if not doc_path.exists():
        raise HTTPException(status_code=404, detail=f"Deliverable {kind} not found for meeting {meeting_id}")
    content = doc_path.read_text(encoding="utf-8")
    # 版本号: demo 走 list_versions 计数, 其他文档走 get_version_file 或默认 "1"
    if kind == "demo":
        from ..demo_version import list_versions
        versions = list_versions(meeting_id)
        version = str(len(versions)) if versions else "1"
    else:
        from ..demo_version import get_version_file
        version = get_version_file(meeting_id, kind)
    return {
        "id": deliverable_id,
        "meetingId": meeting_id,
        "type": kind,
        "name": DOC_LABELS.get(kind, kind),
        "version": version,
        "content": content,
        "updatedAt": datetime.fromtimestamp(doc_path.stat().st_mtime).isoformat(),
    }

# GET /meetings/{meeting_id}/events — 会议事件 (SSE)
# 已通过 GET /api/meetings/{meeting_id}/events 提供 SSE

# GET /client/device-status — 设备状态 (已通过 /api/client/device-status 提供)

# POST /meetings/{meeting_id}/archive — 结束会议并归档
@app.post("/meetings/{meeting_id}/archive")
async def fe_archive_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
    """POST /meetings/:id/archive → POST /api/meetings/:id/close + 归档信息"""
    _require_meeting_owner(meeting_id, user)
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
        import logging
        logging.getLogger(__name__).warning("non-critical error during KB Chroma warmup", exc_info=True)

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
