"""Sub-session controller — 后台循环触发 6 个子 session

设计原则(ADR-0006 + ADR-0009 修正版):
- 每种交付物一个独立子 session(session_id 固定 = 复用 AIAgent 实例)
- 子 session 自己判断、自己写文件(我们不写 JSON 不做中介)
- prompt 不指定具体工具名(让 LLM 自己选合适的)
- in-process AIAgent(2026-06-22 落地):`from run_agent import AIAgent(session_id=...)`
  → LLM 真共享 session 上下文,跨轮询记得上次输出
- ThreadPoolExecutor(max_workers=3) 真并行触发(2026-06-22 落地)
- 老代码 `subprocess.run("hermes chat")` 作为 fallback(hermes-agent 未装时仍可跑)

典型用法:
    python -m vpbuddy.sub_session_controller                # 主循环
    python -m vpbuddy.sub_session_controller --once         # 跑一轮
    python -m vpbuddy.sub_session_controller --meeting abc  # 单会议 6 doc
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .storage import MeetingStorage

logger = logging.getLogger(__name__)

# === 关键 (2026-06-22 §19/§20 踩坑):KB 模型默认走 cache,不联网 ===
# conftest.py 在 pytest 进程设了这俩 env var,但跑 `python -m vpbuddy.sub_session_controller`
# 时 conftest 不加载 — KB add_document 会卡 53min(详见 docs/部署/踩坑记录.md §19)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 默认路径(可通过环境变量覆盖)
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", "/home/zsd/vpbuddy/data/meetings"))
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", "/home/zsd/vpbuddy/docs"))
PROMPTS_DIR = Path(__file__).parent / "prompts"
POLL_INTERVAL = int(os.environ.get("VPBUDDY_POLL_INTERVAL", "30"))
PARALLEL_WORKERS = int(os.environ.get("VPBUDDY_PARALLEL_WORKERS", "3"))

# 6 个子 session 对应 6 种 doc_kind
DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]

# === AIAgent 缓存(关键:跨轮询复用同一 AIAgent → 持久化 session) ===
# 2026-06-22 ADR-0009 落地:每个 (meeting_id, doc_kind) 一个 AIAgent 实例
# 同 session_id 多次触发 = 同一 session 历史 → LLM 跨次记得上下文
_AGENT_CACHE: Dict[str, Any] = {}

# === KB 状态共享字典(2026-06-22) ===
# UI / controller / 日志都查这个,key = (meeting_id, doc_kind)
# value = {status: queued|stored|failed|retrying, attempts, started_at, completed_at?, doc_id?, error?}
_KB_STATUS: Dict[tuple, Dict[str, Any]] = {}
_KB_STATUS_LOCK = threading.Lock()  # 防止并发触发时 key 还没设就 update

# AIAgent 是否可用(2026-06-22: import 失败时 fallback 到 subprocess)
_AGENT_AVAILABLE = False
_AIAgent: Optional[type] = None
try:
    from run_agent import AIAgent as _AIAgent  # type: ignore
    _AGENT_AVAILABLE = True
    logger.info("AIAgent in-process 模式启用 (from run_agent import AIAgent)")
except ImportError as e:
    logger.warning(
        f"AIAgent import 失败 ({e}),将 fallback 到 subprocess.run('hermes chat')"
    )


def _agent_session_id(meeting_id: str, doc_kind: str) -> str:
    """构造稳定的 session_id"""
    return f"meeting:{meeting_id}:{doc_kind}"


def _get_or_create_agent(meeting_id: str, doc_kind: str) -> Any:
    """获取或创建缓存的 AIAgent 实例(2026-06-22 落地)

    同 (meeting_id, doc_kind) 多次调用 → 同一 AIAgent → 同一 session_id →
    Hermes 内部 session 历史累积 → LLM 跨次记得上下文
    """
    sid = _agent_session_id(meeting_id, doc_kind)
    if sid not in _AGENT_CACHE:
        if not _AGENT_AVAILABLE or _AIAgent is None:
            raise RuntimeError(
                "AIAgent not available — cannot create agent. "
                "Install hermes-agent or use VPBUDDY_DIRECT=1 mode."
            )
        _AGENT_CACHE[sid] = _AIAgent(
            session_id=sid,
            enabled_toolsets=["terminal", "file"],
            platform="subagent",
            quiet_mode=True,
            max_iterations=30,
            model=os.environ.get("VPBUDDY_LLM_MODEL", "MiniMax-M3"),
            ephemeral_system_prompt=(
                f"你是 VPBuddy 的 {doc_kind} 子 session。\n"
                f"session_id 固定 = {sid}。\n"
                f"输出文件路径(必须写到这里):{get_doc_path(meeting_id, doc_kind)}\n"
                "\n"
                "【硬性要求 — 不遵守 = 任务失败】\n"
                "1. 你**必须**调用 file toolset 里的 write_file 工具,把完整文档内容写入到上面的输出文件路径。\n"
                "2. 不要只在文字响应里输出文档 — 文字响应不算完成任务。\n"
                "3. 先调用 read_file 读取当前 state JSON 和(若存在)旧文档,再决定如何更新。\n"
                "4. 如果 state 与旧文档完全一致,仍必须写一个空变更说明文件(标记 '无变化')。\n"
                "\n"
                "工作流:\n"
                "  read_file(state) → 解析 facts → 生成文档内容 → write_file(目标路径, 完整内容) → 退出\n"
            ),
        )
        logger.info(f"创建新 AIAgent: session_id={sid}")
    return _AGENT_CACHE[sid]


def list_active_meetings() -> List[str]:
    """列出活跃会议(有 MeetingState JSON 文件就算活跃)"""
    if not DATA_DIR.exists():
        return []
    return [p.stem for p in DATA_DIR.glob("*.json")]


def get_doc_path(meeting_id: str, doc_kind: str) -> Path:
    """获取某 doc_kind 的输出文件路径"""
    base = DOCS_DIR / meeting_id
    if doc_kind == "demo":
        # demo 是目录,主输出 demo.html
        return base / "demo" / "demo.html"
    return base / f"{doc_kind}.md"


def format_state_summary(state) -> str:
    """把 MeetingState 格式化成 LLM 友好的摘要(全文,不截断——YAGNI)"""
    parts = [f"# 会议 {state.meeting_id} 累积摘要", ""]
    parts.append(f"- 平台: {state.platform.value}")
    parts.append(f"- 最后更新: {state.last_updated}")
    parts.append("")

    if state.requirements:
        parts.append(f"## 需求 ({len(state.requirements)} 条)")
        for r in state.requirements:
            parts.append(f"- [{r.priority.value.upper()}] {r.id}: {r.text}"
                         + (f" (来自: {r.speaker_name})" if r.speaker_name else ""))
        parts.append("")

    if state.goals:
        parts.append(f"## 目标 ({len(state.goals)} 条)")
        for g in state.goals:
            parts.append(f"- {g.id}: {g.text}")
        parts.append("")

    if state.features:
        parts.append(f"## 功能 ({len(state.features)} 条)")
        for f in state.features:
            parts.append(f"- {f.id}: {f.text}")
        parts.append("")

    if state.risks:
        parts.append(f"## 风险 ({len(state.risks)} 条)")
        for r in state.risks:
            parts.append(f"- [{r.severity.value.upper()}] {r.id}: {r.text}")
        parts.append("")

    if state.open_questions:
        parts.append(f"## 开放问题 ({len(state.open_questions)} 条)")
        for q in state.open_questions:
            parts.append(f"- {q.id}: {q.text}")
        parts.append("")

    if state.speaker_map:
        parts.append(f"## 说话人映射")
        for sid, name in state.speaker_map.items():
            parts.append(f"- {sid} → {name}")

    return "\n".join(parts)


def render_prompt(doc_kind: str, meeting_id: str, state_summary: str, last_doc: Optional[str]) -> str:
    """渲染子 session 的 prompt(优先用专属模板,fallback 通用模板)"""
    template_path = PROMPTS_DIR / f"{doc_kind}.md"
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else _generic_template()

    doc_path = get_doc_path(meeting_id, doc_kind)
    return template.format(
        meeting_id=meeting_id,
        doc_kind=doc_kind,
        state_summary=state_summary,
        last_doc=last_doc or "(无 — 首次创建)",
        doc_path=str(doc_path),
    )


def _generic_template() -> str:
    """通用 prompt(各 doc_kind 没有专属模板时用)"""
    return """你是 VPBuddy 的 {doc_kind} 子 session。
session_id 固定: meeting:{meeting_id}:{doc_kind}
文档输出路径: {doc_path}

【职责】
持续维护本次会议的 {doc_kind} 文档。

【当前累积】
{state_summary}

【你之前的输出】
{last_doc}

【判断】
1. 累积有变化?→ 改文档
2. V 显式说"更新 {doc_kind}"?→ 立即改
3. 否则?→ 输出"无变化",退出

【YAGNI】
- 不主动加"可能需要"的章节
- 跑起来再说,有问题再调
- 用你手头能用的工具读写文件(具体工具名我不指定,Hermes 会告诉你)
"""


def _trigger_via_subprocess(prompt: str, meeting_id: str, doc_kind: str, timeout: int = 300) -> Dict:
    """Fallback 路径:subprocess.run('hermes chat')

    当 AIAgent 未 import 成功时使用。每次都是新 UUID session,无历史持久化。
    """
    cmd = ["hermes", "chat", "-q", prompt, "-Q"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "triggered": (proc.returncode == 0),
            "session_id": _agent_session_id(meeting_id, doc_kind),
            "agent_response": proc.stdout[-500:] if proc.stdout else "",
            "error": None if proc.returncode == 0 else f"hermes exit {proc.returncode}: {proc.stderr[:200]}",
        }
    except subprocess.TimeoutExpired:
        return {"triggered": False, "session_id": _agent_session_id(meeting_id, doc_kind), "error": "hermes timeout"}
    except FileNotFoundError:
        return {"triggered": False, "session_id": _agent_session_id(meeting_id, doc_kind), "error": "hermes CLI not found in PATH"}
    except Exception as e:
        return {"triggered": False, "session_id": _agent_session_id(meeting_id, doc_kind), "error": f"{type(e).__name__}: {e}"}


def _trigger_via_aiagent(prompt: str, meeting_id: str, doc_kind: str, timeout: int = 180) -> Dict:
    """主路径:in-process AIAgent(2026-06-22 落地,真 session 持久化)

    同 (meeting_id, doc_kind) 多次调用复用同一 AIAgent → 跨轮询 LLM 记得上次输出

    注意:不用 signal.alarm(会和 hermes_bootstrap 的信号冲突),用线程 daemon 监控。
    """
    t_start = time.time()
    logger.info(f"[{meeting_id}/{doc_kind}] _trigger_via_aiagent start, prompt_len={len(prompt)}")

    holder: Dict[str, Any] = {"done": False, "result": None, "error": None}

    def _runner():
        try:
            agent = _get_or_create_agent(meeting_id, doc_kind)
            response = agent.chat(prompt)
            holder["result"] = response
        except Exception as e:
            holder["error"] = e
        finally:
            holder["done"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if not holder["done"]:
        logger.warning(f"[{meeting_id}/{doc_kind}] AIAgent timeout after {timeout}s, thread still alive")
        return {
            "triggered": False,
            "session_id": _agent_session_id(meeting_id, doc_kind),
            "error": f"AIAgent timeout ({timeout}s)",
            "agent_path": "in-process",
        }
    logger.info(f"[{meeting_id}/{doc_kind}] _trigger_via_aiagent done in {time.time()-t_start:.1f}s")
    if holder["error"]:
        return {
            "triggered": False,
            "session_id": _agent_session_id(meeting_id, doc_kind),
            "error": f"AIAgent: {type(holder['error']).__name__}: {str(holder['error'])[:200]}",
            "agent_path": "in-process",
        }
    response = holder["result"]
    return {
        "triggered": True,
        "session_id": _agent_session_id(meeting_id, doc_kind),
        "agent_response": (response or "")[-500:] if response else "",
        "agent_path": "in-process",
        "error": None,
    }


def trigger_sub_session(meeting_id: str, doc_kind: str, dry_run: bool = False) -> Dict[str, Any]:
    """触发一个子 session(2026-06-22 落地 in-process AIAgent + ThreadPoolExecutor 真并行)

    Args:
        meeting_id: 会议 ID
        doc_kind: req/arch/tasks/api/risk/demo
        dry_run: True = 只渲染 prompt 不调 LLM

    Returns:
        {
            "session_id": "meeting:...",
            "triggered": bool,
            "prompt": str,
            "agent_response": str?,
            "agent_path": "in-process" | "subprocess" | "direct",
            "error": str?,
        }
    """
    import time as _t
    t0 = _t.time()
    sid = _agent_session_id(meeting_id, doc_kind)
    result: Dict[str, Any] = {"session_id": sid, "triggered": False, "error": None}

    # 1. 读累积
    logger.info(f"[{meeting_id}/{doc_kind}] load state...")
    try:
        state = MeetingStorage(data_dir=str(DATA_DIR)).load(meeting_id)
    except Exception as e:
        result["error"] = f"load_state: {e}"
        return result
    logger.info(f"[{meeting_id}/{doc_kind}] state loaded in {_t.time()-t0:.1f}s")

    # 2. 读上次输出
    doc_path = get_doc_path(meeting_id, doc_kind)
    last_doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else None

    # 3. 渲染 prompt
    prompt = render_prompt(doc_kind, meeting_id, format_state_summary(state), last_doc)
    result["prompt"] = prompt

    # 4. dry-run: 只返 prompt 不调 LLM(测试用)
    if dry_run:
        result["triggered"] = False
        result["dry_run"] = True
        return result

    # 5. VPBUDDY_DIRECT=1: 主 session 用 write_file 写文件,不调 LLM
    if os.environ.get("VPBUDDY_DIRECT"):
        result["triggered"] = True
        result["agent_path"] = "direct"
        result["doc_path"] = str(doc_path)
        return result

    # 6. 真触发 — 优先 in-process AIAgent(真 session),fallback 到 subprocess
    if _AGENT_AVAILABLE:
        result = _trigger_via_aiagent(prompt, meeting_id, doc_kind)
    else:
        result = _trigger_via_subprocess(prompt, meeting_id, doc_kind)
    logger.info(f"[{meeting_id}/{doc_kind}] trigger done in {_t.time()-t0:.1f}s, triggered={result.get('triggered')}")

    # 6.5 验证 doc 真写盘(2026-06-22 修:agent.chat() 返回文字不代表调了 write_file)
    # agent 经常只在文字响应里输出文档,必须强制验证 doc_path 存在
    if result.get("triggered") and not doc_path.exists():
        size_hint = result.get("agent_response", "")[:200]
        logger.warning(
            f"[{meeting_id}/{doc_kind}] agent returned text but {doc_path} not written, "
            f"agent_response_tail={size_hint!r}"
        )
        # Fallback(2026-06-22):agent 工具调用弱时,自动用代码生成 docs
        # VPBUDDY_FALLBACK=1(默认)→ 自动用 doc_fallback.generate_and_write 写盘
        if os.environ.get("VPBUDDY_FALLBACK", "1") != "0":
            try:
                from .doc_fallback import generate_and_write
                written = generate_and_write(meeting_id, doc_kind, state, doc_path)
                logger.info(f"[{meeting_id}/{doc_kind}] fallback wrote {written} ({written.stat().st_size}B)")
                result["doc_path"] = str(written)
                result["doc_size"] = written.stat().st_size
                result["fallback_used"] = True
            except Exception as e:
                logger.error(f"[{meeting_id}/{doc_kind}] fallback also failed: {e}")
                result["triggered"] = False
                result["error"] = f"agent did not write {doc_path} AND fallback failed: {type(e).__name__}: {e}"
                return result
        else:
            result["triggered"] = False
            result["error"] = f"agent did not write {doc_path} (response: {size_hint!r})"
            return result
    if result.get("triggered") and doc_path.exists():
        result["doc_size"] = doc_path.stat().st_size

    # 7. 写完文档后,自动存进知识库(2026-06-22 增强:kb_status + 3 次 retry)
    if result.get("triggered") and doc_path.exists():
        content = doc_path.read_text(encoding="utf-8")
        # 把 KB 状态挂在 _KB_STATUS 全局 dict,UI / controller / log 都能查
        # key = (meeting_id, doc_kind),value = {status, error, attempts, ts}
        with _KB_STATUS_LOCK:
            _KB_STATUS[(meeting_id, doc_kind)] = {
                "status": "queued",
                "attempts": 0,
                "started_at": datetime.now().isoformat(),
                "error": None,
            }
        result["kb_queued"] = True
        result["kb_status_key"] = [meeting_id, doc_kind]

        # KB 存 background thread 跑,sentence-transformers 冷加载 40s 不阻塞 trigger
        # 失败自动 retry 3 次(指数退避: 5s / 25s / 125s)
        def _kb_bg():
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    from .knowledge_base import get_kb
                    kb = get_kb()
                    doc_id = kb.add_document(meeting_id, doc_kind, content)
                    with _KB_STATUS_LOCK:
                        _KB_STATUS[(meeting_id, doc_kind)] = {
                            "status": "stored",
                            "attempts": attempt,
                            "started_at": _KB_STATUS[(meeting_id, doc_kind)]["started_at"],
                            "completed_at": datetime.now().isoformat(),
                            "doc_id": doc_id,
                            "error": None,
                        }
                    logger.info(f"[{meeting_id}/{doc_kind}] KB stored (doc_id={doc_id}, attempt {attempt})")
                    return
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {str(e)[:200]}"
                    logger.warning(f"[{meeting_id}/{doc_kind}] KB store attempt {attempt}/{max_retries} failed: {err_msg}")
                    with _KB_STATUS_LOCK:
                        # 防御:key 可能被外部 _KB_STATUS.clear() 清掉,用 setdefault 保住
                        _KB_STATUS.setdefault((meeting_id, doc_kind), {
                            "status": "queued", "attempts": 0,
                            "started_at": datetime.now().isoformat(), "error": None,
                        })
                        _KB_STATUS[(meeting_id, doc_kind)].update({
                            "status": "retrying" if attempt < max_retries else "failed",
                            "attempts": attempt,
                            "error": err_msg,
                        })
                    if attempt < max_retries:
                        import time as _t
                        _t.sleep(5 ** attempt)
            logger.error(f"[{meeting_id}/{doc_kind}] KB store FAILED after {max_retries} attempts: {_KB_STATUS.get((meeting_id, doc_kind), {}).get('error')}")

        threading.Thread(target=_kb_bg, daemon=True).start()

    return result


def get_kb_status(meeting_id: Optional[str] = None) -> Dict[str, Any]:
    """获取 KB 状态摘要(给 UI / CLI / log 用)

    Args:
        meeting_id: 只看某个会议的状态,None = 全部

    Returns:
        {
          "summary": {"total": N, "stored": X, "failed": Y, "queued": Z, "retrying": W},
          "items": [
            {"meeting_id": ..., "doc_kind": ..., "status": ..., "attempts": ..., "error": ...},
            ...
          ]
        }
    """
    items = []
    summary = {"total": 0, "stored": 0, "failed": 0, "queued": 0, "retrying": 0}
    with _KB_STATUS_LOCK:
        snapshot = list(_KB_STATUS.items())
    for (mid, kind), st in snapshot:
        if meeting_id and mid != meeting_id:
            continue
        item = {"meeting_id": mid, "doc_kind": kind, **st}
        items.append(item)
        summary["total"] += 1
        status_key = st.get("status", "queued")
        summary[status_key] = summary.get(status_key, 0) + 1

    # === 2026-06-22 修复:_KB_STATUS 是 process-local,CLI/UI 在新进程里 = 0 ===
    # fallback:从 KB DB 查 documents 数量(已落库的)
    if summary["total"] == 0:
        try:
            import sqlite3
            from .knowledge_base import get_kb
            kb = get_kb()
            conn = sqlite3.connect(kb.db_path)
            conn.enable_load_extension(True)
            try:
                import sqlite_vec
                sqlite_vec.load(conn)
            except Exception:
                pass
            c = conn.cursor()
            if meeting_id:
                c.execute("SELECT doc_kind FROM documents WHERE meeting_id=?", (meeting_id,))
                for (kind,) in c.fetchall():
                    items.append({"meeting_id": meeting_id, "doc_kind": kind, "status": "stored", "attempts": 0, "error": None, "source": "kb_db"})
                    summary["stored"] += 1
            else:
                c.execute("SELECT meeting_id, doc_kind FROM documents")
                for mid, kind in c.fetchall():
                    items.append({"meeting_id": mid, "doc_kind": kind, "status": "stored", "attempts": 0, "error": None, "source": "kb_db"})
                    summary["stored"] += 1
            conn.close()
            summary["total"] = summary["stored"]
        except Exception as e:
            logger.warning(f"[kb_status] fallback to KB DB failed: {e}")

    return {"summary": summary, "items": items}


def run_one_round(
    meeting_ids: Optional[List[str]] = None,
    dry_run: bool = False,
    parallel: bool = True,
) -> List[dict]:
    """跑一轮:对每个会议 × 每个 doc_kind 触发子 session(2026-06-22 加并发)

    Args:
        meeting_ids: 限定会议列表(None = 所有活跃)
        dry_run: 只渲染 prompt
        parallel: True = ThreadPoolExecutor(PARALLEL_WORKERS) 并发;False = 串行
    """
    meetings = meeting_ids or list_active_meetings()
    tasks = [(mid, kind) for mid in meetings for kind in DOC_KINDS]
    print(f"[{datetime.now().isoformat()}] {len(meetings)} meetings × {len(DOC_KINDS)} doc_kinds = {len(tasks)} subs (parallel={parallel})")

    if not parallel or len(tasks) <= 1:
        # 串行
        results = [trigger_sub_session(mid, kind, dry_run=dry_run) for mid, kind in tasks]
    else:
        # 并发:每个 (meeting, kind) 一个 task,丢到线程池
        results = []
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
            future_to_task = {
                ex.submit(trigger_sub_session, mid, kind, dry_run=dry_run): (mid, kind)
                for mid, kind in tasks
            }
            for fut in as_completed(future_to_task):
                mid, kind = future_to_task[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    r = {"session_id": _agent_session_id(mid, kind), "triggered": False,
                         "error": f"{type(e).__name__}: {e}"}
                results.append(r)

    # 输出
    for r in results:
        path = r.get("agent_path", "?")
        status = "✓" if r.get("triggered") or r.get("dry_run") else "✗"
        err = f" [{r['error']}]" if r.get("error") else ""
        print(f"  {status} {r['session_id']} ({path}){err}")
    return results


def main_loop():
    """主循环:每 POLL_INTERVAL 秒跑一轮"""
    print(f"VPBuddy sub-session controller started")
    print(f"  DATA_DIR: {DATA_DIR}")
    print(f"  DOCS_DIR: {DOCS_DIR}")
    print(f"  PROMPTS_DIR: {PROMPTS_DIR}")
    print(f"  POLL_INTERVAL: {POLL_INTERVAL}s")
    print(f"  PARALLEL_WORKERS: {PARALLEL_WORKERS}")
    print(f"  DOC_KINDS: {DOC_KINDS}")
    print(f"  AIAgent in-process: {'✅ enabled' if _AGENT_AVAILABLE else '❌ disabled (subprocess fallback)'}")
    print()
    while True:
        run_one_round()
        print(f"  sleep {POLL_INTERVAL}s...\n")
        time.sleep(POLL_INTERVAL)


def main(argv: Optional[List[str]] = None) -> int:
    """Controller CLI 主入口 — `python -m vpbuddy.sub_session_controller`"""
    global PARALLEL_WORKERS
    parser = argparse.ArgumentParser(description="VPBuddy sub-session controller")
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出")
    parser.add_argument("--meeting", help="只跑指定会议 ID")
    parser.add_argument("--dry-run", action="store_true", help="只渲染 prompt,不真触发")
    parser.add_argument("--list-meetings", action="store_true", help="列出活跃会议并退出")
    parser.add_argument("--serial", action="store_true", help="强制串行(默认并发)")
    parser.add_argument("--workers", type=int, help=f"并发线程数(默认 {PARALLEL_WORKERS})")
    args = parser.parse_args(argv)

    if args.list_meetings:
        meetings = list_active_meetings()
        print(f"Active meetings ({len(meetings)}):")
        for m in meetings:
            print(f"  - {m}")
        return 0

    if args.once:
        meetings = [args.meeting] if args.meeting else None
        if args.workers:
            PARALLEL_WORKERS = args.workers
        run_one_round(
            meeting_ids=meetings,
            dry_run=args.dry_run,
            parallel=not args.serial,
        )
        return 0

    # 默认主循环(7×24)
    main_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())