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

### 当前进度

| 范围 | 测试数 | pass | 状态 |
|------|-------|------|------|
| smoke (vite + GPU + UI 渲染) | 3 | 3 | ✅ 链路通 |
| Req #4 首页会议选择 | 6 | 6 | ✅ |
| Req #3 + #8 KB 隔离 | 4 | 4 | ✅ |
| Req #6 demo 版本号 | 0 | - | 📋 TODO |
| Req #5 agent 主动提问 | 0 | - | 📋 TODO |
| Req #2 chat 上传 UI | 0 | - | 📋 TODO |
| **小计** | **13** | **13** | **5.3s 跑完** |

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
