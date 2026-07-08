"""Demo 多版本管理 (ADR-0024)

数据布局:
    data/docs/{meeting_id}/
    ├── demo_v1.html          # 旧版保留, 不删
    ├── demo_v2.html
    ├── demo_vN.html          # 最新
    ├── demo_latest.html      # symlink → demo_vN.html
    └── demo_manifest.json    # 版本清单

兼容 (2026-07-01 ADR-0024):
    老格式 demo/demo.html 单文件 → 首次迁移成 demo_v1.html + symlink → demo_latest.html
    老 demo.html 文件**保留不删**, 防 url 引用挂掉 (静态服务 URL 不变)

设计: KISS
- 永久保留所有版本 (v0.7+ 加 LRU)
- symlink on Linux/macOS, Windows 走 NTFS junction 或 copy (一期用 copy)
- manifest 写一次刷新一次 (会议级, IO 不频繁)
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path

logger = logging.getLogger(__name__)


# ── 路径辅助 ──


def _meeting_dir(meeting_id: str, docs_dir: Path | None = None) -> Path:
    """获取会议文档目录."""
    if docs_dir is None:
        from .ui_server import DOCS_DIR
        docs_dir = DOCS_DIR
    return Path(docs_dir) / meeting_id


def _manifest_path(meeting_id: str, docs_dir: Path | None = None) -> Path:
    return _meeting_dir(meeting_id, docs_dir) / "demo_manifest.json"


def _legacy_demo_path(meeting_id: str, docs_dir: Path | None = None) -> Path:
    """老格式 (ADR-0024 之前): data/docs/{mid}/demo/demo.html"""
    return _meeting_dir(meeting_id, docs_dir) / "demo" / "demo.html"


def _version_path(meeting_id: str, version: int, docs_dir: Path | None = None) -> Path:
    return _meeting_dir(meeting_id, docs_dir) / f"demo_v{version}.html"


def _latest_symlink(meeting_id: str, docs_dir: Path | None = None) -> Path:
    return _meeting_dir(meeting_id, docs_dir) / "demo_latest.html"


# ── Manifest 读写 ──


def load_manifest(meeting_id: str, docs_dir: Path | None = None) -> list[dict]:
    """读 manifest.json. 不存在 → 返 [].

    同时**自动迁移**老格式 demo/demo.html 到 v1 (一次性):
    - 老 demo/demo.html 存在 + 没 manifest → 当作 v1 搬过去, 写 manifest.
    """
    p = _manifest_path(meeting_id, docs_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[{meeting_id}] manifest 解析失败: {e}, 重置")
            return []

    # 首次: 检查老格式迁移
    legacy = _legacy_demo_path(meeting_id, docs_dir)
    if legacy.exists() and legacy.stat().st_size > 0:
        logger.info(f"[{meeting_id}] 发现老格式 demo/demo.html, 迁移成 v1")
        html = legacy.read_text(encoding="utf-8", errors="replace")
        mtime = datetime.fromtimestamp(legacy.stat().st_mtime, tz=UTC).isoformat()
        manifest = [{
            "version": 1,
            "created_at": mtime,
            "trigger": "legacy_migration",
            "summary": _extract_summary(html),
            "file_size": legacy.stat().st_size,
            "file": "demo_v1.html",
        }]
        # 写 v1 + manifest + symlink
        try:
            _meeting_dir(meeting_id, docs_dir).mkdir(parents=True, exist_ok=True)
            _version_path(meeting_id, 1, docs_dir).write_text(html, encoding="utf-8")
            save_manifest(meeting_id, manifest, docs_dir)
            _update_latest_symlink(meeting_id, 1, docs_dir)
            logger.info(f"[{meeting_id}] 迁移完成: 1 版本, 老 demo/demo.html 保留不删")
        except Exception as e:
            logger.warning(f"[{meeting_id}] 迁移失败: {e}")
        return manifest

    return []


def save_manifest(meeting_id: str, manifest: list[dict], docs_dir: Path | None = None) -> None:
    """写 manifest.json."""
    p = _manifest_path(meeting_id, docs_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 版本号推进 ──


def next_version(meeting_id: str, docs_dir: Path | None = None) -> int:
    """查当前最大版本号, 返 N+1. 没版本 → 返 1."""
    manifest = load_manifest(meeting_id, docs_dir)
    if not manifest:
        return 1
    return max(m.get("version", 0) for m in manifest) + 1


# ── Symlink 管理 ──


def _update_latest_symlink(meeting_id: str, version: int, docs_dir: Path | None = None) -> bool:
    """更新 demo_latest.html 指向 demo_v{version}.html.

    Linux/macOS 用 symlink, Windows 走复制 (一期, 简单).
    返 True 成功.
    """
    latest = _latest_symlink(meeting_id, docs_dir)
    target_file = f"demo_v{version}.html"

    # 删旧的 (symlink 或 文件)
    if latest.is_symlink() or latest.exists():
        try:
            latest.unlink()
        except Exception as e:
            logger.warning(f"[{meeting_id}] 删旧 latest 失败: {e}")

    try:
        if os.name == "nt":  # Windows — 复制而非 symlink
            import shutil
            shutil.copy(_version_path(meeting_id, version, docs_dir), latest)
            logger.info(f"[{meeting_id}] Windows 模式: demo_latest.html 复制完成 (→ {target_file})")
        else:  # Linux/macOS — symlink (相对路径, 简单)
            latest.symlink_to(target_file)
            logger.info(f"[{meeting_id}] demo_latest.html → {target_file}")
        return True
    except Exception as e:
        logger.warning(f"[{meeting_id}] 创建 demo_latest.html 失败: {e}")
        return False


# ── 主 API: 写新版本 ──


def write_demo_version(
    meeting_id: str,
    html: str,
    trigger: str = "agent_iterate",
    docs_dir: Path | None = None,
) -> dict:
    """写一个 demo 新版本, 更新 manifest + symlink.

    Args:
        meeting_id: 会议 ID
        html: 完整 HTML 内容
        trigger: 触发来源 (user_chat / docs_complete / auto_iterate / legacy_migration)
        docs_dir: 文档根目录 (测试用)

    Returns:
        {"ok": True, "version": N, "manifest": [...], "summary": "..."}
        或 {"ok": False, "error": "..."}
    """
    if not html or not html.strip():
        return {"ok": False, "error": "html 为空"}

    try:
        md = _meeting_dir(meeting_id, docs_dir)
        md.mkdir(parents=True, exist_ok=True)

        # 1. 推进版本号
        v = next_version(meeting_id, docs_dir)

        # 2. 写文件
        out = _version_path(meeting_id, v, docs_dir)
        out.write_text(html, encoding="utf-8")

        # 3. 更新 manifest
        summary = _extract_summary(html)
        now = datetime.now(UTC).isoformat()
        manifest = load_manifest(meeting_id, docs_dir)
        manifest.append({
            "version": v,
            "created_at": now,
            "trigger": trigger,
            "summary": summary,
            "file_size": out.stat().st_size,
            "file": out.name,
        })
        save_manifest(meeting_id, manifest, docs_dir)

        # 4. 更新 symlink
        _update_latest_symlink(meeting_id, v, docs_dir)

        logger.info(
            f"[{meeting_id}] demo v{v} 已写 ({out.stat().st_size}B, "
            f"trigger={trigger}, summary={summary[:30]!r})"
        )

        # 2026-07-01 ADR-0023 Phase 5: demo 新版本生成 → agent 主动 chat 通知
        # sub_session_controller 已经推 SSE demo-new-version, 这里只补 chat 主动消息.
        try:
            from .agent_proactive import trigger as _proactive_trigger
            _proactive_trigger(
                meeting_id,
                "demo_new_version",
                version=v,
                summary=summary,
            )
        except Exception:
            pass  # 不影响主流程

        return {
            "ok": True,
            "version": v,
            "manifest": manifest,
            "summary": summary,
            "file": out.name,
            "file_size": out.stat().st_size,
            "created_at": now,
        }
    except Exception as e:
        logger.error(f"[{meeting_id}] write_demo_version failed: {e}", exc_info=True)
        return {"ok": False, "error": str(e)[:200]}


# ── 摘要提取 ──


def _extract_summary(html: str) -> str:
    """从 demo HTML 提取首段文字作为一句话描述.

    规则: 找 <h1> / <h2> / <p> 文本, 去 HTML 标签, 取前 50 字符.
    """
    # 优先 h1
    m = re.search(r"<h1[^>]*>(.+?)</h1>", html, re.DOTALL | re.IGNORECASE)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if text:
            return text[:50]

    # 退到 h2
    m = re.search(r"<h2[^>]*>(.+?)</h2>", html, re.DOTALL | re.IGNORECASE)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if text:
            return text[:50]

    # 退到首个 p
    m = re.search(r"<p[^>]*>(.+?)</p>", html, re.DOTALL | re.IGNORECASE)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if text:
            return text[:50]

    return "untitled"


# ── 端点 handler 用的辅助 ──


def list_versions(meeting_id: str, docs_dir: Path | None = None) -> list[dict]:
    """返 [{version, created_at, summary, file_size, file}, ...], 倒序 (最新在前)."""
    manifest = load_manifest(meeting_id, docs_dir)
    return sorted(manifest, key=lambda m: m.get("version", 0), reverse=True)


def get_version_file(meeting_id: str, kind: str, docs_dir: Path | None = None) -> str:
    """读取文档版本号文件, 不存在则返回 '1'.

    用于非 demo 类文档的版本号持久化:
      data/docs/{meeting_id}/{kind}.version
    """
    md = _meeting_dir(meeting_id, docs_dir)
    vp = md / f"{kind}.version"
    if vp.exists():
        return vp.read_text(encoding="utf-8").strip() or "1"
    return "1"
