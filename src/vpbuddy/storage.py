"""storage — JSON 持久化(NFS)

# Auto-computed project root. P1#1 (2026-07-04)

设计原则(ADR-0001):
- 服务端存储(NFS / 本地磁盘),不依赖云存储
- 路径:`{data_dir}/meetings/{meeting_id}.json`
- 每次修改立即落盘(crud 后调用 save)
- 2026-07-05 fix(#4): 加 per-meeting 文件锁 + atomic write
"""

from __future__ import annotations
import json
import threading
import tempfile
import os
from pathlib import Path
from .state import MeetingState


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# 2026-07-05 fix(#4): per-meeting 文件锁, 防止并发写丢更新
_meeting_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()


def _get_lock(meeting_id: str) -> threading.Lock:
    """获取 per-meeting 锁 (线程安全创建)"""
    with _global_lock:
        if meeting_id not in _meeting_locks:
            _meeting_locks[meeting_id] = threading.Lock()
        return _meeting_locks[meeting_id]


def release_lock(meeting_id: str) -> None:
    """释放 per-meeting 锁，从字典中移除 (#10)"""
    with _global_lock:
        _meeting_locks.pop(meeting_id, None)


def cleanup_meeting_locks(data_dir: str | Path) -> int:
    """移除磁盘上已不存在的会议的残留锁 (#10)

    Returns:
        清理掉的锁数量
    """
    data_dir = Path(data_dir)
    removed = 0
    with _global_lock:
        stale = [
            mid for mid in _meeting_locks
            if not (data_dir / f"{mid}.json").exists()
        ]
        for mid in stale:
            _meeting_locks.pop(mid, None)
            removed += 1
    return removed


class StorageError(Exception):
    pass


class MeetingStorage:
    """会议状态持久化"""

    def __init__(self, data_dir: str | Path = PROJECT_ROOT / "data" / "meetings"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, meeting_id: str) -> Path:
        return self.data_dir / f"{meeting_id}.json"

    def save(self, state: MeetingState) -> None:
        """保存状态到 JSON 文件(线程安全 + atomic write)

        pydantic v2 的 model_dump_json 不支持 ensure_ascii 参数,
        改用 json.dumps + json.loads 包装一层来保留中文(可读)。
        """
        lock = _get_lock(state.meeting_id)
        with lock:
            path = self._path(state.meeting_id)
            data = json.dumps(
                json.loads(state.model_dump_json(indent=2)),
                ensure_ascii=False,
                indent=2,
            )
            # atomic write: 先写临时文件再 rename
            fd, tmp_path = tempfile.mkstemp(dir=str(self.data_dir), suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(fd)
                os.replace(tmp_path, str(path))
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    def load(self, meeting_id: str) -> MeetingState:
        """加载会议状态(不存在抛 StorageError)"""
        path = self._path(meeting_id)
        if not path.exists():
            raise StorageError(f"meeting {meeting_id} not found at {path}")
        return MeetingState.model_validate_json(path.read_text(encoding="utf-8"))

    def exists(self, meeting_id: str) -> bool:
        return self._path(meeting_id).exists()

    def list_meetings(self) -> list[str]:
        """列出所有会议 ID(按修改时间倒序)"""
        files = sorted(self.data_dir.glob("*.json"),
                       key=lambda p: p.stat().st_mtime,
                       reverse=True)
        return [f.stem for f in files]

    def delete(self, meeting_id: str) -> bool:
        """删除会议状态(慎用)"""
        path = self._path(meeting_id)
        if path.exists():
            path.unlink()
            release_lock(meeting_id)
            return True
        return False


def create_storage(data_dir: str | None = None) -> MeetingStorage:
    """工厂函数(便于测试时传临时目录)"""
    if data_dir is None:
        data_dir = PROJECT_ROOT / "data" / "meetings"
    return MeetingStorage(data_dir)
