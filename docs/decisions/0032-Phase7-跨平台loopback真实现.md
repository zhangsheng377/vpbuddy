# 0032. Phase 7 跨平台 loopback 真实现 (Linux PulseAudio monitor / macOS BlackHole / Windows WASAPI fallback)

- **状态**: 已接受 (2026-07-02)
- **日期**: 2026-07-02
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (取代 v0.7.1 stub [ADR-0031](./0031-Phase7-客户端双轨采集-stub落地.md))
- **依赖**: [ADR-0021](./0021-桌面客户端-麦克风+内录双轨.md) (loopback 设计) · [ADR-0031](./0031-Phase7-客户端双轨采集-stub落地.md) (v0.7.1 stub, 本 ADR 落地)
- **落地**: v0.8.0

## 背景

v0.7.1 ([ADR-0031](./0031-Phase7-客户端双轨采集-stub落地.md)) 把 audio_source 路由 + `new_with_source` API + `mix_stereo_into` helper + `#[cfg(test)]` 测试落到位, **但 `loopback` / `both` 都 fallback 到 microphone**, 用户期望录系统声实际录的是麦 (UI 没明显提示)。

[ADR-0021](./0021-桌面客户端-麦克风+内录双轨.md) 当初设计时**错误判断** cpal 0.15 暴露了 cross-platform WASAPI loopback API (`wasapi::Device::is_loopback()`)。**实测** cpal 0.15.3 源码确认: 只有 `windows-rs` unsafe 的 `IAudioRenderClient` 才能拿 render flow 设备, cpal 上层抽象**不暴露** cross-platform loopback API。

## 决策

### 1. 三平台差异落地 (基于 cpal 公 API 实际能力)

| 平台 | mic path | loopback path | 实现方式 |
|------|----------|---------------|----------|
| **Linux** | cpal ALSA default input | **PulseAudio monitor source** | `host.input_devices()` 枚举, 名字后缀 `.monitor` 的标 is_loopback; 默认 monitor = `host.default_input_device()` 是 mic, **不**是 monitor, 用 `pacmd list-sources \| grep -F '.monitor'` 列 (或 cpal 拿所有 input, 取第一个含 `.monitor` 的) |
| **macOS** | cpal CoreAudio default input | **BlackHole virtual device** | `host.input_devices()` 枚举, 名字含 `BlackHole` / `Loopback` / `Soundflower` 标 is_loopback; 没装 → loopback path fallback mic + log warn (UI 提示装 BlackHole) |
| **Windows** | cpal WASAPI default input | **cpal 抽象层无法拿** | fallback mic + 强 warn (cpal 0.15 不暴露 cross-platform loopback, 需 unsafe `windows-rs::IAudioRenderClient` — v0.9.x 再加 Windows-only `#[cfg(target_os = "windows")]` 子模块) |

### 2. 架构改动

```
audio.rs
├── data 结构
│   └── AudioDeviceInfo { id, name, is_default, is_loopback }  // + is_loopback
├── helpers (pure)
│   ├── is_loopback_device_name(name: &str) -> bool  // 平台分支: linux 看 .monitor / macos 看 BlackHole/Loopback / windows 恒 false
│   └── mix_two_streams(mic: &[i16], loopback: &[i16]) -> Vec<i16>  // 等权混合 (重对齐 短端补零)
├── platform 分支
│   ├── #[cfg(target_os = "linux")]   detect_default_loopback()  -> Option<String>  // 取第一个 .monitor 设备
│   ├── #[cfg(target_os = "macos")]   detect_default_loopback()  -> Option<String>  // 取第一个 BlackHole 设备
│   └── #[cfg(target_os = "windows")] detect_default_loopback()  -> None  // v0.9
└── impl AudioCapture
    ├── new_with_source(device_id, audio_source)  // 真接: mic 走旧 path / loopback 找 default monitor 后调 inner / both 走 mix_two_streams 双 stream
    └── list_input_devices()  // 每设备标 is_loopback

main.rs
├── list_audio_devices  tauri command  // 现成; 返 is_loopback 字段
├── start_capture       audio_source 透传 (已 v0.7.1)
└── stop_capture        audio_source state 重置为 None (清 v0.7.1 留的旧值)

ui/index.html
├── audio-source-kind 下拉 (已 v0.7.1, 留)
├── audio-source-kind change handler  // 按 source_kind 重新 filter audio-device 下拉
└── audio-source-kind hint banner  // macOS loopback 选时显示"需装 BlackHole" + 链接; Linux 显示"用 PulseAudio monitor"

ui/main.js
├── initAudioDevices 改成 invoke 后按 source_kind filter
└── macOS loopback 检测: invoke result 里 is_loopback 全 false → 显示 "未检测到 BlackHole" banner
```

### 3. `both` 模式简化 (v0.8 一期简化)

- 不做时间戳精确对齐 (2 stream 各自 cpal 启, 各自 buffer 累积到 1s 切片)
- 短端补零 + 等权混合 `(mic[i] + loopback[i]) / 2`, `mix_two_streams` 实现
- v0.8.x quality 不好, v0.9 再调 (gating / soxr 重采样 / 时间戳对齐)

### 4. Windows fallback 决策

- 不在 v0.8.x 加 unsafe `windows-rs::IAudioRenderClient` (v0.7.1 风险评估过: Tauri client 9x 不到 Windows, 用户主平台 Linux/Mac)
- v0.8.x Windows 用户: UI 选 loopback → 强 warn "Windows 真 loopback v0.9.x 实现, 当前 fallback 录麦克风", 不阻录音 (用户可继续开会)
- v0.9.x 计划: 加 `#[cfg(target_os = "windows")]` 子模块, 用 `IAudioClient` + `IAudioRenderClient` 拿 default render endpoint, build WASAPI loopback stream; unsafe 包在最小模块, 文档化 security review

## 设计取舍

### 为什么不引 `cpal` 直接 platform API?

cpal 0.15.3 在 `host::wasapi::Device` 私有字段才有 `is_loopback()`, **不** 暴露 `cpal::Device::is_loopback()`。两种走法:
- (A) unsafe 跨 platform branch 直接调底层 crate (ALSA / CoreAudio / WASAPI), 高风险 + 跨平台 unsafe 块 — ❌
- (B) 用 `cpal::Device::name()` 在 Linux/macOS 上**匹配字符串** (`.monitor` / `BlackHole`), 在 Windows 上 fallback — ✅

选 (B): 用 KISS string match, Linux/macOS 真覆盖 (PulseAudio / PipeWire 都暴露 `.monitor` 后缀; BlackHole 是惯例名), Windows 留给 v0.9.x unsafe 重构。

### 为什么不引 `soxr` 做 resample?

`mix_two_streams` 用 native 采样率切片, 不跨采样率混合 (mic/loopback 同 native 采样率 — 都是 system default 48kHz)。**不需要 resample**, 简化为 buffer 累积 + 等权 + 主循环 16kHz downsample (复用 v0.7.0 `resample_linear`)。

### 为什么不 cancel v0.7.1 stub?

v0.7.1 stub 已 publish 装了客户端的用户**已经**用 mic path 工作, v0.8.0 不破 ABI — `new_with_source(device_id, audio_source)` 签名不变, 只是 mic path 内部走老逻辑 + loopback path 内部走新 `detect_default_loopback` + new is_loopback flag。新老用户**无缝升级**, 唯一变化: 选 loopback/both 现在真录系统声。

## 实施细节

| 文件 | 改动 |
|------|------|
| `vpbuddy-client/src-tauri/src/audio.rs` | +`is_loopback: bool` 字段; +`is_loopback_device_name` + `detect_default_loopback` + `mix_two_streams`; `list_input_devices` 加 is_loopback 标注; `new_with_source` 真接; +inline tests (5 个) |
| `vpbuddy-client/src-tauri/src/main.rs` | `stop_capture` 加 `*state.audio_source.lock().await = None` (cleanup v0.7.1 留值) |
| `vpbuddy-client/ui/index.html` | audio-source-kind 下拉加 title hint; macOS loopback select 显示 banner 占位 |
| `vpbuddy-client/ui/main.js` | `initAudioDevices` 按 source_kind filter + macOS 黑屏 banner 触发 |
| `pyproject.toml` | `0.7.3` → `0.8.0` (minor bump, Phase 7 真实现是 minor) |
| `src/vpbuddy/_version.py` | `0.8.0` |
| `README.md` | CHANGELOG 段 `v0.8.0 (2026-07-02)`: 跨平台 loopback 真实现 |
| `docs/design/总体架构.md` | v1.33 段: 三平台 loopback 现状 |
| `AGENTS.md` | 三. 跨平台部署注意: 更新三平台表 (Linux ✅ 真实现 / macOS ✅ BlackHole 需装 / Windows ⚠️ v0.9) |
| `docs/decisions/0021-桌面客户端-麦克风+内录双轨.md` | 顶部加 "修订注 2026-07-02: cpal 0.15.3 不暴露 cross-platform WASAPI loopback, 详见 ADR-0032 妥协" |

**LOC**: +~180 lines Rust (含 platform cfg + 5 tests + comments); +~40 lines JS/HTML; +1 ADR-0032 全文; AGENTS.md + design + README + pyproject

## 后果

### 积极

- ✅ **Linux 真内录**: PulseAudio / PipeWire 用户选 loopback/both, 录到的真含系统声
- ✅ **macOS 真内录** (装 BlackHole 后): BlackHole 用户选 loopback/both 正常
- ✅ **UI 不破**: 现有 v0.7.1 客户端用户升级无感, audio_source=microphone 行为 100% 一致
- ✅ **测试覆盖**: inline tests 5 个 (is_loopback_device_name × 3 platform + mix_two_streams 边界 + detect_default_loopback 单元化抽 signature)
- ✅ **Windows 路径明确**: fallback + UI 强提示, 不假装"录到了" (避免 v0.7.x 兜底误导)

### 消极

- ⚠️ **Windows 真 loopback 仍未实现**: v0.8.x 仍 fallback mic. 用户选 loopback 在 Windows 上会收到 "v0.9.x 实现" warn, 但录音不破
- ⚠️ **`both` 混合质量粗糙**: 短端补零 + 等权, 短端 0 时输出偏 mic 侧. v0.9 加时间戳对齐 + soxr 重采样
- ⚠️ **macOS BlackHole 用户操作**: 不装 BlackHole → loopback 不可用, UI 需提示 (本 ADR 加 banner, 但 UX 上仍需用户主动装 driver)
- ⚠️ **inline test 不能测 cpal 真 e2e**: `detect_default_loopback` 在本机 Linux (PulseAudio 没启 monitor) 返 None, 测试用 mock 字符串 match 验证 is_loopback_device_name; 真 cpal e2e 仍是 install-client.sh 实机验证

## 未决问题 (v0.9.x)

- Windows WASAPI loopback 真实现 (`IAudioRenderClient` unsafe 包装, 最小模块)
- `both` 模式时间戳精确对齐 (mic + loopback 同 cpal 启时间, 取时间戳标 diff ≤ 50ms 视为同帧, 否则分帧)
- 引入 `soxr` 做 mic + loopback 不同采样率时的高质量重采样
- `AudioCaptureConfig` struct 替代 `new_with_source` 顺序参 (API ergonomics, 非必需)

## 关联

- ADR-0021 (loopback 设计) — 顶部加修订注指本 ADR
- ADR-0031 (v0.7.1 stub) — 本 ADR 取代
- AGENTS.md 三. 跨平台部署注意 — 更新三平台表
- `vpbuddy-client/src-tauri/src/audio.rs` (主改)
- `vpbuddy-client/ui/index.html` + `main.js` (UI 提示)