"""agent_proactive module"""
from __future__ import annotations
from pathlib import Path


# Auto-computed project root. P1#1 (2026-07-04)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent




import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .realtime_server import push_event

logger = logging.getLogger(__name__)

# 节流内存 set: key = "{meeting_id}:{trigger_type}"
# 会议关闭时通过 clear_throttle(mid) 清理 (防止下次会话残留)
_TRIGGERED: set[str] = set()
_TRIGGER_LOCK = threading.Lock()

# 沉默阈值 (秒) — 距最后一次 transcript segment 多久没新内容算"停顿"
SILENCE_THRESHOLD_SEC = 5 * 60  # 5 分钟

# 会议节点 (会议开始后秒数)
TIME_NODE_SECONDS = [10 * 60, 30 * 60, 60 * 60]  # 10 / 30 / 60 分钟

# ── trigger 消息模板 ──
# 简单 key/value, 不引 jinja2 (KISS)

# 2026-07-03 v0.8.4: trigger 推到 collab 面板, section 反映主题
_TRIGGER_TO_SECTION = {
    "docs_complete": "docs",
    "risk_threshold": "risk",
    "demo_new_version": "demo",
    "silence": "docs",
    "time_node": "docs",
}


def _trigger_docs_complete(meeting_id: str, **kwargs: Any) -> str:
    """6 文档全部生成完成 (提示, 非问题)."""
    state_summary = kwargs.get("state_summary", "")
    return "\n".join([
        "📄 6 文档已生成 (v1)。",
        state_summary[:300] if state_summary else "请到 docs 面板查看。",
    ]).strip()


def _trigger_risk_threshold(meeting_id: str, **kwargs: Any) -> str:
    """RISK 累计 >=3 (提示)."""
    risk_list = kwargs.get("risk_list", [])
    items = "\n".join(f"- {r}" for r in risk_list[:3]) if risk_list else "- (未提供细节)"
    return "\n".join([
        f"⚠️ 当前会议已记录 {len(risk_list)} 条风险:",
        items,
    ]).strip()


def _trigger_demo_new_version(meeting_id: str, **kwargs: Any) -> str:
    """demo 新版本生成 (提示)."""
    version = kwargs.get("version", "?")
    summary = kwargs.get("summary", "")
    return f"🎨 Demo v{version} 已生成: {summary}"


def _trigger_silence(meeting_id: str, **kwargs: Any) -> str:
    """5 分钟沉默 (提示)."""
    silence_sec = int(kwargs.get("silence_sec", SILENCE_THRESHOLD_SEC))
    return f"🤔 似乎大家在思考, 已经沉默 {silence_sec // 60} 分钟。"


def _trigger_time_node(meeting_id: str, **kwargs: Any) -> str:
    """会议节点 (10/30/60 分钟) (提示)."""
    elapsed_sec = int(kwargs.get("elapsed_sec", 0))
    minutes = elapsed_sec // 60
    facts_count = int(kwargs.get("facts_count", 0))
    return f"⏱️ 会议进行 {minutes} 分钟, 已累积 {facts_count} 条事实。"


_TRIGGER_BUILDERS = {
    "docs_complete": _trigger_docs_complete,
    "risk_threshold": _trigger_risk_threshold,
    "demo_new_version": _trigger_demo_new_version,
    "silence": _trigger_silence,
    "time_node": _trigger_time_node,
}


def _key(meeting_id: str, trigger_type: str) -> str:
    return f"{meeting_id}:{trigger_type}"


def clear_throttle(meeting_id: str) -> int:
    """清除某会议的所有节流标记 (会议关闭时调用)."""
    cleared = 0
    with _TRIGGER_LOCK:
        to_remove = [k for k in _TRIGGERED if k.startswith(f"{meeting_id}:")]
        for k in to_remove:
            _TRIGGERED.discard(k)
            cleared += 1
    if cleared:
        logger.info("proactive: cleared %d throttle keys for meeting=%s", cleared, meeting_id)
    return cleared


def _run_async(meeting_id: str, trigger_type: str, text: str) -> None:
    """落 collab.md + 推 SSE collab-update (2026-07-03 v0.8.4).

    失败不抛, 仅 log. 走 collab.ask_question 写到 docs/{mid}/collab.md,
    节流交给 collab 模块 (同 (mid, section, 相似问题) 1 次会议只 1 条).
    """
    from .collab import ask_question

    section = _TRIGGER_TO_SECTION.get(trigger_type, "docs")
    asker = f"agent-proactive:{trigger_type}"
    try:
        result = ask_question(meeting_id, section, text, asker=asker)
    except Exception as e:
        logger.exception("proactive: ask_question failed mid=%s trigger=%s: %s", meeting_id, trigger_type, e)
        return

    # 推 SSE collab-update 让前端 🤝 协作疑问面板展开
    # status=added 才是真新增; throttled / duplicate_exact 也推, 让前端知道节流工作
    qid = result.get("qid")
    if not qid:
        logger.debug("proactive: ask_question returned no qid mid=%s: %s", meeting_id, result)
        return
    try:
        push_event(meeting_id, "collab-update", {
            "action": "ask",
            "qid": qid,
            "section": section,
            "status": result.get("status", "added"),
            "question": text,
            "asker": asker,
        })
    except Exception as e:
        logger.warning("proactive: push_event collab-update failed mid=%s: %s", meeting_id, e)


def trigger(
    meeting_id: str,
    trigger_type: str,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """触发一次 agent 主动消息 (2026-07-03 v0.8.4 → 推 collab panel).

    Args:
        meeting_id: 会议 ID
        trigger_type: docs_complete / risk_threshold / demo_new_version / silence / time_node
        **kwargs: 透传给模板构造器

    Returns:
        None if 已节流跳过; 否则返回 dict (含 trigger_type / message / created_at /
                collab_qid / section)

    行为:
        1. 节流 set 检查 (同 (mid, type) 跳过)
        2. 标记已触发
        3. 调 builder 构造消息文本
        4. 异步 (daemon thread) 写 collab.md + 推 SSE collab-update
    """
    if trigger_type not in _TRIGGER_BUILDERS:
        logger.warning("proactive: unknown trigger_type=%s mid=%s", trigger_type, meeting_id)
        return None

    k = _key(meeting_id, trigger_type)
    with _TRIGGER_LOCK:
        if k in _TRIGGERED:
            logger.debug("proactive: throttled mid=%s trigger=%s", meeting_id, trigger_type)
            return None
        _TRIGGERED.add(k)

    builder = _TRIGGER_BUILDERS[trigger_type]
    text = builder(meeting_id, **kwargs)
    logger.info("proactive: firing mid=%s trigger=%s text_len=%d", meeting_id, trigger_type, len(text))

    t = threading.Thread(
        target=_run_async,
        args=(meeting_id, trigger_type, text),
        daemon=True,
        name=f"proactive-{trigger_type}-{meeting_id[:8]}",
    )
    t.start()

    section = _TRIGGER_TO_SECTION.get(trigger_type, "docs")
    return {
        "trigger_type": trigger_type,
        "meeting_id": meeting_id,
        "message": text,
        "section": section,
        "fired_at": datetime.now().isoformat(),
    }


# ── 后台监控 (silence / time_node) ──
# 单独线程, 每 60s 检查所有活跃会议.
# 用线程事件优雅退出 (测试时可手动 stop).
_MONITOR_THREAD: threading.Thread | None = None
_MONITOR_STOP = threading.Event()
_MONITOR_INTERVAL = int(os.environ.get("VPBUDDY_PROACTIVE_INTERVAL", "60"))


def _monitor_loop() -> None:
    """silence + time_node 后台监控循环.

    检查每个活跃会议的 state JSON:
        - last_updated 距现在 > SILENCE_THRESHOLD_SEC → trigger('silence')
        - meeting_start 距现在 > TIME_NODE_SECONDS → trigger('time_node', elapsed_sec=...)

    节流交给 trigger() 内部处理 (同 mid + 同 type 只触发 1 次).
    """
    from .storage import MeetingStorage  # 局部 import 避免循环

    while not _MONITOR_STOP.is_set():
        try:
            if not data_dir.exists():
                _MONITOR_STOP.wait(_MONITOR_INTERVAL)
                continue
            storage = MeetingStorage(data_dir)
            now = time.time()
            for state_path in data_dir.glob("*.json"):
                # 跳过 stream.json / chat.json / 非 meeting state 文件
                if state_path.suffix != ".json" or not _is_meeting_state(state_path):
                    continue
                mid = state_path.stem
                try:
                    state = storage.load(mid)
                except Exception:
                    continue

                # 1. silence 检测
                last_upd = state.last_updated
                if last_upd:
                    try:
                        last_ts = datetime.fromisoformat(last_upd).timestamp()
                    except Exception:
                        last_ts = state_path.stat().st_mtime
                    silence_sec = now - last_ts
                    if silence_sec >= SILENCE_THRESHOLD_SEC:
                        trigger(mid, "silence", silence_sec=int(silence_sec))

                # 2. time_node 检测 (基于 started_at)
                if hasattr(state, "started_at") and state.started_at:
                    try:
                        start_ts = datetime.fromisoformat(state.started_at).timestamp()
                    except Exception:
                        start_ts = state_path.stat().st_ctime
                    elapsed = now - start_ts
                    # 找到下一个未触发的节点
                    for node_sec in TIME_NODE_SECONDS:
                        if elapsed >= node_sec:
                            # 用 "10min / 30min / 60min" 作为 trigger key 后缀防止重复
                            sub = f"time_node_{node_sec}"
                            k2 = _key(mid, sub)
                            with _TRIGGER_LOCK:
                                if k2 not in _TRIGGERED:
                                    _TRIGGERED.add(k2)
                                    facts_count = (
                                        len(state.requirements)
                                        + len(state.goals)
                                        + len(state.features)
                                        + len(state.risks)
                                        + len(state.open_questions)
                                    )
                                    threading.Thread(
                                        target=_run_async,
                                        args=(mid, sub, _trigger_time_node(mid, elapsed_sec=int(elapsed), facts_count=facts_count)),
                                        daemon=True,
                                    ).start()
                            # 旧的 time_node key 也置上避免重复
                            with _TRIGGER_LOCK:
                                _TRIGGERED.add(_key(mid, "time_node"))
        except Exception as e:
            logger.exception("proactive monitor loop error: %s", e)

        _MONITOR_STOP.wait(_MONITOR_INTERVAL)


def _is_meeting_state(path: Path) -> bool:
    """判断 JSON 文件是否是 MeetingState (排除 stream.json / chat.json)."""
    # MeetingState 文件名 = "{meeting_id}.json"
    # stream.json / chat.json 都在 _load_stream_meta / _load_chat_history 内开
    # 这里只过滤明显的非 state 文件: 包含 ".stream.json" / ".chat.json" 不可能 (通配符只 *.json)
    # 所以只能走"读 json 看 platform 字段"判定. KISS: 跳过有特殊前缀的.
    name = path.name
    return not (name.endswith(".stream.json") or name.endswith(".chat.json"))


def start_monitor(daemon: bool = True) -> None:
    """启动后台监控线程. 幂等."""
    global _MONITOR_THREAD
    if _MONITOR_THREAD is not None and _MONITOR_THREAD.is_alive():
        return
    _MONITOR_STOP.clear()
    _MONITOR_THREAD = threading.Thread(
        target=_monitor_loop,
        daemon=daemon,
        name="proactive-monitor",
    )
    _MONITOR_THREAD.start()
    logger.info("proactive monitor started, interval=%ds", _MONITOR_INTERVAL)


def stop_monitor(timeout: float = 2.0) -> None:
    """停止后台监控线程."""
    _MONITOR_STOP.set()
    if _MONITOR_THREAD is not None:
        _MONITOR_THREAD.join(timeout=timeout)
