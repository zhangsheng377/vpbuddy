"""sub_session_controller module (P1#1 2026-07-04)"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---- original imports ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
from typing import Any

from .storage import MeetingStorage

logger = logging.getLogger(__name__)

# === 关键 (2026-06-22 §19/§20 踩坑):KB 模型默认走 cache,不联网 ===
# conftest.py 在 pytest 进程设了这俩 env var,但跑 `python -m vpbuddy.sub_session_controller`
# 时 conftest 不加载 — KB add_document 会卡 53min(详见 docs/部署/踩坑记录.md §19)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 默认路径(可通过环境变量覆盖)
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", PROJECT_ROOT / "data" / "meetings"))
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", PROJECT_ROOT / "docs"))
PROMPTS_DIR = Path(__file__).parent / "prompts"
POLL_INTERVAL = int(os.environ.get("VPBUDDY_POLL_INTERVAL", "30"))
PARALLEL_WORKERS = int(os.environ.get("VPBUDDY_PARALLEL_WORKERS", "3"))

# 6 个子 session 对应 6 种 doc_kind
DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]

# 2026-07-01 ADR-0029: 6 kinds 合并为 2 kinds (batch_docs 5 文档 1 次 + demo 单独)
BATCH_DOCS_KIND = "batch_docs"
DEMO_KIND = "demo"
# 新调度常量 (run_one_round 用)
SCHEDULED_KINDS = [BATCH_DOCS_KIND, DEMO_KIND]

# === AIAgent 缓存(关键:跨轮询复用同一 AIAgent → 持久化 session) ===
# 2026-06-22 ADR-0009 落地:每个 (meeting_id, doc_kind) 一个 AIAgent 实例
# 同 session_id 多次触发 = 同一 session 历史 → LLM 跨次记得上下文
_AGENT_CACHE: dict[str, Any] = {}

# === KB 状态共享字典(2026-06-22) ===
# UI / controller / 日志都查这个,key = (meeting_id, doc_kind)
# value = {status: queued|stored|failed|retrying, attempts, started_at, completed_at?, doc_id?, error?}
_KB_STATUS: dict[tuple, dict[str, Any]] = {}
_KB_STATUS_LOCK = threading.Lock()  # 防止并发触发时 key 还没设就 update

# AIAgent 是否可用(2026-06-22: import 失败时 fallback 到 subprocess)
# 2026-07-03: 即使 import 成功 (run_agent 模块在 GPU server 装了), init_agent 内部
# 仍要求 LLM provider 配置 (OPENROUTER_API_KEY / HERMES_API_KEY). GPU server 部署
# 状态下 LLM provider 可能没配, 但 sub_session controller 仍要能起 (controller 不崩),
# 然后 _get_or_create_agent 返 stub, controller 调用方走 fallback 路径.
_AGENT_AVAILABLE = False
_AIAgent: type | None = None
try:
    from run_agent import AIAgent as _AIAgent  # type: ignore
    _AGENT_AVAILABLE = True
    logger.info("AIAgent in-process 模式启用 (from run_agent import AIAgent)")
except ImportError as e:
    logger.warning(
        f"AIAgent import 失败 ({e}),将 fallback 到 subprocess.run('hermes chat')"
    )


class _StubAIAgent:
    """2026-07-03: LLM provider 未配置时的 stub fallback.

    仅保留 session_id + cache 行为, 真实 chat 由调用方 fallback 到 subprocess
    或返 "no_llm_configured" 错误. 目的是让 controller 进程可启动, 不崩在 init.
    """
    def __init__(self, session_id: str, **_kwargs):
        self.session_id = session_id
        self._kwargs = _kwargs

    def chat(self, prompt, **kwargs):
        # 同步返回 (跟 _trigger_via_aiagent 期望一致). 调用方从 response
        # 看到 "no_llm_configured" 后会走自己的 fallback (subprocess / VPBUDDY_DIRECT).
        return (
            "[_StubAIAgent] no LLM provider configured. "
            "Set OPENROUTER_API_KEY / HERMES_API_KEY, or use VPBUDDY_DIRECT=1."
        )

    async def achat(self, prompt, **kwargs):
        # 真 AIAgent 有 async 接口, stub 同样提供避免调用方 hasattr 判错.
        return self.chat(prompt, **kwargs)

    def __repr__(self):
        return f"<StubAIAgent session={self.session_id}>"


def _agent_session_id(meeting_id: str, doc_kind: str) -> str:
    """构造稳定的 session_id"""
    return f"meeting:{meeting_id}:{doc_kind}"


def _get_or_create_agent(meeting_id: str, doc_kind: str) -> Any:
    """获取或创建缓存的 AIAgent 实例(2026-06-22 落地, 2026-06-23 按 ADR-0006 加 demo sandbox)

    同 (meeting_id, doc_kind) 多次调用 → 同一 AIAgent → 同一 session_id →
    Hermes 内部 session 历史累积 → LLM 跨次记得上下文

    工具权限 (2026-06-23 张胜东纠正):
    - 6 个 doc_kind 全部给 ["terminal", "file"] — 不按 doc_kind 区分 sandbox
    - demo agent 故意不禁 fetch/eval, 防止 sandbox 太严 demo 做不出来
    - 隔离在 UI 层做(将来 iframe sandbox="allow-scripts" if 需要)
    """
    sid = _agent_session_id(meeting_id, doc_kind)
    if sid not in _AGENT_CACHE:
        if _AGENT_AVAILABLE and _AIAgent is not None:
            # 2026-06-23 张胜东纠正: 不禁止 demo agent fetch/eval
            # 防止 sandbox 太严 demo 做不出来, 先看效果
            # 真出问题再在 UI 层 (iframe sandbox) 加隔离, 不在 agent 层禁
            toolsets = ["terminal", "file"]
            # ⚠️ 2026-06-23 bug 修: 之前写 ephemeral_system_prompt=(...) 多行 tuple
            # Python 自动变 tuple, AIAgent chat 时 str + tuple 报错 TypeError
            # 用 "\n".join([...]) 强制 str
            # 2026-07-04: 显式把 OPENAI_BASE_URL + OPENAI_API_KEY env 透传给 AIAgent,
            # 否则 hermes 默认走 openrouter → MiniMax-M3 也被路由过去但 openrouter
            # 不识别我们的 MiniMax API key → HTTP 401. 现在直连 MiniMax endpoint.
            try:
                _AGENT_CACHE[sid] = _AIAgent(
                    session_id=sid,
                    enabled_toolsets=toolsets,
                    platform="subagent",
                    quiet_mode=True,
                    max_iterations=30,
                    model=os.environ.get("VPBUDDY_LLM_MODEL", "MiniMax-M3"),
                    base_url=os.environ.get("OPENAI_BASE_URL"),  # 直连 MiniMax endpoint
                    api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY"),
                    ephemeral_system_prompt="\n".join([
                        f"你是 VPBuddy 的 {doc_kind} 子 session。",
                f"session_id 固定 = {sid}。",
                f"当前 meeting_id = {meeting_id} (用于 KB 检索)。",
                f"输出文件路径(必须写到这里):{get_doc_path(meeting_id, doc_kind)}",
                "",
                "【硬性要求 — 不遵守 = 任务失败】",
                "1. 你**必须**调用 file toolset 里的 write_file 工具,把完整文档内容写入到上面的输出文件路径。",
                "2. 不要只在文字响应里输出文档 — 文字响应不算完成任务。",
                "3. 先调用 read_file 读取当前 state JSON 和(若存在)旧文档,再决定如何更新。",
                "4. 如果 state 与旧文档完全一致,仍必须写一个空变更说明文件(标记 '无变化')。",
                "",
                "【工具调用示例 — 必须严格按这个模式】",
                "```",
                "1. 调 read_file 工具,路径 = state JSON 路径",
                "2. 解析 state.facts.{doc_kind} 等字段",
                "3. 调 read_file 工具,路径 = 旧 doc 路径(可能不存在)",
                "4. 生成新文档内容(基于 state + 旧 doc)",
                "5. 调 write_file 工具,路径 = 上面给的输出文件路径,内容 = 完整文档",
                "6. 退出(不要再调其他工具)",
                "```",
                "",
                "【反例 — 这是错的】",
                "❌ 只在文字响应里输出整个文档,没调 write_file → 任务失败",
                "❌ 调 write_file 但路径错了 → 任务失败",
                "❌ 调 write_file 但内容空 → 任务失败",
                "",
                "【可选工具 (按需用, 别为基本生成步骤无谓调)】",
                "# 网络搜索 (DDG 无 API key, 返回 top 5/20 条)",
                "python -c \"from vpbuddy.tools.web_search import search; import json; print(json.dumps(search('Q4 行业报告', max_results=5), ensure_ascii=False))\"",
                "",
                "# KB 检索 (meeting_id 已自动注入, 强制会议隔离)",
                f"python -c \"from vpbuddy.tools.kb_search import search; import json; print(json.dumps(search('{meeting_id}', '客户合同要点', top_k=5), ensure_ascii=False))\"",
                "",
                "返回 JSON. ok=False 时 fallback 到训练知识, 别重试.",
                "",
                "工作流:",
                "  read_file(state) → 解析 facts → (可选) 工具调用 → 生成文档内容 → write_file(目标路径, 完整内容) → 退出",
                    ]),
                )
                logger.info(f"创建新 AIAgent: session_id={sid}")
            except Exception as e:
                # 2026-07-03: GPU server 上 run_agent 装了但 init_agent 内部要 LLM provider
                # 配置, 没配就 RuntimeError. fallback 到 StubAIAgent 让 controller 不崩.
                logger.warning(
                    f"AIAgent 初始化失败 ({type(e).__name__}: {e}), "
                    f"fallback 到 _StubAIAgent (LLM 未配)"
                )
                _AGENT_CACHE[sid] = _StubAIAgent(session_id=sid)
        else:
            # run_agent 模块没装 (import 失败), 直接用 stub
            logger.warning(f"AIAgent 模块不可用, 用 _StubAIAgent fallback (session={sid})")
            _AGENT_CACHE[sid] = _StubAIAgent(session_id=sid)
    return _AGENT_CACHE[sid]


def _dispatch_kind(meeting_id: str, kind: str, dry_run: bool = False) -> dict[str, Any]:
    """按 kind 路由到对应 trigger 函数 (ADR-0029 Commit 3).

    Routing:
        batch_docs → sub_sessions.batch_docs.trigger_batch_docs
        demo       → 老 trigger_sub_session(mid, "demo") (保留原路径)
        req/arch/tasks/api/risk → 返 deprecated 警告 (引导用 batch_docs)
    """
    if kind == BATCH_DOCS_KIND:
        from .sub_sessions.batch_docs import trigger_batch_docs
        return trigger_batch_docs(meeting_id, dry_run=dry_run)
    if kind == DEMO_KIND:
        # demo 走老路径 (已有 demo_version.write_demo_version + write_doc)
        return trigger_sub_session(meeting_id, "demo", dry_run=dry_run)
    # 老 kinds (req/arch/tasks/api/risk) — deprecated 引导
    return {
        "session_id": _agent_session_id(meeting_id, kind),
        "triggered": False,
        "deprecated": True,
        "error": (
            f"doc_kind '{kind}' deprecated since v0.7 (ADR-0029). "
            f"5 文档已合并为 batch_docs agent. "
            f"Use _dispatch_kind(mid, 'batch_docs') instead."
        ),
    }


def list_active_meetings() -> list[str]:
    """列出活跃会议(有 MeetingState JSON 文件就算活跃)"""
    if not DATA_DIR.exists():
        return []
    return [p.stem for p in DATA_DIR.glob("*.json")]


def cleanup_inactive_agents(inactive_minutes: int = 30, dry_run: bool = False) -> dict[str, Any]:
    """清理长期不活跃会议的 AIAgent 缓存 (2026-06-24 张胜东要求,防内存泄漏)

    判据: meeting_state JSON 文件 mtime > inactive_minutes 分钟前的会议视为已结束
    行为: 从 _AGENT_CACHE 弹出对应的 6 个 session_id (req/arch/tasks/api/risk/demo)

    ⚠️ 设计权衡:
    - MeetingState 没有 status 字段 (state.py), 无法显式标记"会议结束"
    - 用 JSON 文件 mtime 做判据 — VPBuddy 每次 save() 都更新文件
    - 默认 30 分钟 — 短于 30 分钟的会议静默期不该清 (长会议/茶歇场景)
    - dry_run=True 时只统计不真删 (默认 False)

    Returns:
        {"cleaned": [mid, ...], "kept_active": [mid, ...], "skipped_no_state": [mid, ...]}
    """
    import time as _time

    now = _time.time()
    threshold_sec = inactive_minutes * 60

    cleaned: list[str] = []
    kept: list[str] = []
    skipped: list[str] = []

    # 取所有已 cache 的 meeting_id
    cached_mids = set()
    for sid in _AGENT_CACHE.keys():
        # sid 格式: meeting:{mid}:{kind}
        parts = sid.split(":", 2)
        if len(parts) == 3 and parts[0] == "meeting":
            cached_mids.add(parts[1])

    for mid in cached_mids:
        state_path = DATA_DIR / f"{mid}.json"
        # state JSON 已被删 (人工/V 主动删) → 该会议的 agent 也清
        if not state_path.exists():
            if not dry_run:
                for kind in DOC_KINDS:
                    sid = _agent_session_id(mid, kind)
                    _AGENT_CACHE.pop(sid, None)
            cleaned.append(mid)
            if dry_run:
                skipped.append(f"{mid} (no_state)")
            continue

        # mtime 判断
        mtime = state_path.stat().st_mtime
        if now - mtime > threshold_sec:
            if not dry_run:
                for kind in DOC_KINDS:
                    sid = _agent_session_id(mid, kind)
                    _AGENT_CACHE.pop(sid, None)
                logger.info(
                    f"清理 inactive AIAgent: meeting_id={mid} "
                    f"(inactive {(now - mtime) / 60:.1f}min)"
                )
            cleaned.append(mid)
        else:
            kept.append(mid)

    return {
        "cleaned": cleaned,
        "kept_active": kept,
        "skipped_no_state": skipped,
        "inactive_threshold_minutes": inactive_minutes,
        "cache_size_before": len(cached_mids),
        "cache_size_after": len(_AGENT_CACHE),
    }


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
        parts.append("## 说话人映射")
        for sid, name in state.speaker_map.items():
            parts.append(f"- {sid} → {name}")

    return "\n".join(parts)


def render_prompt(doc_kind: str, meeting_id: str, state_summary: str, last_doc: str | None) -> str:
    """渲染子 session 的 prompt(优先用专属模板,fallback 通用模板)

    ⚠️ 2026-06-23 bug 修: prompt 模板里如果出现 `{` 或 `}`(比如 CSS 代码块
    `<style>body { font-family: ... }</style>`), .format() 会抛 KeyError.
    用 escape_braces 把 { → {{, } → }} (除了我们真要替换的 4 个变量).
    """
    template_path = PROMPTS_DIR / f"{doc_kind}.md"
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else _generic_template()

    doc_path = get_doc_path(meeting_id, doc_kind)
    # P1#4 (2026-07-04): string.Template 替代 .format(), 避免文档内容含 { } 抛 KeyError
    from string import Template as _StringTemplate
    for key in ["meeting_id", "doc_kind", "state_summary", "last_doc", "doc_path"]:
        template = template.replace("{" + key + "}", "${" + key + "}")
    return _StringTemplate(template).safe_substitute(
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


def _trigger_via_subprocess(prompt: str, meeting_id: str, doc_kind: str, timeout: int = 300) -> dict:
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


def _trigger_via_aiagent(prompt: str, meeting_id: str, doc_kind: str, timeout: int = 180) -> dict:
    """主路径:in-process AIAgent(2026-06-22 落地,真 session 持久化)

    同 (meeting_id, doc_kind) 多次调用复用同一 AIAgent → 跨轮询 LLM 记得上次输出

    注意:不用 signal.alarm(会和 hermes_bootstrap 的信号冲突),用线程 daemon 监控。
    """
    t_start = time.time()
    logger.info(f"[{meeting_id}/{doc_kind}] _trigger_via_aiagent start, prompt_len={len(prompt)}")

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError

    def _chat_fn():
        agent = _get_or_create_agent(meeting_id, doc_kind)
        return agent.chat(prompt)

    with ThreadPoolExecutor(max_workers=1) as _executor:
        _future = _executor.submit(_chat_fn)
        try:
            response = _future.result(timeout=timeout)
            logger.info(f"[{meeting_id}/{doc_kind}] done in {time.time()-t_start:.1f}s")
            # StubAIAgent check
            if isinstance(response, str) and response.startswith("[_StubAIAgent]"):
                return {
                    "triggered": False,
                    "session_id": _agent_session_id(meeting_id, doc_kind),
                    "error": response,
                    "agent_path": "in-process-stub",
                }
            return {
                "triggered": True,
                "session_id": _agent_session_id(meeting_id, doc_kind),
                "agent_response": (response or "")[-500:] if response else "",
                "agent_path": "in-process",
                "error": None,
            }
        except _FutureTimeoutError:
            logger.warning(f"[{meeting_id}/{doc_kind}] AIAgent timeout after {timeout}s")
            return {
                "triggered": False,
                "session_id": _agent_session_id(meeting_id, doc_kind),
                "error": f"AIAgent timeout ({timeout}s)",
                "agent_path": "in-process",
            }
        except Exception as e:
            logger.error(f"[{meeting_id}/{doc_kind}] AIAgent error: {type(e).__name__}: {e}")
            return {
                "triggered": False,
                "session_id": _agent_session_id(meeting_id, doc_kind),
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "agent_path": "in-process",
            }
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
    # 2026-07-03: StubAIAgent 返回 "[_StubAIAgent] no LLM provider configured"
    # 这是 fallback 标记, 不是真触发成功. 返 triggered=False 让调用方走 subprocess 路径.
    if isinstance(response, str) and response.startswith("[_StubAIAgent]"):
        return {
            "triggered": False,
            "session_id": _agent_session_id(meeting_id, doc_kind),
            "error": response,
            "agent_path": "in-process-stub",
        }
    return {
        "triggered": True,
        "session_id": _agent_session_id(meeting_id, doc_kind),
        "agent_response": (response or "")[-500:] if response else "",
        "agent_path": "in-process",
        "error": None,
    }


def trigger_sub_session(meeting_id: str, doc_kind: str, dry_run: bool = False) -> dict[str, Any]:
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
    result: dict[str, Any] = {"session_id": sid, "triggered": False, "error": None}

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
        content = doc_path.read_text(encoding="utf-8", errors="replace")

        # 2026-07-01 ADR-0024: demo 走版本化 — 写新版本, 不覆盖老的
        if doc_kind == "demo":
            try:
                from .demo_version import write_demo_version
                from .realtime_server import push_event as _push
                v_result = write_demo_version(meeting_id, content, trigger="agent_iterate")
                if v_result.get("ok"):
                    # 推 demo-new-version SSE (新版生成通知)
                    try:
                        _push(meeting_id, "demo-new-version", {
                            "version": v_result["version"],
                            "summary": v_result["summary"],
                            "file_size": v_result["file_size"],
                            "file": v_result["file"],
                        })
                    except Exception as e:
                        logger.warning(f"[{mid}/{doc_kind}] push SSE demo-version failed: {e}")
                    result["demo_version"] = v_result["version"]
                    result["demo_versions_count"] = len(v_result["manifest"])
                else:
                    logger.warning(f"[{meeting_id}/demo] 写新版本失败: {v_result.get('error')}")
            except Exception as e:
                logger.warning(f"[{meeting_id}/demo] demo_version integration failed: {e}")

        # 推送 SSE: 文档生成完成
        try:
            from .realtime_server import push_event
            push_event(meeting_id, "doc-update", {
                "kind": doc_kind,
                "status": "stored",
                "doc_size": result["doc_size"],
                "meeting_id": meeting_id,
                "content": content,
                "updated_at": datetime.now().isoformat(),
                "is_demo": doc_kind == "demo",
            })
        except Exception as e:
            logger.warning(f"[{meeting_id}/{doc_kind}] push SSE doc-update failed: {e}")

    # 7. ADR-0020: 废弃 6 docs 自动入 KB. 文档写完只推 SSE + 检查全文档完成.
    # 旧 KB 逻辑 (_kb_bg 自动 ingest) 已删除. 用户主动上传走 /api/kb/upload.
    if result.get("triggered") and doc_path.exists():
        # 2026-07-01: ADR-0022 — 6 docs 全 stored 推 docs-complete, **不** 关会议
        # 会议结束 = 切会议 / 关客户端 / 用户手动 [结束会议] (POST /api/meetings/{id}/close)
        try:
            from .ui_server_helpers import check_all_docs_stored_notify
            check_all_docs_stored_notify(meeting_id)
        except Exception as e:
            logger.warning(f"[{meeting_id}/{doc_kind}] check_all_docs_stored_notify failed: {e}")

    return result


def get_kb_status(meeting_id: str | None = None) -> dict[str, Any]:
    """ADR-0020: KB 自动 ingest 已废弃, 返回空. 保留 stub 兼容 cli.py."""
    return {"summary": {"total": 0, "stored": 0, "failed": 0, "queued": 0, "retrying": 0}, "items": []}


def run_one_round(
    meeting_ids: list[str] | None = None,
    dry_run: bool = False,
    parallel: bool = True,
) -> list[dict]:
    """跑一轮:对每个会议触发 batch_docs + demo (2 个 sub-session 并行).

    2026-07-01 ADR-0029: 6 kinds (req/arch/tasks/api/risk/demo) 合并为 2 kinds
    (batch_docs + demo). LLM 调用从 6 → 2, 时间从 3-5 min → 1-2 min.

    Args:
        meeting_ids: 限定会议列表(None = 所有活跃)
        dry_run: 只渲染 prompt (不调 LLM)
        parallel: True = ThreadPoolExecutor(PARALLEL_WORKERS) 并发;False = 串行
    """
    meetings = meeting_ids or list_active_meetings()
    # 新调度: 每个会议 2 个 task (batch_docs + demo)
    kinds = [BATCH_DOCS_KIND, DEMO_KIND]
    tasks = [(mid, kind) for mid in meetings for kind in kinds]
    print(f"[{datetime.now().isoformat()}] {len(meetings)} meetings × {len(kinds)} kinds = {len(tasks)} subs (parallel={parallel}, ADR-0029 batch)")

    if not parallel or len(tasks) <= 1:
        # 串行
        results = [_dispatch_kind(mid, kind, dry_run=dry_run) for mid, kind in tasks]
    else:
        # 并发:每个 (meeting, kind) 一个 task,丢到线程池
        results = []
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
            future_to_task = {
                ex.submit(_dispatch_kind, mid, kind, dry_run=dry_run): (mid, kind)
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


def main_loop() -> None:
    """主循环:每 POLL_INTERVAL 秒跑一轮,每小时清理一次 inactive agents"""
    print("VPBuddy sub-session controller started")
    print(f"  DATA_DIR: {DATA_DIR}")
    print(f"  DOCS_DIR: {DOCS_DIR}")
    print(f"  POLL_INTERVAL: {POLL_INTERVAL}s")
    print(f"  PARALLEL_WORKERS: {PARALLEL_WORKERS}")
    print(f"  DOC_KINDS: {DOC_KINDS}")
    print(f"  AIAgent in-process: {'✅ enabled' if _AGENT_AVAILABLE else '❌ disabled (subprocess fallback)'}")
    print(f"  auto-cleanup: 每 {int(3600 / POLL_INTERVAL)} 轮 ≈ 1 小时")

    # 2026-07-01 ADR-0023 Phase 5: 启动 agent 主动消息后台监控 (silence / time_node)
    try:
        from .agent_proactive import start_monitor
        start_monitor()
        print(f"  proactive monitor: ✅ enabled (silence {int(os.environ.get('VPBUDDY_PROACTIVE_INTERVAL', '60'))}s 轮询)")
    except Exception as e:
        print(f"  proactive monitor: ❌ failed: {e}")

    print()
    cleanup_counter = 0
    CLEANUP_EVERY: int = max(1, int(3600 / POLL_INTERVAL))  # noqa: N806 (period seconds)
    while True:
        run_one_round()
        cleanup_counter += 1
        if cleanup_counter >= CLEANUP_EVERY:
            cleanup_counter = 0
            try:
                result = cleanup_inactive_agents(inactive_minutes=30, dry_run=False)
                if result["cleaned"]:
                    print(
                        f"  [auto-cleanup] cleaned {len(result['cleaned'])} inactive meetings, "
                        f"cache {result['cache_size_before']} → {result['cache_size_after']}"
                    )
            except Exception as e:
                logger.warning(f"auto-cleanup failed: {e}")
        print(f"  sleep {POLL_INTERVAL}s...\n")
        time.sleep(POLL_INTERVAL)


def main(argv: list[str] | None = None) -> int:
    """Controller CLI 主入口 — `python -m vpbuddy.sub_session_controller`"""
    global PARALLEL_WORKERS
    parser = argparse.ArgumentParser(description="VPBuddy sub-session controller")
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出")
    parser.add_argument("--meeting", help="只跑指定会议 ID")
    parser.add_argument("--dry-run", action="store_true", help="只渲染 prompt,不真触发")
    parser.add_argument("--list-meetings", action="store_true", help="列出活跃会议并退出")
    parser.add_argument("--serial", action="store_true", help="强制串行(默认并发)")
    parser.add_argument("--workers", type=int, help=f"并发线程数(默认 {PARALLEL_WORKERS})")
    parser.add_argument(
        "--cleanup-agents",
        type=int,
        metavar="MINUTES",
        help="清理 inactive_minutes 分钟没更新的会议对应 AIAgent 缓存(0=用默认 30)",
    )
    args = parser.parse_args(argv)

    if args.list_meetings:
        meetings = list_active_meetings()
        print(f"Active meetings ({len(meetings)}):")
        for m in meetings:
            print(f"  - {m}")
        return 0

    if args.cleanup_agents is not None:
        threshold = args.cleanup_agents if args.cleanup_agents > 0 else 30
        result = cleanup_inactive_agents(inactive_minutes=threshold, dry_run=False)
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
