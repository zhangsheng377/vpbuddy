"""storage — JSON 持久化(NFS)

设计原则(ADR-0001):
- 本地存储(NFS),不上云
- 路径:`{data_dir}/meetings/{meeting_id}.json`
- 每次修改立即落盘(crud 后调用 save)
- 简单,不要数据库 / 不要锁
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Optional

from .state import MeetingState


class StorageError(Exception):
    pass


class MeetingStorage:
    """会议状态持久化"""

    def __init__(self, data_dir: str | Path = "/home/zsd/vpbuddy/data/meetings"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, meeting_id: str) -> Path:
        return self.data_dir / f"{meeting_id}.json"

    def save(self, state: MeetingState) -> None:
        """保存状态到 JSON 文件(立即落盘)

        pydantic v2 的 model_dump_json 不支持 ensure_ascii 参数,
        改用 json.dumps + json.loads 包装一层来保留中文(可读)。
        """
        path = self._path(state.meeting_id)
        path.write_text(
            json.dumps(
                json.loads(state.model_dump_json(indent=2)),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load(self, meeting_id: str) -> MeetingState:
        """加载会议状态(不存在抛 StorageError)"""
        path = self._path(meeting_id)
        if not path.exists():
            raise StorageError(f"meeting {meeting_id} not found at {path}")
        return MeetingState.model_validate_json(path.read_text(encoding="utf-8"))

    def exists(self, meeting_id: str) -> bool:
        return self._path(meeting_id).exists()

    def list_meetings(self) -> List[str]:
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
            return True
        return False


def create_storage(data_dir: Optional[str] = None) -> MeetingStorage:
    """工厂函数(便于测试时传临时目录)"""
    if data_dir is None:
        data_dir = "/home/zsd/vpbuddy/data/meetings"
    return MeetingStorage(data_dir)
