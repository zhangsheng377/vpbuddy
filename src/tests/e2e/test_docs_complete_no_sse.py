"""e2e — check_all_docs_stored_notify 不推 SSE (2026-07-02 删死代码后行为).

跑法: RUN_E2E=1 pytest src/tests/e2e/test_docs_complete_no_sse.py -v -m e2e

测什么:
- 真在 GPU server 端写满 6 个 doc 文件 (req/arch/tasks/api/risk + demo)
- 通过 e2e-only HTTP 端点 POST /api/_e2e/check_docs_complete?mid=XXX 触发真 server 进程的
  check_all_docs_stored_notify (env guard VPBUDDY_E2E=1, 端点默认 404)
- 真订阅 SSE 流 /api/meetings/{mid}/events, 收集 N 秒所有事件
- 核心断言: 收集的事件列表里**不**含 "docs-complete" 事件 (新行为)
- 辅助断言: check_all_docs_stored_notify 返 True (6 doc 全 stored 事实成立)
- 辅助断言: check 后 close_meeting 没被调 (ADR-0022), 会议 state 仍可读

为什么需要这个测试:
- 之前的 30 个 e2e 没覆盖 check_all_docs_stored_notify 路径 (e2e 不跑 batch_docs agent,
  没法自然触发 6 doc 写完)
- 单元测试 test_docs_complete_not_close.py 是 mock push_event, 验不出真 GPU 进程行为
- 这是**唯一**真覆盖 GPU server 进程跑 06ab0e1 代码后 docs-complete 行为 = 静默的 e2e
- 之前盲点: 用户 2026-07-02 指出 GPU 进程是 v0.8.3, 我才意识到 e2e 没真触发这条代码路径
  (e2e 只验 HTTP API 层, 不验 SSE push 链路)

不测什么:
- agent_proactive.trigger("docs_complete") chat 通道 (单元测覆盖)
- 6 doc 实际写盘流程 (LLM 强相关, 留 batch_docs unit 测)

前置条件:
- GPU server 跑 06ab0e1 或更新代码 (用 VPBUDDY_E2E=1 启动暴露 _e2e 端点)
"""
from __future__ import annotations

import json
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

import pytest


pytestmark = pytest.mark.e2e


# === Helpers ===

GPU_HOST = "zsd@192.168.10.63"
GPU_DOCS_DIR = "/home/zsd/vpbuddy/docs"
DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]


def _ssh_run(cmd: str, timeout: int = 10) -> str:
    """在 GPU 端跑命令, 返 stdout."""
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", GPU_HOST, cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout + ("\n[STDERR] " + result.stderr if result.stderr else "")


def _write_doc_fixture(meeting_id: str) -> None:
    """SSH 写满 6 个 doc 到 GPU server 的 DOCS_DIR/{mid}/.

    路径布局跟 ui_server._doc_path(meeting_id, kind) 严格对齐:
    - req/arch/tasks/api/risk → {DOCS_DIR}/{mid}/{kind}.md
    - demo → {DOCS_DIR}/{mid}/demo/demo.html
    """
    base = f"{GPU_DOCS_DIR}/{meeting_id}"
    _ssh_run(f"mkdir -p {shlex.quote(base)}/demo", timeout=5)
    for kind in ["req", "arch", "tasks", "api", "risk"]:
        body = f"# {kind}\nfixture content for {meeting_id}"
        _ssh_run(
            f"cat > {shlex.quote(base)}/{kind}.md <<'DOCEOF'\n{body}\nDOCEOF",
            timeout=5,
        )
    _ssh_run(
        f"cat > {shlex.quote(base)}/demo/demo.html <<'HTMLEOF'\n<h1>demo {meeting_id}</h1>\nHTMLEOF",
        timeout=5,
    )


def _remove_doc_fixture(meeting_id: str) -> None:
    """清理: SSH 删 docs/{mid}/ 目录."""
    _ssh_run(f"rm -rf {shlex.quote(GPU_DOCS_DIR + '/' + meeting_id)}", timeout=5)


def _post(url: str, data: dict | None = None, timeout: int = 10) -> tuple[int, dict]:
    """HTTP POST JSON, 返 (status_code, body_dict)."""
    body = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}


def _get(url: str, timeout: int = 5) -> tuple[int, dict | str]:
    """HTTP GET, 返 (status_code, body)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, ""


def _trigger_check_via_http(gpu_url: str, meeting_id: str) -> tuple[int, dict]:
    """通过 e2e 端点 (env-guarded) 调真 GPU server 进程的 check_all_docs_stored_notify."""
    url = f"{gpu_url}/api/_e2e/check_docs_complete?mid={urllib.parse.quote(meeting_id)}"
    return _post(url)


def _sse_collect(gpu_url: str, meeting_id: str, duration_sec: float) -> list[str]:
    """订阅 SSE /api/meetings/{mid}/events, 收 duration_sec 秒, 返所有 event type 列表.

    走纯 stdlib (urllib). SSE 协议 text/event-stream, 每行格式:
        event: <type>\\n
        data: <json>\\n\\n
    只关心 event type.
    """
    url = f"{gpu_url}/api/meetings/{meeting_id}/events"
    event_types: list[str] = []
    stop_at = time.monotonic() + duration_sec

    def _reader() -> None:
        try:
            req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=duration_sec + 2) as resp:
                for raw in resp:
                    if time.monotonic() >= stop_at:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("event: "):
                        event_types.append(line[len("event: "):])
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=duration_sec + 3)
    return event_types


@pytest.fixture(scope="module")
def e2e_endpoint_available(gpu_server: str) -> str:
    """前置检查: GPU server 必须 VPBUDDY_E2E=1 启, /api/_e2e/check_docs_complete 存在."""
    url = f"{gpu_server}/api/_e2e/check_docs_complete?mid=__probe__"
    code, _ = _post(url)
    if code == 404:
        pytest.skip(
            "GPU server 端点 /api/_e2e/check_docs_complete 不可用, "
            "需 VPBUDDY_E2E=1 启动 server 才暴露. "
            "(生产 deploy 不设这个 env, 本测试默认 skip)"
        )
    return gpu_server


@pytest.fixture
def docs_complete_meeting() -> Iterator[str]:
    """准备: 写 6 doc fixture, 收尾删."""
    mid = "E2E_DOCS_COMPLETE_TEST"
    _write_doc_fixture(mid)
    try:
        yield mid
    finally:
        _remove_doc_fixture(mid)


# === Tests ===

def test_e2e_endpoint_requires_env_guard(gpu_server: str) -> None:
    """端点必须在 VPBUDDY_E2E=1 才暴露 (生产部署 404)."""
    url = f"{gpu_server}/api/_e2e/check_docs_complete?mid=__probe__"
    code, _ = _post(url)
    # code 200 (e2e 启了) 或 404 (生产) 都接受, 但**不能** 500 或其他
    assert code in (200, 404), f"端点应 200 (e2e) 或 404 (prod), 实际 {code}"


def test_check_returns_true_when_all_6_docs_stored(
    docs_complete_meeting: str, e2e_endpoint_available: str
) -> None:
    """6 doc 写满 → check_all_docs_stored_notify 应返 True (真 server 进程行为)."""
    code, body = _trigger_check_via_http(e2e_endpoint_available, docs_complete_meeting)
    assert code == 200
    assert body["all_stored"] is True
    assert body["meeting_id"] == docs_complete_meeting


def test_check_does_not_push_docs_complete_event(
    docs_complete_meeting: str, e2e_endpoint_available: str
) -> None:
    """核心断言 (2026-07-02 删死代码后): check 不推 SSE "docs-complete".

    真在 GPU server 进程跑 check + 真订阅 SSE 验证. 验的是:
    - 生产 server 跑 06ab0e1 代码
    - check_all_docs_stored_notify 静默返 True, **不** push_event
    - SSE 流里**不**出现 "docs-complete" event
    """
    mid = docs_complete_meeting
    gpu_url = e2e_endpoint_available

    # 1. 启动 SSE listener (后台线程, 2.5s 后停止)
    collected: list[str] = []
    sse_done = threading.Event()

    def _sse_reader() -> None:
        nonlocal collected
        collected = _sse_collect(gpu_url, mid, duration_sec=2.5)
        sse_done.set()

    reader = threading.Thread(target=_sse_reader, daemon=True)
    reader.start()

    # 2. 等 SSE 连上
    time.sleep(0.3)

    # 3. 通过 e2e 端点触发真 GPU server 进程跑 check
    code, body = _trigger_check_via_http(gpu_url, mid)
    assert code == 200
    assert body["all_stored"] is True

    # 4. 等 SSE 收完
    sse_done.wait(timeout=4.0)

    # 5. 核心断言
    assert "docs-complete" not in collected, (
        f"应不推 docs-complete (2026-07-02 删死代码), 实际 SSE events: {collected}"
    )

    # 6. 辅助断言: check 也没推 doc-update (那是 batch_docs.run 推的, 不是 check 推的)
    assert "doc-update" not in collected, (
        f"check_all_docs_stored_notify 不应推 doc-update (那是 batch_docs.run 推的), "
        f"实际: {collected}"
    )


def test_check_does_not_close_meeting(
    docs_complete_meeting: str, e2e_endpoint_available: str
) -> None:
    """ADR-0022 核心: 6 doc 完成不调 close_meeting. 验真 GPU server 进程行为.

    跟 docs-complete 独立 — 这是 ADR-0022 立的, 没在这次改的清理范围, 但 e2e 一起验
    (免得回归).
    """
    mid = docs_complete_meeting
    gpu_url = e2e_endpoint_available

    # 准备: 创建 meeting state (这样 state 端点才能 200)
    # 用 e2e 端点 + check 流程, 不需要预先 state — check 只看 doc 文件, 不依赖 state 存在
    # 但 close_meeting 检查 state 存在, 所以我们用现有会议来验
    # 简单做法: 列出现有会议, 拿一个 meeting_id 写 6 doc 进去触发, 但这会污染数据
    # 折中: 仅验 "check 后 close_meeting 没被调" — 通过 state 端点读不到就 skip

    # check 前: 通过 _handle_meeting_docs 端点验 doc 在 (200)
    docs_url = f"{gpu_url}/api/meetings/{urllib.parse.quote(mid)}/docs"
    code, _ = _get(docs_url)
    if code == 404:
        pytest.skip(f"会议 {mid} 不在 server docs 列表, 无法验不 close, skip")

    # 触发
    code, body = _trigger_check_via_http(gpu_url, mid)
    assert code == 200
    assert body["all_stored"] is True

    # check 后: docs 端点**仍**200 (没被 close 删掉)
    code_after, _ = _get(docs_url)
    assert code_after == 200, (
        f"ADR-0022: 6 doc 完成不应触发 close_meeting, 但 docs 端点返 {code_after}"
    )
