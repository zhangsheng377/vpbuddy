"""KB API 端点 — 文件上传 / 检索 / 列表 / 删除 (ADR-0020)

依赖 rag_backend.py (ChromaRAG), 业务代码解耦。
所有查询默认带 meeting_id 过滤。

Endpoint 签名(被 ui_server.py 调用):
    handle_kb_upload(params, body, content_type) -> dict
    handle_kb_search(params, body) -> dict
    handle_kb_list(params) -> dict
    handle_kb_delete(path) -> dict
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .rag_backend import DATA_DIR, get_rag

logger = logging.getLogger(__name__)

# ── 配置 ──
UPLOADS_DIR = DATA_DIR / "uploads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
# 2026-07-01 ADR-0023: chat 上传额外允许图片
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_CHAT_EXTENSIONS = ALLOWED_EXTENSIONS | ALLOWED_IMAGE_EXTENSIONS
CHROMA_DIR = DATA_DIR / "chroma"


def _parse_multipart(body: bytes, content_type: str) -> dict[str, Any]:
    """用 python-multipart 解析 multipart/form-data (P1#3 2026-07-04).

    返回结构:
        {
            "text_field": str          # 普通字段值 (最后一个同名覆盖)
            "files": [
                {"filename": str, "data": bytes, "content_type": str}, ...
            ],
        }
    """
    from io import BytesIO
    from multipart import parse_form

    parts: dict[str, Any] = {"files": []}

    def on_field(f):
        parts[f.field_name.decode()] = f.value.decode("utf-8", "replace")

    def on_file(f):
        f.file_object.seek(0)
        parts["files"].append({
            "name": f.field_name.decode() if isinstance(f.field_name, bytes) else str(f.field_name or ""),
            "filename": f.file_name.decode() if isinstance(f.file_name, bytes) else str(f.file_name or "unknown"),
            "data": f.file_object.read(),
            "content_type": f.content_type or "application/octet-stream",
        })

    parse_form({"Content-Type": content_type.encode()}, BytesIO(body),
               on_field=on_field, on_file=on_file)
    return parts


def _extract_text(file_bytes: bytes, filename: str) -> str:
    """解析上传文件为纯文本."""
    ext = Path(filename).suffix.lower()

    if ext in (".txt", ".md"):
        return file_bytes.decode("utf-8", errors="replace")

    if ext == ".pdf":
        from io import BytesIO

        import pypdf
        reader = pypdf.PdfReader(BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text.strip())
        return "\n\n".join(p.strip() for p in pages if p.strip())

    raise ValueError(f"不支持的文件类型: {ext}")


def _image_to_b64_data_uri(file_bytes: bytes, content_type: str) -> str:
    """图片转 data URI (base64) — 给 chat 多模态 LLM 喂图用.

    2026-07-01 ADR-0023 Phase 6: chat 上传图片不写入 KB, 走 ollama /api/chat
    images 字段. 太大 (>5MB) 拒绝, 避免 LLM 上下文爆.
    """
    import base64
    if len(file_bytes) > 5 * 1024 * 1024:
        raise ValueError(f"图片超过 5MB (当前 {len(file_bytes) // 1024 // 1024}MB)")
    b64 = base64.b64encode(file_bytes).decode("ascii")
    # 兜底 content_type (前端可能没传)
    ct = content_type if content_type.startswith("image/") else "image/png"
    return f"data:{ct};base64,{b64}"


def _validate_file(filename: str, file_bytes: bytes, *, allow_images: bool = False) -> None:
    """校验上传文件: 扩展名白名单 + 大小上限."""
    ext = Path(filename).suffix.lower()
    allowed = ALLOWED_CHAT_EXTENSIONS if allow_images else ALLOWED_EXTENSIONS
    if ext not in allowed:
        kinds = "txt/md/pdf" + ("/png/jpg/jpeg/gif/webp" if allow_images else "")
        raise ValueError(f"只支持 {kinds}, 收到 {ext}")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise ValueError("文件超过 50MB 限制")


def _is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_IMAGE_EXTENSIONS


def handle_kb_upload(body: bytes, content_type: str) -> dict:
    """POST /api/kb/upload — 上传文件进 KB.

    multipart/form-data:
        meeting_id: str (必填)
        file: binary (必填, .txt/.md/.pdf) — 单文件 (向后兼容老调用方)

    2026-07-01 ADR-0023: _parse_multipart 升级支持多文件, 这里为向后兼容仍只取
    files[0]. 多文件上传请走 handle_chat_upload (POST /api/meetings/{id}/chat).
    """
    try:
        parts = _parse_multipart(body, content_type)
    except ValueError as e:
        return {"error": f"解析请求失败: {e}", "status": 400}

    meeting_id = parts.get("meeting_id", "").strip()
    files = parts.get("files") or []
    if not files:
        return {"error": "file 必填", "status": 400}
    first = files[0]
    file_bytes = first["data"]
    filename = first["filename"]

    if not meeting_id:
        return {"error": "meeting_id 必填", "status": 400}

    try:
        _validate_file(filename, file_bytes)
    except ValueError as e:
        return {"error": str(e), "status": 400}

    # 保存原始文件
    file_uuid = uuid.uuid4().hex[:12]
    upload_dir = UPLOADS_DIR / meeting_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    raw_path = upload_dir / f"{file_uuid}_{filename}"
    raw_path.write_bytes(file_bytes)

    # 抽取文本
    try:
        text = _extract_text(file_bytes, filename)
    except Exception as e:
        return {"error": f"文件解析失败: {e}", "status": 400}

    if not text.strip():
        return {"error": "文件内容为空", "status": 400}

    # 入库 Chroma
    rag = get_rag()
    doc_id = f"{meeting_id}:{file_uuid}"
    now = datetime.now(UTC).isoformat()
    rag.add(
        ids=[doc_id],
        documents=[text],
        metadatas=[{
            "meeting_id": meeting_id,
            "source": f"upload:{filename}",
            "uploaded_at": now,
            "chunk_index": 0,
            "file_size": len(file_bytes),
            "file_ext": Path(filename).suffix.lower().lstrip("."),
        }],
    )

    logger.info("KB uploaded: meeting=%s file=%s doc_id=%s size=%d", meeting_id, filename, doc_id, len(file_bytes))

    return {
        "status": 200,
        "doc_id": doc_id,
        "meeting_id": meeting_id,
        "filename": filename,
        "chunks": 1,
        "char_count": len(text),
    }


def handle_chat_upload(body: bytes, content_type: str, meeting_id: str) -> dict:
    """POST /api/meetings/{id}/chat (multipart) — chat 多文件上传 (ADR-0023).

    行为:
      - 文件 (.txt/.md/.pdf) → 抽文本 + 入 KB (Chroma)
      - 图片 (.png/.jpg/.gif/.webp) → 转 base64 data URI, 不入 KB, 走 LLM vision
      - 返回每文件处理结果 (status/filename/error), chat agent 拿到 images 列表后
        在 ollama /api/chat 调用里喂给 LLM.

    multipart/form-data:
        text: str (可选, 用户问的文本)
        files: list[file] (可选, 多个)
    """
    try:
        parts = _parse_multipart(body, content_type)
    except ValueError as e:
        return {"error": f"解析请求失败: {e}", "status": 400}

    text = (parts.get("text") or "").strip()
    files = parts.get("files") or []
    if not text and not files:
        return {"error": "text 或 files 至少一个非空", "status": 400}

    # 每个文件单独处理
    results: list[dict[str, Any]] = []
    kb_doc_ids: list[str] = []
    image_data_uris: list[str] = []

    for f in files:
        fname = f["filename"]
        data = f["data"]
        ct = f.get("content_type", "application/octet-stream")

        try:
            _validate_file(fname, data, allow_images=True)
        except ValueError as e:
            results.append({"filename": fname, "status": "rejected", "error": str(e)})
            continue

        if _is_image(fname):
            # 图片 → base64, 不入库
            try:
                uri = _image_to_b64_data_uri(data, ct)
                image_data_uris.append(uri)
                results.append({"filename": fname, "status": "image", "data_uri_length": len(uri)})
            except ValueError as e:
                results.append({"filename": fname, "status": "rejected", "error": str(e)})
        else:
            # 文本类 → 入 KB
            try:
                content = _extract_text(data, fname)
                if not content.strip():
                    results.append({"filename": fname, "status": "empty"})
                    continue
                file_uuid = uuid.uuid4().hex[:12]
                doc_id = f"{meeting_id}:chat-upload:{file_uuid}"
                rag = get_rag()
                now = datetime.now(UTC).isoformat()
                rag.add(
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[{
                        "meeting_id": meeting_id,
                        "source": f"chat-upload:{fname}",
                        "uploaded_at": now,
                        "chunk_index": 0,
                        "file_size": len(data),
                        "file_ext": Path(fname).suffix.lower().lstrip("."),
                    }],
                )
                kb_doc_ids.append(doc_id)
                results.append({"filename": fname, "status": "kb-stored", "doc_id": doc_id, "chars": len(content)})
                logger.info("chat upload: meeting=%s file=%s doc_id=%s chars=%d", meeting_id, fname, doc_id, len(content))
            except Exception as e:
                logger.exception("chat upload failed: %s", fname)
                results.append({"filename": fname, "status": "error", "error": str(e)})

    return {
        "status": 200,
        "meeting_id": meeting_id,
        "text": text,
        "files": results,
        "kb_doc_ids": kb_doc_ids,
        "image_count": len(image_data_uris),
        # 图片不直接返 data URI (太大), 由调用方按需从 chat 上下文拿
    }


def handle_kb_search(params: dict[str, list[str]], body_bytes: bytes) -> dict:
    """POST /api/kb/search — 检索 KB (默认带 meeting_id 过滤)."""
    if body_bytes.strip():
        try:
            req = json.loads(body_bytes)
        except json.JSONDecodeError:
            return {"error": "请求体不是有效 JSON", "status": 400}
    else:
        req = {}

    query = (req.get("query") or params.get("q", [""])[0]).strip()
    if not query:
        return {"results": [], "count": 0, "scope": "current"}

    top_k = int(req.get("top_k", 5))
    meeting_id = req.get("meeting_id") or params.get("meeting_id", [None])[0]
    scope = req.get("scope", "current")

    where: dict[str, str] | None = None
    if scope == "current" and meeting_id:
        where = {"meeting_id": meeting_id}

    rag = get_rag()
    results = rag.query(query, top_k=top_k, where=where)

    return {
        "results": results,
        "count": len(results),
        "scope": scope if meeting_id else "none",
        "meeting_id": meeting_id or None,
    }


def handle_kb_list(params: dict[str, list[str]]) -> dict:
    """GET /api/kb/list?meeting_id= — 列出 KB 文档 (按会议/全部)."""
    # Chroma 不提供按 metadata 列表, 用 count
    rag = get_rag()
    meeting_id = params.get("meeting_id", [None])[0]

    total = rag.count()
    return {
        "total": total,
        "meeting_id": meeting_id or None,
        "note": "全量列表需逐文档遍历(Chroma 不暴露 metadata 枚举)",
    }


def handle_kb_delete(path: str) -> dict:
    """DELETE /api/kb/{doc_id} — 删除 KB 文档."""
    # /api/kb/<doc_id>
    doc_id = path.split("/")[-1]
    if not doc_id:
        return {"error": "doc_id 必填", "status": 400}

    rag = get_rag()
    rag.delete([doc_id])
    logger.info("KB deleted: doc_id=%s", doc_id)
    return {"status": 200, "doc_id": doc_id}
