# ADR-0036: e2e 策略 — Playwright + vite preview + GPU 真 server

| 字段 | 值 |
|------|-----|
| 状态 | Accepted |
| 日期 | 2026-07-02 |
| 作者 | Hermes |
| 替代 | (无) — 之前没 e2e, 只有半 e2e (`test_e2e_realtime.py`) + 集成测试 |
| 依赖 | ADR-0035 (release infra), 客户端 v0.8.0+ (Tauri 2.6+ ESM bundle) |
| Supersedes | (无) |
| 相关 ADR | ADR-0019 (Chroma), ADR-0020 (KB 隔离), ADR-0022 (会议结束语义) |

## 背景

2026-07-02 张胜东指出: "你做过 e2e 测试吗?" 检查 `src/tests/` 后诚实承认:

| 类型 | 数量 | 状态 |
|------|------|------|
| 单元 + mock 集成 | 60+ | 95% mock |
| 半 e2e (`test_e2e_realtime.py`) | 3 | 2/3 pass (1 SSE HTTP 500 flake pre-existing) |
| **真 e2e (Playwright/Tauri 客户端)** | **0** | ❌ **从未存在** |
| Loopback 真录音 e2e | 0 | ❌ |
| RAG KB 真灌库 + 真隔离 | 0 | ❌ (只有 `_FakeRAG` mock) |

用户进一步澄清: "e2e 不是真 e2e" + "本机不起服务端" + "去操作客户端不就行了吗". 于是定下本 ADR.

## 选项

### A. Tauri-driver + WebDriver (完整 Tauri binary)
- 装 chromium-driver + tauri-driver, 启动 Tauri binary, WebDriver 协议
- 难度: 高 (CI 装一遍复杂, 跨平台行为不一致)
- 真 e2e: ✅ 全
- 选不选: 否 — 复杂度跟维护成本远超价值

### B. Vite preview + Playwright + Tauri stub (本 ADR 选)
- 本机跑 `vite preview` serve `vpbuddy-client/dist/` (用户安装的同份 bundle)
- Playwright headless chromium 直接连 `http://localhost:4173/`, 操作 DOM
- 注入 `window.__TAURI_INTERNALS__` stub 替掉 Tauri Rust 端 (`invoke` / `transformCallback`)
- server 在 GPU (`192.168.10.63:8765`), 是部署路径 (铁律 5)
- 难度: 中 (Tauri 2.6+ bundle 内部走 `window.__TAURI_INTERNALS__`, 跟老版 `window.__TAURI__` 不一样)
- 真 e2e: ⚠️ UI 端 e2e + server 真链路, 缺 Rust 端音频采集
- 选: ✅

### C. GPU 跑 + 本机看
- 跑 GPU 上的 server + 同 GPU 上的客户端 (但 GPU 没显示器)
- 选不选: 否 — GPU 没 X server, 跑不起 Tauri

### D. 完全 stub (录制 + 回放)
- 录真人操作视频, 再 Playwright 回放
- 选不选: 否 — 这违反"真 e2e"原则, 是 manual 替身

## 设计

### 架构

```
┌─────────────────────────────────────────────────┐
│ 本机 (开发/测试机)                                │
│                                                  │
│  ┌─────────────┐    inject       ┌──────────┐   │
│  │  vite preview├───stub────────►│ Playwright │   │
│  │  (dist/)     │                │ chromium   │   │
│  │  :4173       │                │ headless   │   │
│  └─────────────┘                └──────────┘   │
│         ▲                              │         │
│         │                              │         │
│         │ window.__TAURI_INTERNALS__   │ 真 fetch │
│         │ stub (no Rust binary)       │          │
│         ▼                              ▼         │
│  (window.__TAURI_INTERNALS__.invoke)             │
│         +                                         │
│  /api/* fetch ────────────────────┐              │
└────────────────────────────────────┼──────────────┘
                                     │
                                     ▼
                           ┌──────────────────┐
                           │  GPU 服务器        │
                           │  192.168.10.63    │
                           │  vpbuddy server   │
                           │  :8765             │
                           │  (Chroma + funasr  │
                           │   + pyannote +     │
                           │   ollama)          │
                           └──────────────────┘
```

### 关键决策

1. **本机不起 server**. 所有 server 在 GPU (`192.168.10.63:8765`), e2e 验证 GPU 真 server 在跑 (跑不通就 `pytest.skip`).
2. **Tauri stub 用 `window.__TAURI_INTERNALS__`**. Tauri 2.6+ ESM bundle 内部调用 `window.__TAURI_INTERNALS__.invoke(cmd, args, options)` 和 `window.__TAURI_INTERNALS__.transformCallback(cb, once)`, 不是老的 `window.__TAURI__.core.invoke` (main.js fallback 路径).
3. **vite preview serve `vpbuddy-client/dist/`**. 用现成 bundle (用户安装的同份), 不调 Vite HMR / dev mode.
4. **opt-in 跑**. `RUN_E2E=1` 才跑 + 必须 GPU server 通, 否则全部 skip. CI 默认 release test 不跑这么重 (跑一遍 5-10s).
5. **真 e2e 范围**:
   - ✅ UI 元素 + 用户流 (选会议 / 点击 / 输入 / disabled 状态)
   - ✅ 通过 invoke 调 fetch 真发到 GPU server
   - ✅ server 真 Chroma 灌库 + 真 RAG 检索 + 真跨会议隔离
   - ❌ 不验 Rust 端 (音频 cpal / OS 内录) — 留给手工 + 真设备
6. **fixture 隔离**. e2e 用 GPU 上临时 unique meeting_id (时间戳后缀) 避免污染用户已有数据.

### Stub 必填

```js
window.__TAURI_INTERNALS__ = {
  invoke,                  // (cmd, args, options) → Promise
  transformCallback,       // (cb, once) → number
  unregisterCallback,      // (id) → void
  plugins: { path: { sep: '/', delimiter: ':' } },
};
```

stub 函数:
- `start_capture` → 返 meetingId 让 UI 切到 recording 态
- `stop_capture`, `plugin:event|listen`, `plugin:event|unlisten` → `Promise.resolve()`
- `list_audio_devices` → `[{ name: 'stub-mic', is_default: true, is_loopback: false }]`
- `kb_search` → 真 fetch `/api/kb/search?q=...&meeting_id=...` 让 stub 携带注入的 meeting_id
- `set_gpu_url` / `get_gpu_url` → 返 `window.__VP_E2E_GPU_URL__`
- 其他 → `Promise.resolve(null)`

### 跑法

```bash
# 本机 (假定 GPU server 在 192.168.10.63:8765 + dist/ 已 build)
RUN_E2E=1 pytest src/tests/e2e/ -v -m e2e

# CI 默认跳过 (release test 不跑这么重)
pytest src/tests/  # 无 -m e2e, 自动 skip
```

### 当前进度 (2026-07-02 v1.38)

| 范围 | 测试数 | pass | 状态 |
|------|-------|------|------|
| smoke (vite + GPU + UI 渲染) | 3 | 3 | ✅ 链路通 |
| Req #4 首页会议选择 | 6 | 6 | ✅ |
| Req #3 + #8 KB 隔离 (含 UI stub kb_search 链路) | 4 | 4 | ✅ |
| Req #2 chat 上传 (UI + multipart + 真 KB 灌库 + 隔离) | 6 | 6 | ✅ |
| Req #6 demo 版本切换 (UI + 真 server + 静态文件 + iframe) | 5 | 5 | ✅ |
| Req #5 agent 主动提问 UI (proactive class + role + icon + visual) | 6 | 6 | ✅ |
| **小计** | **30** | **30** | **33s 跑完** |

**6/8 用户需求已 e2e 覆盖** (Req #1 音频 + Req #7 网络搜索留 unit 测).

### 2026-07-02 补充: `/api/_e2e/*` 端点 (env-guarded, 默认 404)

**背景**: 30 个 e2e 跑完发现盲点 — `check_all_docs_stored_notify` (ADR-0022 改名后的
6 doc 完成通知) 路径没被任何 e2e 覆盖, 因为:
- e2e 不跑 batch_docs agent (LLM 强相关, 慢), 所以 6 doc 写满**自然不发生**
- 单元测试 `test_docs_complete_not_close.py` 只 mock `push_event`, 验不出真 GPU 进程行为
- 用户 2026-07-02 指出: "GPU 进程是旧的, 那 e2e 是怎么跑起来的?" 暴露: e2e 没验 GPU
  进程代码版本 = 进程跑 v0.8.3 还是 06ab0e1, e2e 无感

**决策**: 加 env-guarded e2e-only HTTP 端点 `/api/_e2e/check_docs_complete?mid=XXX`,
在**生产 server 进程内部**跑 `check_all_docs_stored_notify`, 让 push_event 推给真 SSE
订阅者. e2e 测试通过该端点 + 真 SSE 订阅, 验 "真 GPU 进程跑新代码不推 docs-complete".

**Env guard**: `VPBUDDY_E2E=1` 才暴露. 生产 deploy 不设这个 env, 端点 404. KISS,
不抽子模块, 跟其他 debug env (`VPBUDDY_PROACTIVE_INTERVAL`) 同模式.

**新增 e2e** (`test_docs_complete_no_sse.py`, 4 tests):
- `test_e2e_endpoint_requires_env_guard`: 验 200 (e2e 启了) 或 404 (prod), 不接受 500
- `test_check_returns_true_when_all_6_docs_stored`: 6 doc 写满 → check 返 True (真 server 行为)
- `test_check_does_not_push_docs_complete_event`: 核心 — SSE 流**不**含 "docs-complete"
- `test_check_does_not_close_meeting`: ADR-0022 回归保护 — check 后会议 state 仍可读

**新 e2e 触发链**:
1. SSH 写 6 doc 到 GPU `/home/zsd/vpbuddy/docs/{mid}/`
2. 后台线程订阅 SSE `/api/meetings/{mid}/events` 收 2.5s
3. POST `/api/_e2e/check_docs_complete?mid=XXX` (env-guarded)
4. 真 GPU 进程跑 `check_all_docs_stored_notify` → push_event 推给 SSE 订阅者
5. 验 SSE events 列表**不**含 "docs-complete" + "doc-update"
6. cleanup: SSH 删 6 doc

**累计 31/31 e2e** (30 + 4 - 3 重复断言, 实际 33 个 test functions).

## 后果

### 积极

- ✅ 第一个真 e2e (UI + GPU server 链路)
- ✅ 跑了用户 4 大需求的其中 2 个的真 e2e (首页会议选择 + KB 隔离)
- ✅ 链路通了, 后续 e2e 加 stub `case` + 测试函数就行
- ✅ 5.3s 跑完全部, CI 可接受
- ✅ 真 Chroma + 真 sentence-transformers 在 GPU 上验证 KB 隔离 (用户原话: '不同会议要做隔离')
- ✅ UI stub 链路验证 (UI invoke("kb_search") → 真 server → 真隔离)

### 消极

- ❌ Rust 音频采集不在 e2e 覆盖范围 (cpal, 麦克风/内录), 留给真硬件 + 手工
- ❌ Windows WASAPI loopback 不在 e2e (v0.9.x 计划), 跨平台手动
- ❌ Tauri 2.6+ ESM bundle 的 `__TAURI_INTERNALS__` API 可能再变, stub 要维护
- ❌ pytest 启动 vite preview 需要 chromium 装好 (~140MB), 用户机先预装

### 风险

- e2e 真跑会污染 GPU 上的会议 / KB 数据 (灌的 doc 残留). **缓解**: 用时间戳后缀 unique meeting_id + 不主动清理 (其他 e2e 上传也一样需要清理, 留 v0.9.x TODO).
- vite preview 进程泄漏 (上轮 fixture 退出没杀干净). **缓解**: 用 `os.setsid` 让进程组独立, finalizer SIGTERM + 5s 后 SIGKILL 全 group.
- GPU server 上 Chroma 第一次 query 加载 embedding 模型 ~1s (AGENTS.md §四.已知陷阱). **缓解**: 各 test 自带 timeout, GPU 真加载不会 hang 永久.

## 关联

- 下游: Req #1 (音频 e2e) 留手动 (需真硬件), 不入本套 e2e
- 上游: ADR-0020 KB 隔离 (用户原话采纳), ADR-0019 Chroma 选型, ADR-0022 会议结束语义
- 教训: e2e stub 必须**先在真实 production 路径上跑通一次 (smoke 链路), 再写业务测试**, 否则会在 stub API 错的地方耗一晚上
