"""Material storage — 会议材料实体管理 (v0.15.0)

材料 = 用户上传到会议的任何文件（截图/PPT/PDF/文档/图片等）。
上传后两条路：
1. 进 Hermes 主会话（chat 路径，LLM 当场处理）
2. 异步进知识库（后续检索用）

存储结构:
  {DATA_DIR}/materials/{meeting_id}/{material_id}/
    - original_filename.ext      # 原始文件
    - meta.json                  # 元数据
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 目录基址由外部 set_data_dir() 设置，默认 None
_MATERIALS_BASE: Path | None = None
_LOCK = threading.Lock()

# 允许的材料类型（比 KB 宽，覆盖一般办公文件）
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx",
    ".csv", ".json", ".xml", ".yaml", ".yml",
    ".mp3", ".wav", ".m4a", ".ogg",
    ".mp4", ".mov", ".avi",
}

# 可直接读取文本内容的文件类型（喂给 Hermes）
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml"}
# 图片文件类型（调 vision API 分析）
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 文本文件最大喂给 Hermes 的字数
MAX_TEXT_CHARS = 50000


def init(data_dir: Path) -> None:
    """初始化材料存储基目录。在服务启动时调用一次。"""
    global _MATERIALS_BASE
    _MATERIALS_BASE = data_dir / "materials"
    _MATERIALS_BASE.mkdir(parents=True, exist_ok=True)
    print(f"[material_storage] 初始化完成: {_MATERIALS_BASE}")


def _base() -> Path:
    if _MATERIALS_BASE is None:
        raise RuntimeError("material_storage.init() 尚未调用")
    return _MATERIALS_BASE


# ── Material 元数据 ──


class MaterialMeta:
    """单条材料的元数据。"""
    def __init__(
        self,
        material_id: str,
        meeting_id: str,
        filename: str,
        content_type: str,
        size: int,
        created_at: str,
        status: str = "stored",
    ):
        self.material_id = material_id
        self.meeting_id = meeting_id
        self.filename = filename
        self.content_type = content_type
        self.size = size
        self.created_at = created_at
        self.status = status

    def to_dict(self) -> dict:
        return {
            "id": self.material_id,
            "meeting_id": self.meeting_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "created_at": self.created_at,
            "status": self.status,
        }

    @staticmethod
    def from_dict(d: dict) -> "MaterialMeta":
        return MaterialMeta(
            material_id=d["id"],
            meeting_id=d["meeting_id"],
            filename=d["filename"],
            content_type=d["content_type"],
            size=d["size"],
            created_at=d["created_at"],
            status=d.get("status", "stored"),
        )


# ── Meeting 级别的 index 文件 ──


def _meeting_index_path(meeting_id: str) -> Path:
    return _base() / meeting_id / "index.json"


def _load_index(meeting_id: str) -> list[dict]:
    p = _meeting_index_path(meeting_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(meeting_id: str, entries: list[dict]) -> None:
    p = _meeting_index_path(meeting_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 原子写
    fd, tmp = tempfile.mkstemp(suffix=".json.tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(fd)
        os.replace(tmp, p)
    except:
        try:
            os.unlink(tmp)
        except:
            pass
        raise


# ── 公开 API ──


def store_file(
    meeting_id: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> MaterialMeta:
    """保存上传文件到材料存储，返回 MaterialMeta。"""
    material_id = f"mat_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    mat_dir = _base() / meeting_id / material_id
    mat_dir.mkdir(parents=True, exist_ok=True)

    # 写原始文件
    file_path = mat_dir / filename
    file_path.write_bytes(file_bytes)

    meta = MaterialMeta(
        material_id=material_id,
        meeting_id=meeting_id,
        filename=filename,
        content_type=content_type,
        size=len(file_bytes),
        created_at=now,
        status="stored",
    )

    # 写 meta.json
    meta_path = mat_dir / "meta.json"
    meta_path.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    # 更新 meeting index
    with _LOCK:
        entries = _load_index(meeting_id)
        entries.append(meta.to_dict())
        _save_index(meeting_id, entries)

    return meta


def delete_material(material_id: str) -> bool:
    """删除材料及其文件目录，从 index 中移除 (v0.19.0)."""
    # 先查元数据找 meeting_id
    all_meetings = [d.name for d in _base().iterdir() if d.is_dir() and d.name != "temp"]
    found_dir = None
    target_meeting = None
    for mid in all_meetings:
        candidate = _base() / mid / material_id
        if candidate.is_dir():
            found_dir = candidate
            target_meeting = mid
            break
    if found_dir is None:
        return False
    # 删文件目录
    shutil.rmtree(found_dir)
    # 从 index 中移除
    entries = _load_index(target_meeting)
    entries = [e for e in entries if e.get("id") != material_id]
    _save_index(target_meeting, entries)
    return True


def list_materials(meeting_id: str) -> list[dict]:
    """列出会议的所有材料。"""
    return _load_index(meeting_id)


def get_material(material_id: str) -> MaterialMeta | None:
    """按 material_id 查找材料元数据（跨 meeting 搜索）。"""
    base = _base()
    if not base.exists():
        return None
    for meeting_dir in base.iterdir():
        if not meeting_dir.is_dir():
            continue
        mat_dir = meeting_dir / material_id
        if mat_dir.is_dir():
            meta_path = mat_dir / "meta.json"
            if meta_path.exists():
                try:
                    return MaterialMeta.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
                except Exception:
                    return None
    return None


def get_file_path(material_id: str) -> Path | None:
    """返回材料的文件路径（用于下载）。"""
    meta = get_material(material_id)
    if meta is None:
        return None
    # 多目录搜索
    base = _base()
    if not base.exists():
        return None
    for meeting_dir in base.iterdir():
        if not meeting_dir.is_dir():
            continue
        mat_dir = meeting_dir / material_id
        if mat_dir.is_dir():
            fp = mat_dir / meta.filename
            if fp.exists():
                return fp
    return None


def classify_file(filename: str) -> str:
    """按扩展名分类文件：text / image / binary"""
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "binary"


def read_text_content(material_id: str) -> tuple[str | None, bool, str | None]:
    """读取文本类材料的内容。

    返回 (content, truncated, error):
        content=None 表示不支持读取该类型
        truncated=True 表示被截断
        error 为错误信息（如果有）
    """
    meta = get_material(material_id)
    if meta is None:
        return None, False, "material not found"
    ext = Path(meta.filename).suffix.lower()
    if ext not in TEXT_EXTENSIONS:
        return None, False, None  # 不是文本类型，不报错
    fp = get_file_path(material_id)
    if fp is None:
        return None, False, "file not found on disk"
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > MAX_TEXT_CHARS
        if truncated:
            text = text[:MAX_TEXT_CHARS] + (
                f"\n\n[...已截断，原始文件约 {len(text)} 字，"
                f"完整内容已存入知识库可供搜索]"
            )
        return text, truncated, None
    except Exception as e:
        return None, False, str(e)
