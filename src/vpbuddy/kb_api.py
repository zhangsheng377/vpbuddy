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
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

from .rag_backend import get_rag, DATA_DIR

logger = logging.getLogger(__name__)

# ── 配置 ──
UPLOADS_DIR = DATA_DIR / "uploads"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
CHROMA_DIR = DATA_DIR / "chroma"


def _parse_multipart(body: bytes, content_type: str) -> dict[str, Any]:
    """手写超轻 multipart/form-data 解析(约 50 行, 不引第三方)."""
    import re

    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        raise ValueError("no boundary in Content-Type")
    boundary = (match.group(1) or match.group(2)).encode()
    parts: dict[str, Any] = {}

    # 标准 multipart: body 以 --{boundary} 开头, 用 \r\n--{boundary} 分隔各分节
    # 第一个分隔前的内容 (preamble) 忽略
    sep = b"\r\n--" + boundary
    for section in body.split(sep):
        if not section:
            continue
        # 跳过末尾的 -- (分节结束符) 和 preamble (--boundary 开头的部分)
        trimmed = section.strip(b"\r\n")
        if trimmed == b"--":
            continue
        if section.startswith(b"--"):
            # 第一个分节的 section 可能是 --boundary\r\nContent-Disposition: ...
            # 去掉开头多余的 --
            section = section.lstrip(b"-")

        header_end = section.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        raw_headers = section[:header_end].decode(errors="replace")
        data = section[header_end + 4:]
        # trim trailing \r\n
        if data.endswith(b"\r\n"):
            data = data[:-2]

        name_match = re.search(r'name="([^"]*)"', raw_headers)
        if not name_match:
            continue
        name = name_match.group(1)

        if "filename=" in raw_headers:
            fname_match = re.search(r'filename="([^"]*)"', raw_headers)
            parts["file"] = data
            parts["filename"] = fname_match.group(1) if fname_match else "unknown"
        else:
            parts[name] = data.decode(errors="replace")

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


def _validate_file(filename: str, file_bytes: bytes) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"只支持 .txt / .md / .pdf, 收到 {ext}")
    if len(file_bytes) > MAX_FILE_SIZE:
        raise ValueError(f"文件超过 50MB 限制")


def handle_kb_upload(body: bytes, content_type: str) -> dict:
    """POST /api/kb/upload — 上传文件进 KB.

    multipart/form-data:
        meeting_id: str (必填)
        file: binary (必填, .txt/.md/.pdf)
    """
    try:
        parts = _parse_multipart(body, content_type)
    except ValueError as e:
        return {"error": f"解析请求失败: {e}", "status": 400}

    meeting_id = parts.get("meeting_id", "").strip()
    file_bytes = parts.get("file")
    filename = parts.get("filename", "unknown")

    if not meeting_id:
        return {"error": "meeting_id 必填", "status": 400}
    if not file_bytes:
        return {"error": "file 必填", "status": 400}

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
    now = datetime.now(timezone.utc).isoformat()
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
