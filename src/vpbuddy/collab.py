"""协作提问文档 (Phase 5.5 提议).

设计原则:
- 文件: docs/{meeting_id}/collab.md (跟 5 文档同级)
- 协议: Markdown 分隔符 + 简单状态机 (pending / answered)
- API: read_collab / ask_question / answer_question / list_pending
- 节流: 同 (mid, section, 相似问题) 一次会议只 1 次
- 线程安全: 用文件锁 (fcntl), 避免 batch_docs agent + chat agent 并发写冲突
- 角色: 主对话 agent + batch_docs agent + demo agent 三方都能读/写

典型用法 (3 个 agent 通过 terminal 调):
    python -c "from vpbuddy.collab import read_collab, list_pending, ask_question; print(list_pending('mtg01'))"
"""
from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 模块级文件锁: 同进程内多线程不会竞争, 跨进程用 fcntl
_FILE_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    """同文件路径返同一锁 (in-process)."""
    key = str(path.resolve())
    with _LOCKS_GUARD:
        if key not in _FILE_LOCKS:
            _FILE_LOCKS[key] = threading.Lock()
        return _FILE_LOCKS[key]


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """组合锁: 进程内 threading + 跨进程 fcntl (POSIX)."""
    in_proc = _get_lock(path)
    in_proc.acquire()
    fcntl_fd = None
    try:
        # POSIX fcntl 跨进程互斥
        if os.name == "posix":
            try:
                import fcntl
                lock_path = path.parent / f".{path.name}.lock"
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                fd = open(lock_path, "w")
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
                fcntl_fd = fd
            except Exception:
                # fcntl 失败不阻塞 (Windows 不支持; 容器可能禁 .lock)
                fcntl_fd = None
        yield
    finally:
        if fcntl_fd is not None:
            try:
                fcntl_fd.close()
            except Exception:
                pass
        in_proc.release()


# ── 路径 ──


def _default_docs_dir() -> Path:
    """默认 DOCS_DIR (跟 sub_session_controller 同步)."""
    from .sub_session_controller import DOCS_DIR
    return DOCS_DIR


def collab_path(meeting_id: str, docs_dir: Path | None = None) -> Path:
    """collab.md 路径."""
    return (docs_dir or _default_docs_dir()) / meeting_id / "collab.md"


# ── 解析 ──

# 匹配单个 Q 块 (含 pending 或 answered)
_QA_PATTERN = re.compile(
    r"### Q(?P<qid>[\w-]+)\s+\[(?P<section>[\w-]+)\]\s+(?P<question>.+?)\n"
    r"-\s+Asked by:\s+(?P<asker>[^\n]+?)\s+at\s+(?P<asked_at>[^\n]+?)\n"
    r"(?:-\s+Answered by:\s+(?P<answerer>[^\n]+?)\s+at\s+(?P<answered_at>[^\n]+?):\s+(?P<answer>[^\n]+?)\n)?",
    re.MULTILINE,
)


def read_collab(meeting_id: str, docs_dir: Path | None = None) -> str:
    """读 collab.md 全文. 不存在返空字符串.

    Args:
        meeting_id: 会议 ID
        docs_dir: docs 根目录, None 用默认 DOCS_DIR

    Returns:
        markdown 文本 (可能为空)
    """
    p = collab_path(meeting_id, docs_dir)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def parse_questions(meeting_id: str, docs_dir: Path | None = None) -> list[dict]:
    """解析 collab.md, 返所有 Q 块 (含 pending + answered).

    Returns:
        [{qid, section, question, asked_by, asked_at, answered?, answered_by?, answered_at?, answer?}, ...]
    """
    text = read_collab(meeting_id, docs_dir)
    if not text:
        return []
    out = []
    for m in _QA_PATTERN.finditer(text):
        d = {
            "qid": m["qid"],
            "section": m["section"],
            "question": m["question"].strip(),
            "asked_by": m["asker"].strip(),
            "asked_at": m["asked_at"].strip(),
        }
        if m["answerer"]:
            d["answered_by"] = m["answerer"].strip()
            d["answered_at"] = m["answered_at"].strip()
            d["answer"] = m["answer"].strip()
        out.append(d)
    return out


def list_pending(
    meeting_id: str,
    section: str | None = None,
    docs_dir: Path | None = None,
) -> list[dict]:
    """返未答问题列表. section=None 返全部.

    Returns:
        [{qid, section, question, asked_by, asked_at}, ...]
    """
    pending = [q for q in parse_questions(meeting_id, docs_dir) if "answered_by" not in q]
    if section:
        pending = [q for q in pending if q["section"] == section]
    return pending


def list_answered(
    meeting_id: str,
    section: str | None = None,
    docs_dir: Path | None = None,
) -> list[dict]:
    """返已答问题列表 (按 answered_at 倒序)."""
    answered = [q for q in parse_questions(meeting_id, docs_dir) if "answered_by" in q]
    if section:
        answered = [q for q in answered if q["section"] == section]
    answered.sort(key=lambda q: q.get("answered_at", ""), reverse=True)
    return answered


# ── 节流 ──


def _throttle_key(section: str, question: str) -> str:
    """节流 key: section + 问题前 30 字符小写 + 去多余空白."""
    q_norm = re.sub(r"\s+", " ", question.lower().strip())[:30]
    return f"[{section}] {q_norm}"


def _normalize_oneline(text: str) -> str:
    """归一化单行文本: 去多余空白, 换行 → 空格.

    question / answer 协议上**单行**. 多行内容会被归一化.
    """
    return re.sub(r"\s+", " ", text.strip())


# ── 写 ──


def _ensure_skeleton(text: str, meeting_id: str) -> str:
    """确保文件有 header + Pending + Answered 3 段."""
    ts = datetime.now().isoformat()
    if not text.strip():
        return (
            f"# Collab Doc — {meeting_id}\n"
            f"Generated: {ts}\n\n"
            f"## ❓ Pending Questions (未答)\n\n"
            f"## ✅ Answered Questions (已答)\n"
        )
    if "## ❓ Pending Questions" not in text:
        text += "\n\n## ❓ Pending Questions (未答)\n"
    if "## ✅ Answered Questions" not in text:
        text += "\n\n## ✅ Answered Questions (已答)\n"
    return text


def ask_question(
    meeting_id: str,
    section: str,
    question: str,
    asker: str = "agent",
    docs_dir: Path | None = None,
) -> dict:
    """追加 1 条 pending 提问. 节流: 同 (mid, section, 相似问题) 跳过.

    Args:
        meeting_id: 会议 ID
        section: 问题归属 section (req/arch/tasks/api/risk/docs/demo 等)
        question: 问题文本 (单行, 内部换行会被归一化为空格)
        asker: 提问者标识 (chat/batch_docs/demo)
        docs_dir: docs 根目录

    Returns:
        {
            "ok": True,
            "qid": "q-xxxxxxxx" | None (throttled 时),
            "status": "added" | "throttled" | "duplicate_exact",
        }
    """
    section = section.strip()
    question = _normalize_oneline(question)
    if not section or not question:
        return {"ok": False, "error": "section 和 question 必填", "status": "rejected"}

    p = collab_path(meeting_id, docs_dir)
    p.parent.mkdir(parents=True, exist_ok=True)

    with _file_lock(p):
        text = read_collab(meeting_id, docs_dir)
        text = _ensure_skeleton(text, meeting_id)

        # 节流检查
        existing_qs = parse_questions(meeting_id, docs_dir)
        throttle_k = _throttle_key(section, question)

        # 1. 严格重复 (同 section + 同 qid 完整字符串): 跳
        for q in existing_qs:
            if q["section"] == section and q["question"].strip() == question:
                return {
                    "ok": True,
                    "qid": q["qid"],
                    "status": "duplicate_exact",
                    "reason": f"same question already exists (qid={q['qid']})",
                }

        # 2. 相似节流 (前 30 字符匹配): 跳 — 节流 key 必须用 q["section"]
        for q in existing_qs:
            if _throttle_key(q["section"], q["question"]) == throttle_k:
                return {
                    "ok": True,
                    "qid": q["qid"],
                    "status": "throttled",
                    "reason": f"similar question exists (qid={q['qid']})",
                }

        # 3. 通过, 追加
        qid = f"q-{uuid.uuid4().hex[:8]}"
        ts = datetime.now().isoformat()
        block = (
            f"\n### Q{qid} [{section}] {question}\n"
            f"- Asked by: {asker} at {ts}\n"
        )
        text = text.replace(
            "## ❓ Pending Questions (未答)\n",
            f"## ❓ Pending Questions (未答)\n{block}",
            1,
        )
        p.write_text(text, encoding="utf-8")
        logger.info(f"[collab] {meeting_id} ask Q{qid} [{section}]: {question[:50]}")
        return {"ok": True, "qid": qid, "status": "added"}


def answer_question(
    meeting_id: str,
    qid: str,
    answer: str,
    answerer: str = "VP",
    docs_dir: Path | None = None,
) -> dict:
    """把 qid 标记为 answered. 把块从 Pending 段移到 Answered 段.

    Returns:
        {"ok": True, "qid": qid, "status": "answered"}
        或 {"ok": False, "error": "..."}
    """
    qid = qid.strip()
    answer = _normalize_oneline(answer)
    if not qid or not answer:
        return {"ok": False, "error": "qid 和 answer 必填", "status": "rejected"}

    p = collab_path(meeting_id, docs_dir)
    if not p.exists():
        return {"ok": False, "error": "collab.md not exist", "status": "not_found"}

    with _file_lock(p):
        text = p.read_text(encoding="utf-8")

        # 找这条 q 的 pending 块 (3 行格式, 不带 - Answered by: 行)
        # 用 lookahead 确保不匹配已答块
        pattern = re.compile(
            rf"### Q{re.escape(qid)}\s+\[(?P<section>[\w-]+)\]\s+(?P<question>.+?)\n"
            rf"-\s+Asked by:\s+(?P<asker>[^\n]+?)\s+at\s+(?P<asked_at>[^\n]+?)\n"
            rf"(?!-\s+Answered by:)",
            re.DOTALL,
        )
        m = pattern.search(text)
        if not m:
            # 检查是否存在 (可能已 answered)
            return {"ok": False, "error": f"qid {qid} not found or not pending", "status": "not_found"}

        ts = datetime.now().isoformat()
        new_block = (
            f"\n### Q{qid} [{m['section']}] {m['question'].strip()}\n"
            f"- Asked by: {m['asker'].strip()} at {m['asked_at'].strip()}\n"
            f"- Answered by: {answerer} at {ts}: {answer}\n"
        )
        # 从 Pending 段移除 (match.group(0) 是 ### Q... + 2 行 metadata)
        text = text.replace(m.group(0), "", 1)
        # 加到 Answered 段
        text = _ensure_skeleton(text, meeting_id)
        text = text.replace(
            "## ✅ Answered Questions (已答)\n",
            f"## ✅ Answered Questions (已答)\n{new_block}",
            1,
        )
        p.write_text(text, encoding="utf-8")
        logger.info(f"[collab] {meeting_id} answer Q{qid}: {answer[:50]}")
        return {"ok": True, "qid": qid, "status": "answered"}


def delete_question(
    meeting_id: str,
    qid: str,
    docs_dir: Path | None = None,
) -> dict:
    """删除某条 q (pending 或 answered 都能删). 谨慎用."""
    qid = qid.strip()
    if not qid:
        return {"ok": False, "error": "qid 必填", "status": "rejected"}

    p = collab_path(meeting_id, docs_dir)
    if not p.exists():
        return {"ok": False, "error": "collab.md not exist", "status": "not_found"}

    with _file_lock(p):
        text = p.read_text(encoding="utf-8")
        # 同时匹配 pending / answered 两种块
        pattern = re.compile(
            rf"### Q{re.escape(qid)}\s+\[[\w-]+\]\s+.+?(?=\n### Q|\n## |\Z)",
            re.DOTALL,
        )
        new_text, n = pattern.subn("", text)
        if n == 0:
            return {"ok": False, "error": f"qid {qid} not found", "status": "not_found"}
        p.write_text(new_text, encoding="utf-8")
        return {"ok": True, "qid": qid, "status": "deleted"}


# ── 统计 ──


def collab_stats(meeting_id: str, docs_dir: Path | None = None) -> dict:
    """返 collab.md 统计: pending/answered 各几条, 按 section 分组."""
    qs = parse_questions(meeting_id, docs_dir)
    pending = [q for q in qs if "answered_by" not in q]
    answered = [q for q in qs if "answered_by" in q]

    by_section_pending = {}
    for q in pending:
        by_section_pending[q["section"]] = by_section_pending.get(q["section"], 0) + 1

    return {
        "meeting_id": meeting_id,
        "total": len(qs),
        "pending": len(pending),
        "answered": len(answered),
        "by_section_pending": by_section_pending,
        "exists": collab_path(meeting_id, docs_dir).exists(),
    }
