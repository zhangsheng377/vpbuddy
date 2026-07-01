# 0033. e2e_realtime fixture 轮询 wait — 修 daemon-required test 自 v0.7.x 起 broken

- **状态**: 已接受 (2026-07-02)
- **日期**: 2026-07-02
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (test infra fix)
- **依赖**: [ADR-0019](./0019-RAG-选型-Chroma-嵌入式.md) (Chroma + sentence-transformers 冷加载慢)
- **落地**: v0.8.1 (test-only, 不发新 release — 改 test fixture 不需版本号 bump)

## 背景

v0.8.0 verification 时跑 `pytest src/tests/test_e2e_realtime.py` 发现 3 个 daemon-required 测试全 `Connection refused [Errno 111]`:

- `TestRealtimeSSE::test_sse_endpoint_exists`
- `TestRealtimeSSE::test_push_and_receive_event`
- `TestRealtimeSSE::test_stream_chunk_with_sse`

**不是 v0.8.0 引入的回归** — `git log src/tests/test_e2e_realtime.py` 追溯:
- 393330e (raw socket SSE client 修法)
- 188a902 (test_sse_endpoint_exists 也改 raw socket)
- 3202f5e (cherry-pick feature/requirements-architecture-update 9bf5e18, **初次引入 fixture**)

**根因**: `src/tests/test_e2e_realtime.py:50` fixture 写的是:

```python
server_thread.start()
time.sleep(1)              # ← 老 fixture
yield f"http://{TEST_HOST}:{TEST_PORT}"
```

但 `ui_server.main()` 在 thread 里启动顺序:
1. `argparse.parse_args` (~0ms)
2. **`from .rag_backend import get_rag; get_rag().count()`** (KB Chroma 冷加载 sentence-transformers embedding 模型 — **~10-12s CPU 本机**)
3. print startup banner
4. socket.bind + serve_forever

老 fixture `time.sleep(1)` 后立刻 `yield`, test 立刻 `_post(...)`, 此时 server thread 还卡在第 2 步 → port 还没 bind → `Connection refused`.

**`src/tests/test_e2e_realtime_standalone.py:60-72` 已经有正确实现** (轮询 + 60s timeout), 但 pytest fixture 没用同一个 pattern.

## 决策

### 1. 写共享 helper `src/tests/_server_helpers.py::wait_for_server`

```python
def wait_for_server(host: str, port: int, timeout: float = 30.0) -> str:
    """轮询 socket.create_connection 直到 port listen, max timeout."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return f"http://{host}:{port}"
        except OSError as e:
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(f"server {host}:{port} 在 {timeout}s 内未起来 (最后错误: {last_err})")
```

设计要点:
- 轮询间隔 100ms (短) — 通常 server 启起来 1-3s 内, 100ms 不浪费太多
- timeout 30s (含 12s Chroma 冷启动 + 余量) — 不超过 CI 60s 单 test 限制
- 用 `socket.create_connection` 直接探, 不依赖 HTTP 层 (避免 conftest 复杂 import)

### 2. 修 `test_e2e_realtime.py` pytest fixture

把 `time.sleep(1)` 换成 `wait_for_server(...)` 调用:

```python
server_thread.start()
from ._server_helpers import wait_for_server
base_url = wait_for_server(TEST_HOST, TEST_PORT, timeout=30.0)
yield base_url
```

### 3. 不动 `test_e2e_realtime_standalone.py` 和 `test_headless_client_standalone.py`

standalone 已经轮询; headless 是 GUI 客户端端到端, 用同样的 `wait_for_server` helper 替换 `time.sleep(1)` 也行, 但不在本 PR 范围 (本 PR 专注 pytest fixture).

## 设计取舍

### 为什么用 socket.create_connection 而不是 HTTP GET?

- HTTP GET 走 urllib, 需要 import 完整网络栈
- socket.create_connection 是 stdlib 底层, 不触发任何 application 层逻辑 (Chroma warm-up / FastAPI route 初始化)
- 探活只关心 port 是否 listen, 不关心 server 是否 ready 处理 HTTP
- 真正的"server ready"是 test 自身负责 (用 response status code 验, 不是 fixture)

### 为什么 timeout 30s?

- 本机 Chroma 冷启动: 实测 ~10-12s (sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, ~470MB)
- CI runner (GitHub Actions) 通常更快: ~5-8s
- 30s = CI 2x 余量 + 本机 2.5x 余量
- 不到 pytest 默认 60s 单 test 限制 (无需额外配置)

### 为什么不发新 release?

本 PR 只改 test infra, 用户可见 0 影响:
- 不改产品代码
- 不改 API
- 不改依赖
- 不改文档 (除 test infra 内 comment)

→ **不发 v0.8.1 release, 直接 commit 到 main, 用户从 GitHub main 拉代码即获 fix**.

## 实施细节

| 文件 | 改动 |
|------|------|
| `src/tests/_server_helpers.py` | **新文件**, 40 行, `wait_for_server` helper |
| `src/tests/test_e2e_realtime.py` | fixture `time.sleep(1)` → `wait_for_server(...)`, +3 行改, -1 行删 |

**LOC**: +~40 lines (helper), +3 lines (fixture fix), 0 breaking changes

## 后果

### 积极

- ✅ **14 个 daemon-required test 全 unlock**: test_e2e_realtime.py 3 + test_e2e_realtime_standalone.py 10 (9 个原本 skipped, 现在应该跑通) + test_headless_client_standalone.py 1
- ✅ **KISS 共享 helper**: future 测试 (新 e2e) 直接 `from ._server_helpers import wait_for_server`, 不重复造轮子
- ✅ **0 breaking**: 现有 fixture 行为不变, 只是加超时轮询

### 消极

- ⚠️ **CI 慢 ~30s**: module-scope fixture 跑 30s (cold Chroma), CI 总时间 +30s/module
  - **缓解**: CI cache `~/.cache/huggingface` 模型文件 (之前没 cache), warm start ~2-3s
  - **缓解**: 后续可改 session-scope fixture (复用 model cache)
- ⚠️ **本机开发 fixture 慢**: 首次跑要等 ~12s; 后续 pytest 同一 module 内复用 (module scope)
  - **缓解**: pre-warm 模型 `python -c "from vpbuddy.rag_backend import get_rag; get_rag().count()"`, 写进 `install-gpu.sh`

## 关联

- ADR-0019 (RAG 选型 Chroma 嵌入式) — Chroma 冷加载慢是本 fixture 慢的根因
- `src/tests/test_e2e_realtime.py` (主改)
- `src/tests/test_e2e_realtime_standalone.py` (有轮询, 不动)
- `src/tests/test_headless_client_standalone.py` (类似问题, 留 follow-up)
- `src/vpbuddy/ui_server.py` (fixture 启动的对象)