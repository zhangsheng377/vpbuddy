# 0031. Phase 7 客户端双轨采集 stub 落地 (microphone / loopback / both)

- **状态**: 已接受 (Stub 阶段)
- **日期**: 2026-07-02
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (新增 stub)
- **依赖**: [ADR-0021] (服务端 audio_source 字段), [vpbuddy-client/src-tauri/src/audio.rs](../vpbuddy-client/src-tauri/src/audio.rs)

## 背景

v0.7.0 release 后, 用户反馈需要在 Tauri 客户端上实现"录系统声音" (loopback) 能力 — 当前录音只走 `microphone` 一条路, 完全不能录会议另一头 / 视频 / YouTube 等系统音频。架构层面:

1. **服务端已就绪** (ADR-0021 / 2026-06-21): `POST /api/meetings/stream_start?audio_source=microphone|loopback|both` 服务端已经接收 `audio_source` 字段并持久化
2. **前端 UI 已就绪** (Commit 4 `e5712a8`, ADR-0030, 2026-07-02): UI 已经有 audio_source 下拉 (microphone/loopback/both), 传给 start_capture
3. **客户端 Rust 端缺实现**: `main.rs::start_capture` 收到 `audio_source` 但只传到 GPU server, **客户端自己的 cpal 采集完全忽略 audio_source**, 默认全走 mic

## 决策

### 1. API surface 改造: `AudioCapture::new_with_source()` 统一入口

```rust
// 新公开 API — caller (main.rs) 走这个
pub fn new_with_source(device_id: Option<String>, audio_source: &str) -> Result<Self>

// 保留 legacy API — 内部拆为 mic-only
pub fn new_with_device(device_id: Option<String>, audio_source: &str) -> Result<Self>
//                                          └─ 内部仅 log, 真逻辑走 self_new_with_device_inner
```

**为什么不直接改 `new_with_device` 签名**: Tauri command 和测试代码可能从其他地方调过 `new_with_device(None)`。新加一层 wrapper 既不破现有 caller (那些 caller 现在只调 `new()` 默认路径), 也清晰表达 intent。

### 2. routing: `match audio_source { "microphone" => ... ; "loopback" => stub ; "both" => stub ; 未知 => mic fallback }`

**关键决策**: v0.7.1 **不实现** loopback / both 跨平台 cpal 调用, 只:

| audio_source | v0.7.1 行为 | v0.8.x 计划 |
|---|---|---|
| `microphone` (默认) | ✅ 现行 cpal mic 逻辑 | 不变 |
| `loopback` | ⚠️ warn log + **fallback mic** | Linux PulseAudio monitor / macOS BlackHole detect / Windows WASAPI loopback |
| `both` | ⚠️ warn log + **fallback mic** | mic + loopback 双 stream 混合 (用 `mix_stereo_into` 已落库) |
| 未知值 | ⚠️ warn log + **fallback mic** | 同上 |

**fallback 策略**: 不 throw / 不 panic, 只 warn + 落 mic。这样:
- ✅ 不破 v0.7.0 mic 主流程 (向后兼容)
- ✅ 选 loopback/both 时 warn 提示用户
- ✅ `mix_stereo_into` helper 先 commit, v0.8.x 开 both path 时复用

### 3. AppState 加共享字段

```rust
// 2026-07-02 Phase 7
pub audio_source: Arc<Mutex<Option<String>>>,
//                          └─ None = 还没 start_capture; Some("microphone") = 已设
```

**为什么要共享**: `start_capture` 在 Tauri async runtime, 写 `Some(audio_source_norm)` 后 .clone; `run_capture_loop` 的 `spawn_blocking` 线程读之前 outer scope 也 .clone() → spawn 内 move. 不共享的话 `audio_source_norm` 这个局部变量撑死也跑不到 cpal 线程, 跟 v0.7.0 完全一样无效。

**stop_capture 重置**: 见 "[未决问题](#未决问题)" — 当前 stop_capture 不清, 但 start_capture 总是先写再读, 没影响。

### 4. `mix_stereo_into` helper (pure, pub fn)

```rust
/// 双声道 → 单声道等权混合. for both path (mic+loopback = 2ch 混合)
///
/// 调试 assert: src.len() % 2 == 0 (L/R 帧配对)
/// overflow: ((l + r) / 2).clamp(i16::MIN, i16::MAX) 防止削顶
///
/// v0.7.1 stub: helper 落地, both path 不接. v0.8.x 真 both path 复用.
pub fn mix_stereo_into(dst: &mut Vec<i16>, src: &[i16])
```

**为什么不一次到位实现 both path**: 涉及 (a) cpal 双 stream 并行采集 (b) soxr 重采样 (c) 后台 merge 线程 (d) 跨平台 loopback 调用。一周内 ship 不了, 风险高。KISS 拆 stub → impl 是 v0.8 PR。

### 5. inline unit tests (audio.rs 末尾 `#[cfg(test)] mod tests`)

| Test | 验 |
|---|---|
| `mix_stereo_into_full_and_zero` | `[MAX, 0, MAX, 0]` → `[MAX/2, MAX/2]` 半幅 |
| `mix_stereo_into_overflow_clamp` | `[MAX, MAX, MAX, MAX]` → clamp 到 `[MAX, MAX]` |
| `mix_stereo_into_odd_length_panics` | `[1,2,3]` 触发 `debug_assert!` panic |
| `mix_stereo_into_appends_not_clears` | 多次调用累加, 不清 dst |
| `resample_linear_same_rate_identity` | 16k→16k 直通 |
| `resample_linear_downsample_48k_to_16k` | ratio=3 下采样, len ∈ [15,17] |

**为什么 inline**: 不依赖 mock cpal; 外部 `tests/unit/audio.rs` 会需要 整编译 cpal runtime, inline 跑 `cargo test --lib` 秒级。真实 e2e 录音由 `install-client.sh` 触发实机测。

## 后果

### 积极

- ✅ **代码骨架就位**: `new_with_source` pub API + AppState field + main.rs 透传, 全链路通
- ✅ **不破 v0.7.0**: mic path 完全不动, 选 microphone 行为 100% 一致
- ✅ **测试覆盖**: 6 个 inline unit test, pure helpers
- ✅ **warn UX**: 用户选 loopback/both, log 立刻提示"暂未实现, v0.8 跟随平台"
- ✅ **`mix_stereo_into` ready**: v0.8 both path 直接调, 不重写

### 消极 / 取舍

- ⚠️ **audio_source=loopback/both 仍 fallback mic**: 用户期望录系统声, 但实际录的是麦克风。ADR 写明 + warn log, 但用户体验上**没真正录到**, 可能误以为录到了。
  - **缓解**: docstring + UI 端 "音频源" label 加 "(待 v0.8 实现)" 副标 — 留 v0.8.x UI 升级 PR
- ⚠️ **inline test 覆盖率有限**: 只测 pure helper, 没测真 cpal e2e (4 warnings: dead_code for `new`, `mix_stereo_into`, `create_meeting` pub 没 caller, `last_recv` unused assign)
  - **缓解**: follow-up PR 加 e2e (mock cpal Stream + 实际 mono verify)
- ⚠️ **stop_capture 没清 audio_source**: stop 后 state 仍 `Some("loopback")`, 第二次 start 如果不传新 audio_source, 会读到旧值 (但实际上 start_capture 总是传新参覆写, 没问题)
  - **缓解**: stop_capture 加 `*state.audio_source.lock().await = None` 是 v0.8 cleanup 一并做

## 未决问题

- **stop_capture 是否清 audio_source**: 当前不清, 行为 OK 但不优雅. v0.8.x cleanup PR 同步处理
- **`new_with_device` 是否 deprecated**: 当前保留向后兼容 + 内部仅 log. v0.8.x 如果 dev 全部切到 `new_with_source`, 可以 `#[deprecated]` + 单 API
- **`mix_stereo_into` 是否升级为 soxr-based**: 当前 KISS 等权平均. v0.8 both path 真接时考虑 soxr crate (更工业级, 避免相位漂移)

## 实施细节

| 文件 | 改动 |
|---|---|
| `vpbuddy-client/src-tauri/src/audio.rs` | `+new_with_source` API, `+self_new_with_device_inner` 内部, `+mix_stereo_into` pure, `+#[cfg(test)] mod tests` 6 测试 |
| `vpbuddy-client/src-tauri/src/main.rs` | AppState 加 `audio_source` 字段; start_capture 写共享; outer scope clone 给 spawn; run_capture_loop 加参; spawn_blocking 用 `new_with_source(device, &audio_source)` |
| `pyproject.toml` | `0.7.0` → `0.7.1` |
| `src/vpbuddy/_version.py` | `0.7.1` |
| `README.md` | CHANGELOG 段 `v0.7.1 (2026-07-02)` |
| `docs/design/总体架构.md` | v1.32 段: Phase 7 stub |

**LOC**: +~85 lines Rust (含 tests + comments), 4 new code paths, 0 breaking changes

## 验证

| 项 | 结果 |
|---|---|
| `cargo check` | ✅ 0 errors, 4 warnings (all dead_code/unused, expected) |
| `cargo test --lib` | ✅ 6/6 passed in 0.00s |
| `cargo check --bin` | ✅ vpbuddy-client bin 编译过 |
| 现有 pytest 回归 | (跑 TODO in commit hook) |
