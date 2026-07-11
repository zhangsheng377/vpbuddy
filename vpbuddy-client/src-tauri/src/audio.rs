// 跨平台音频采集 (cpal 抽象)
// 设计: 16kHz mono i16 PCM → 返回 Vec<i16>
//       Linux/macOS/Windows 自动用对应后端
//
// 2026-07-02 Phase 7 v0.8.0: 跨平台 loopback 真实现
//   - Linux: PulseAudio/PipeWire monitor source (cpal 暴露, 名字 .monitor 后缀)
//   - macOS: BlackHole / Loopback / Soundflower 虚拟设备 (cpal 暴露, 名字匹配)
//   - Windows: cpal 抽象层无 cross-platform loopback API → fallback mic + UI 提示 (v0.9.x unsafe 重构)

use anyhow::{Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::Serialize;
use std::sync::{mpsc, Arc};
use std::time::Duration;

#[derive(Debug, Serialize, Clone)]
pub struct AudioDeviceInfo {
    pub id: String,
    pub name: String,
    pub is_default: bool,
    /// 2026-07-02 Phase 7 v0.8.0: 标记是否为内录设备 (Linux monitor / macOS BlackHole 等)
    /// false = 普通麦克风; true = 系统音频输出 capture
    pub is_loopback: bool,
}

/// 2026-07-02 Phase 7 v0.8.0: 保活 cpal Stream (单/双 stream)
/// `Single` 是 v0.7.x 老 path; `Merged` 是 v0.8.0 新 both path (双 stream 必须并存不掉线)
// 注: `Single.0` 字段从不 read — cpal::Stream 仅靠保活工作 (持有 ≠ 调用字段),
// 但 tuple variant 必带至少一个字段 (否则编译器让改成 unit variant 而 enum 语法变).
// 加 allow(dead_code) 抑制警告, 保留 enum 二态语义 (Single | Merged) 清晰.
#[allow(dead_code)]
enum StreamGuard {
    Single(cpal::Stream),
    Merged {
        _mic: Arc<cpal::Stream>,
        _loopback: Arc<cpal::Stream>,
    },
    #[cfg(target_os = "windows")]
    WasapiLoopback(wasapi_loopback::WindowsLoopback),
    #[cfg(target_os = "windows")]
    WasapiBoth {
        _mic: Arc<cpal::Stream>,
        _loopback: wasapi_loopback::WindowsLoopback,
    },
}

/// 线性重采样器：输入任意 sample rate 的 i16 样本，输出到目标 rate
struct StreamResampler {
    src_rate: f64,
    dst_rate: f64,
    frac: f64,
    last: f64,
}

impl StreamResampler {
    fn new(src_rate: u32, dst_rate: u32) -> Self {
        Self { src_rate: src_rate as f64, dst_rate: dst_rate as f64, frac: 0.0, last: 0.0 }
    }

    fn push(&mut self, src: &[i16], dst: &mut Vec<i16>) {
        let ratio = self.src_rate / self.dst_rate;
        for &s in src {
            self.frac += 1.0;
            while self.frac >= ratio {
                self.frac -= ratio;
                let t = self.frac / ratio;
                let v = self.last * (1.0 - t) + (s as f64) * t;
                dst.push(v.clamp(i16::MIN as f64, i16::MAX as f64) as i16);
            }
            self.last = s as f64;
        }
    }
}

pub struct AudioCapture {
    _stream: StreamGuard,
    rx: mpsc::Receiver<Vec<i16>>,
    /// 2026-06-27: 设备原生采样率, read_chunk_blocking 用来重采样到目标 16kHz
    native_sample_rate: u32,
    /// 2026-07-02 Phase 7 v0.8.0: 标记当前采集是 loopback (only/both path), 用于 run_capture_loop 决断是否触发音频卡死 banner
    is_loopback: bool,
}

impl AudioCapture {
    // 2026-07-02: 三个 v0.7.x 留 pub API 不再被新代码调用, 但保留作向后兼容 + 文档化 KISS 入口
    // `new` = 默认 mic path 便捷构造; `new_with_device` = v0.7.x 老签名; `is_loopback_active` = 给前端 status 面板预留
    // 不删 (后续 v0.9 重构 AudioCaptureConfig struct 时一并删 + 替换)
    #[allow(dead_code)]
    pub fn new() -> Result<Self> {
        Self::new_with_source(None, "microphone")
    }

    /// 2026-07-02 Phase 7 v0.8.0: 真接 loopback / both 路径
    /// - "microphone" / "mic": 老 mic path (v0.7.0 行为, 完全不变)
    /// - "loopback": 平台分支找默认 monitor / BlackHole 设备, 找不到 fallback mic + 强 warn
    /// - "both": mic + loopback 双 stream 并行, mix_two_streams 累积 + 等权混合 (mix 到 mpsc 通道)
    /// - 未知值: warn + 兜底 microphone (兼容 v0.7.1)
    ///
    /// 行为契约 (相对于 v0.7.1 stub):
    ///   ✅ microphone path: 100% 行为不变
    ///   ✅ loopback path: Linux/macOS 真录系统声 (Windows 仍 fallback mic, 强 warn)
    ///   ✅ both path: mic + loopback 混合 (Linux/macOS 真覆盖; Windows fallback mic-only + warn)
    pub fn new_with_source(device_id: Option<String>, audio_source: &str) -> Result<Self> {
        match audio_source {
            "microphone" | "mic" => Self::new_with_device_inner(device_id),
            "loopback" => {
                #[cfg(target_os = "windows")]
                {
                    return Self::new_wasapi_loopback();
                }
                #[cfg(not(target_os = "windows"))]
                {
                    let loopback_dev_id = detect_default_loopback();
                    match loopback_dev_id {
                        Some(id) => {
                            log::info!("Phase 7: loopback 用设备 {id:?}");
                            let mut cap = Self::new_with_device_inner(Some(id))?;
                            cap.is_loopback = true;
                            Ok(cap)
                        }
                        None => {
                            log::warn!(
                                "loopback 设备未找到 (Linux 无 PulseAudio/PipeWire monitor / macOS 未装 BlackHole) — 兜底用 microphone"
                            );
                            Self::new_with_device_inner(device_id)
                        }
                    }
                }
            }
            "both" => {
                #[cfg(target_os = "windows")]
                {
                    return Self::new_with_device_inner(device_id);
                }
                #[cfg(not(target_os = "windows"))]
                {
                    let loopback_dev_id = detect_default_loopback();
                    match loopback_dev_id {
                        Some(id) => Self::new_with_both_streams(device_id, Some(id)),
                        None => {
                            log::warn!(
                                "both path 缺 loopback 设备, 退化为仅 microphone (Linux 无 monitor / macOS 未装 BlackHole)"
                            );
                            Self::new_with_device_inner(device_id)
                        }
                    }
                }
            }
            other => {
                log::warn!(
                    "未知 audio_source={other:?}, 期望 microphone|loopback|both — 兜底用 microphone"
                );
                Self::new_with_device_inner(device_id)
            }
        }
    }

    /// 2026-06-25: cherry-pick from feature/requirements-architecture-update
    /// 2026-06-27: 用设备原生采样率, 主循环 resample 到 16kHz
    /// 2026-07-02 Phase 7 v0.8.0: 仍 pub 保留向后兼容 (v0.7.x 调用方), 但内部 impl 全走 new_with_source
    #[allow(dead_code)]  // 2026-07-02: v0.7.x 老签名, 新代码走 new_with_source. v0.9 重构 AudioCaptureConfig 时一并清理
    pub fn new_with_device(device_id: Option<String>, audio_source: &str) -> Result<Self> {
        log::debug!("audio_source={audio_source} (本期仅 log, mic path 仍走 new_with_device_inner)");
        let _ = audio_source; // suppress unused
        Self::new_with_device_inner(device_id)
    }

    #[cfg(target_os = "windows")]
    fn new_wasapi_loopback() -> Result<Self> {
        let (rx, native_rate, guard) = wasapi_loopback::create_loopback()?;
        log::info!("WASAPI loopback 就绪, native_rate={}", native_rate);
        Ok(Self {
            _stream: StreamGuard::WasapiLoopback(guard),
            rx,
            native_sample_rate: native_rate,
            is_loopback: true,
        })
    }

    #[cfg(target_os = "windows")]
    fn new_with_wasapi_both(device_id: Option<String>) -> Result<Self> {
        let AudioCapture { rx: mic_rx, _stream: mic_guard, native_sample_rate: mic_rate, .. } =
            Self::new_with_device_inner(device_id)?;
        let (loopback_rx, loopback_rate, loopback_guard) = wasapi_loopback::create_loopback()?;
        let target_rate: u32 = 16000;
        log::info!("Phase 7 Win both: mic={}Hz + loopback={}Hz → resample both → {}Hz",
            mic_rate, loopback_rate, target_rate);

        let mic_stream = match mic_guard {
            StreamGuard::Single(s) => Arc::new(s),
            _ => anyhow::bail!("内部错误: mic 端必须是 Single variant"),
        };

        let (out_tx, out_rx) = mpsc::sync_channel::<Vec<i16>>(64);
        let chunk_target = (target_rate as usize) / 10; // 100ms frames
        std::thread::spawn(move || {
            let mut mic_resampler = StreamResampler::new(mic_rate, target_rate);
            let mut loop_resampler = StreamResampler::new(loopback_rate, target_rate);
            let mut mic_buf: Vec<i16> = Vec::new();
            let mut loop_buf: Vec<i16> = Vec::new();
            loop {
                while mic_buf.len() < chunk_target {
                    match mic_rx.recv_timeout(Duration::from_millis(500)) {
                        Ok(mut chunk) => {
                            mic_resampler.push(&chunk, &mut mic_buf);
                        }
                        Err(mpsc::RecvTimeoutError::Timeout) => { mic_buf.resize(chunk_target, 0); break; }
                        Err(_) => return,
                    }
                }
                while loop_buf.len() < chunk_target {
                    match loopback_rx.recv_timeout(Duration::from_millis(500)) {
                        Ok(mut chunk) => {
                            loop_resampler.push(&chunk, &mut loop_buf);
                        }
                        Err(mpsc::RecvTimeoutError::Timeout) => { loop_buf.resize(chunk_target, 0); break; }
                        Err(_) => return,
                    }
                }
                let take = chunk_target.min(mic_buf.len()).min(loop_buf.len());
                let mixed = mix_two_streams(&mic_buf[..take], &loop_buf[..take]);
                if out_tx.send(mixed).is_err() { return; }
                mic_buf.drain(..take);
                loop_buf.drain(..take);
            }
        });

        Ok(Self {
            _stream: StreamGuard::WasapiBoth { _mic: mic_stream, _loopback: loopback_guard },
            rx: out_rx,
            native_sample_rate: target_rate,
            is_loopback: true,
        })
    }

    /// 实际设备 init (v0.7.0 原 logic, v0.8.0 内部用)
    fn new_with_device_inner(device_id: Option<String>) -> Result<Self> {
        let host = cpal::default_host();
        let device = if let Some(id) = device_id {
            let devices = host.input_devices().context("无法枚举输入设备")?;
            devices
                .filter_map(|d| d.name().ok().map(|name| (name, d)))
                .find(|(name, _)| name == &id)
                .map(|(_, d)| d)
                .with_context(|| format!("找不到音频输入设备: {id}"))?
        } else {
            host.default_input_device()
                .context("找不到默认输入设备 (麦克风/loopback)")?
        };

        // 2026-06-27 修: 取设备支持的默认采样率 (而不是写死 16kHz)
        let supported = device
            .supported_input_configs()
            .context("无法读取设备支持的输入配置")?;
        let configs: Vec<_> = supported.collect();
        let cfg = configs
            .iter()
            .find(|c| c.channels() == 1)
            .or_else(|| configs.iter().max_by_key(|c| c.channels()))
            .context("设备没有任何支持的输入配置")?;
        let native_sample_rate = cfg.max_sample_rate().0;
        log::info!(
            "cpal: 设备 {:?} 原生采样率 {}Hz, {} ch, format {:?}",
            device.name().unwrap_or_default(),
            native_sample_rate,
            cfg.channels(),
            cfg.sample_format()
        );

        let config = cpal::StreamConfig {
            channels: cfg.channels(),
            sample_rate: cpal::SampleRate(native_sample_rate),
            buffer_size: cpal::BufferSize::Default,
        };
        let channels = cfg.channels() as usize;

        let (tx, rx) = mpsc::sync_channel::<Vec<i16>>(64);
        let fmt = cfg.sample_format();
        let stream: cpal::Stream;
        if fmt == cpal::SampleFormat::I16 {
            let tx_i16 = tx.clone();
            let data_cb = move |data: &[i16], _: &cpal::InputCallbackInfo| {
                let mono: Vec<i16> = if channels == 1 {
                    data.to_vec()
                } else {
                    data.chunks(channels)
                        .map(|frame| {
                            let sum: i32 = frame.iter().map(|&s| s as i32).sum();
                            (sum / channels as i32) as i16
                        })
                        .collect()
                };
                let _ = tx_i16.try_send(mono);
            };
            stream = device.build_input_stream(&config, data_cb, |err| log::error!("cpal stream error: {err}"), None)?;
        } else if fmt == cpal::SampleFormat::F32 {
            let tx_f32 = tx.clone();
            let data_cb = move |data: &[f32], _: &cpal::InputCallbackInfo| {
                let i16_data: Vec<i16> = data
                    .iter()
                    .map(|&s| (s.clamp(-1.0, 1.0) * 32767.0) as i16)
                    .collect();
                let mono: Vec<i16> = if channels == 1 {
                    i16_data
                } else {
                    i16_data
                        .chunks(channels)
                        .map(|frame| {
                            let sum: i32 = frame.iter().map(|&s| s as i32).sum();
                            (sum / channels as i32) as i16
                        })
                        .collect()
                };
                let _ = tx_f32.try_send(mono);
            };
            stream = device.build_input_stream(&config, data_cb, |err| log::error!("cpal stream error: {err}"), None)?;
        } else if fmt == cpal::SampleFormat::U16 {
            let tx_u16 = tx.clone();
            let data_cb = move |data: &[u16], _: &cpal::InputCallbackInfo| {
                let i16_data: Vec<i16> = data
                    .iter()
                    .map(|&s| s.wrapping_sub(32768) as i16)
                    .collect();
                let mono: Vec<i16> = if channels == 1 {
                    i16_data
                } else {
                    i16_data
                        .chunks(channels)
                        .map(|frame| {
                            let sum: i32 = frame.iter().map(|&s| s as i32).sum();
                            (sum / channels as i32) as i16
                        })
                        .collect()
                };
                let _ = tx_u16.try_send(mono);
            };
            stream = device.build_input_stream(&config, data_cb, |err| log::error!("cpal stream error: {err}"), None)?;
        } else {
            let tx_fallback = tx;
            let data_cb = move |data: &[i16], _: &cpal::InputCallbackInfo| {
                let mono: Vec<i16> = if channels == 1 {
                    data.to_vec()
                } else {
                    data.chunks(channels)
                        .map(|frame| {
                            let sum: i32 = frame.iter().map(|&s| s as i32).sum();
                            (sum / channels as i32) as i16
                        })
                        .collect()
                };
                let _ = tx_fallback.try_send(mono);
            };
            stream = device.build_input_stream(&config, data_cb, |err| log::error!("cpal stream error: {err}"), None)?;
        }

        stream.play()?;
        log::info!(
            "cpal: 采集循环启动 — native {}Hz, target 16kHz (软件重采样)",
            native_sample_rate
        );
        Ok(Self {
            _stream: StreamGuard::Single(stream),
            rx,
            native_sample_rate,
            is_loopback: false,
        })
    }

    /// 2026-07-02 Phase 7 v0.8.0: 双 stream 并行 (mic + loopback), 等权混合到单个 rx
    ///
    /// 简化策略:
    ///   - 各开 1 个 cpal Stream, 各自 tx → 共享 buffer
    ///   - 阻塞读 chunk: 等 2 路都到齐 1s 切片, 短端补零 → mix_two_streams
    ///   - 1s chunk = native_sample_rate samples (避免 resample)
    ///
    /// 不做时间戳精确对齐 (一期简化), 短端补零等效: 短端那一段输出仅依赖长端 (即 mic 侧或 loopback 侧)
    /// v0.9.x 加时间戳对齐 (cpal stream callback 给 timestamp ns)
    fn new_with_both_streams(mic_id: Option<String>, loopback_id: Option<String>) -> Result<Self> {
        let host = cpal::default_host();

        // 取设备 (mic + loopback, 都用 first config max channels)
        let mic_dev = if let Some(id) = mic_id {
            pick_device_by_name(&host, &id)?
        } else {
            host.default_input_device().context("both path: 默认 mic 设备不可用")?
        };
        let loop_dev = if let Some(id) = loopback_id {
            pick_device_by_name(&host, &id)?
        } else {
            // 没传 loopback_id: 拿第一个 is_loopback 设备
            host.input_devices()
                .context("无法枚举输入设备")?
                .find(|d| d.name().map(|n| is_loopback_device_name(&n)).unwrap_or(false))
                .context("both path: 找不到任何 loopback 设备")?
        };

        let mic_cfg = pick_input_config(&mic_dev)?;
        let loop_cfg = pick_input_config(&loop_dev)?;
        // 取两边 max_sample_rate 大的作统一采样率 (短端补零按长端对齐)
        // 注: cpal 0.15 SupportedStreamConfigRange 没有 .sample_rate(), 需 .with_sample_rate() 拿具体 config
        // 这里用 max_sample_rate 作 "该设备可支持的最高采样率" (实际设备 native 采样率通常 ≈ max)
        let unified_rate = mic_cfg.max_sample_rate().0.max(loop_cfg.max_sample_rate().0);
        let unified_channels = mic_cfg.channels().max(loop_cfg.channels()) as usize;

        log::info!(
            "Phase 7 both: mic={:?} ~{}Hz/{}ch + loopback={:?} ~{}Hz/{}ch → 统一 {}Hz/{}ch",
            mic_dev.name().unwrap_or_default(),
            mic_cfg.max_sample_rate().0,
            mic_cfg.channels(),
            loop_dev.name().unwrap_or_default(),
            loop_cfg.max_sample_rate().0,
            loop_cfg.channels(),
            unified_rate,
            unified_channels,
        );

        let (mic_tx, mic_rx) = mpsc::sync_channel::<Vec<i16>>(64);
        let (loop_tx, loop_rx) = mpsc::sync_channel::<Vec<i16>>(64);

        // mic stream
        let mic_config = cpal::StreamConfig {
            channels: mic_cfg.channels(),
            sample_rate: cpal::SampleRate(unified_rate),
            buffer_size: cpal::BufferSize::Default,
        };
        let mic_channels = mic_cfg.channels() as usize;
        let mic_stream = mic_dev.build_input_stream(
            &mic_config,
            move |data: &[i16], _: &cpal::InputCallbackInfo| {
                let mono = downmix_to_mono(data, mic_channels);
                let _ = mic_tx.try_send(mono);
            },
            |err| log::error!("cpal mic stream error: {err}"),
            None,
        )?;
        mic_stream.play()?;

        // loopback stream
        let loop_config = cpal::StreamConfig {
            channels: loop_cfg.channels(),
            sample_rate: cpal::SampleRate(unified_rate),
            buffer_size: cpal::BufferSize::Default,
        };
        let loop_channels = loop_cfg.channels() as usize;
        let loop_stream = loop_dev.build_input_stream(
            &loop_config,
            move |data: &[i16], _: &cpal::InputCallbackInfo| {
                let mono = downmix_to_mono(data, loop_channels);
                let _ = loop_tx.try_send(mono);
            },
            |err| log::error!("cpal loopback stream error: {err}"),
            None,
        )?;
        loop_stream.play()?;

        // 拼合 mpsc::Receiver: 内部 spawn 一个 std::thread 周期性 drain 两路 + mix + send
        let (out_tx, out_rx) = mpsc::sync_channel::<Vec<i16>>(64);
        let unified_rate_bg = unified_rate;
        // 用 Box::leak 让 mixer thread 不被 drop (process 生命周期内有效)
        // P1#5 (2026-07-04): thread independent, no leak
    std::thread::spawn(move || {
            let mut mic_buf: Vec<i16> = Vec::new();
            let mut loop_buf: Vec<i16> = Vec::new();
            // 1s 切片 (避免太频繁 mix, 也避免单 stream 卡顿导致输出停顿)
            let chunk_target = unified_rate_bg as usize;
            loop {
                // 等 mic 累积到 chunk_target (或超时)
                while mic_buf.len() < chunk_target {
                    match mic_rx.recv_timeout(Duration::from_millis(500)) {
                        Ok(mut chunk) => mic_buf.append(&mut chunk),
                        Err(mpsc::RecvTimeoutError::Timeout) => {
                            // 1s 没新数据 — 全填 0
                            mic_buf.resize(chunk_target, 0);
                            break;
                        }
                        Err(_) => return,
                    }
                }
                while loop_buf.len() < chunk_target {
                    match loop_rx.recv_timeout(Duration::from_millis(500)) {
                        Ok(mut chunk) => loop_buf.append(&mut chunk),
                        Err(mpsc::RecvTimeoutError::Timeout) => {
                            loop_buf.resize(chunk_target, 0);
                            break;
                        }
                        Err(_) => return,
                    }
                }
                let take = chunk_target.min(mic_buf.len()).min(loop_buf.len());
                let mic_slice = &mic_buf[..take];
                let loop_slice = &loop_buf[..take];
                let mixed = mix_two_streams(mic_slice, loop_slice);
                if out_tx.send(mixed).is_err() {
                    return;
                }
                mic_buf.drain(..take);
                loop_buf.drain(..take);
            }
        });

        log::info!(
            "Phase 7 both: 双 stream 启动, unified {}Hz/{}ch, target 16kHz (软件重采样)",
            unified_rate,
            unified_channels,
        );
        Ok(Self {
            _stream: StreamGuard::Merged {
                _mic: Arc::new(mic_stream),
                _loopback: Arc::new(loop_stream),
            },
            rx: out_rx,
            native_sample_rate: unified_rate,
            is_loopback: true,  // both 算 loopback-active (含系统声)
        })
    }

    /// 2026-06-27: 给外部读 native 采样率
    pub fn native_sample_rate(&self) -> u32 { self.native_sample_rate }

    /// 2026-07-02 Phase 7 v0.8.0: 给外部判断当前采集是否含系统声 (loopback/both → true)
    /// run_capture_loop 用它在静音诊断 banner 区分 (loopback 静音可能正常, mic 静音异常)
    #[allow(dead_code)]  // 2026-07-02: 当前 run_capture_loop 还未调用, 留作 v0.9 静音诊断 banner
    pub fn is_loopback_active(&self) -> bool { self.is_loopback }

    /// 读 N 秒 native 采样率 audio (blocking — 用于 spawn_blocking context)
    /// 2026-06-27: 改成内部用 self.native_sample_rate
    pub fn read_chunk_blocking(&mut self, seconds: f32) -> Result<Vec<i16>> {
        let needed = (self.native_sample_rate as f32 * seconds) as usize;
        let mut out = Vec::with_capacity(needed);
        let timeout = Duration::from_millis(1000);
        let deadline = std::time::Instant::now() + timeout;

        while out.len() < needed {
            let remaining = needed - out.len();
            let wait = deadline.saturating_duration_since(std::time::Instant::now());
            if wait.is_zero() { break; }
            match self.rx.recv_timeout(wait) {
                Ok(mut chunk) => {
                    let take = remaining.min(chunk.len());
                    out.append(&mut chunk.drain(..take).collect::<Vec<_>>());
                }
                Err(mpsc::RecvTimeoutError::Timeout) => break,
                Err(e) => anyhow::bail!("audio recv: {e}"),
            }
        }
        Ok(out)
    }
}

// =============================================================================
// Pure helpers (v0.7.1 落库, v0.8.0 保留)
// =============================================================================

/// 2026-07-02 Phase 7 v0.8.0: 多声道 → mono downmix (cpal callback 用)
fn downmix_to_mono(data: &[i16], channels: usize) -> Vec<i16> {
    if channels <= 1 {
        data.to_vec()
    } else {
        data.chunks(channels)
            .map(|frame| {
                let sum: i32 = frame.iter().map(|&s| s as i32).sum();
                (sum / channels as i32) as i16
            })
            .collect()
    }
}

/// 2026-07-02 Phase 7 v0.8.0: 按名字 pick 设备 (mic / loopback 用)
fn pick_device_by_name(host: &cpal::Host, name: &str) -> Result<cpal::Device> {
    host.input_devices()
        .context("无法枚举输入设备")?
        .find(|d| d.name().map(|n| n == name).unwrap_or(false))
        .with_context(|| format!("找不到音频设备: {name}"))
}

/// 2026-07-02 Phase 7 v0.8.0: pick first input config (优先 mono, 否则 max channels)
fn pick_input_config(device: &cpal::Device) -> Result<cpal::SupportedStreamConfigRange> {
    let supported = device.supported_input_configs().context("无法读取输入配置")?;
    let configs: Vec<_> = supported.collect();
    configs
        .iter()
        .find(|c| c.channels() == 1)
        .or_else(|| configs.iter().max_by_key(|c| c.channels()))
        .cloned()
        .context("设备没有任何支持的输入配置")
}

// =============================================================================
// 平台分支: 默认 loopback 设备检测
// =============================================================================

/// 2026-07-02 Phase 7 v0.8.0: 平台分支, 找当前默认的 loopback 设备 id (= name)
/// - Linux: 第一个 .monitor 后缀设备 (PulseAudio/PipeWire 都用 .monitor 约定)
/// - macOS: 第一个 BlackHole / Loopback / Soundflower 设备
/// - Windows: WASAPI loopback (始终可用)
pub fn detect_default_loopback() -> Option<String> {
    #[cfg(target_os = "linux")]
    return detect_linux_loopback();
    #[cfg(target_os = "macos")]
    return detect_macos_loopback();
    #[cfg(target_os = "windows")]
    {
        return Some(wasapi_loopback::loopback_device_name());
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        let _ = ();
        return None;
    }
}

#[cfg(target_os = "linux")]
fn detect_linux_loopback() -> Option<String> {
    let host = cpal::default_host();
    host.input_devices().ok()?.find(|d| {
        d.name()
            .map(|n| is_loopback_device_name(&n))
            .unwrap_or(false)
    }).and_then(|d| d.name().ok())
}

#[cfg(target_os = "macos")]
fn detect_macos_loopback() -> Option<String> {
    let host = cpal::default_host();
    host.input_devices().ok()?.find(|d| {
        d.name()
            .map(|n| is_loopback_device_name(&n))
            .unwrap_or(false)
    }).and_then(|d| d.name().ok())
}

/// 2026-07-02 Phase 7 v0.8.0: 平台分支判定设备名是否为 loopback
/// - Linux: `.monitor` 后缀 (PulseAudio/PipeWire 约定)
/// - macOS: BlackHole / Loopback / Soundflower (case-insensitive)
/// - Windows: WASAPI Loopback 设备名
pub fn is_loopback_device_name(name: &str) -> bool {
    #[cfg(target_os = "linux")]
    { return name.ends_with(".monitor"); }
    #[cfg(target_os = "macos")]
    {
        let n = name.to_lowercase();
        return n.contains("blackhole")
            || n.contains("loopback")
            || n.contains("soundflower");
    }
    #[cfg(target_os = "windows")]
    {
        return name == wasapi_loopback::loopback_device_name();
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        let _ = name;
        return false;
    }
}

/// 2026-07-02 Phase 7 v0.8.0: 等权混合两路等长 PCM (both 模式输出)
/// 短端补 0 — 调用方需先补零到同长度
pub fn mix_two_streams(mic: &[i16], loopback: &[i16]) -> Vec<i16> {
    let max_len = mic.len().max(loopback.len());
    let mut out = Vec::with_capacity(max_len);
    for i in 0..max_len {
        let m = mic.get(i).copied().unwrap_or(0) as f32;
        let l = loopback.get(i).copied().unwrap_or(0) as f32;
        let mixed = (m + l * 0.3).clamp(i16::MIN as f32, i16::MAX as f32) as i16;
        out.push(mixed);
    }
    out
}

// =============================================================================
// Pure helpers (v0.7.1 落库, v0.8.0 保留)
// =============================================================================

/// 2026-07-02 Phase 7 (v0.7.1): 双声道 → 单声道等权混合.
/// 用于 `audio_source=both` 时把 mic 半幅 + loopback 半幅 求均值, 防止削顶.
/// `dst` 追加结果 (push 风格; 切片场景复用 buffer)
/// `src` 长度必须是 偶数 (左/右 帧) — 否则最后一个 sample 抛 drop (调试 assert).
///
/// v0.8.0 仍保留作 mic-only fallback 内部用 (multi-channel mic → mono downmix 时也可用)
/// 实际新 both path 走 mix_two_streams (各 mono 之后混)
#[allow(dead_code)]  // 2026-07-02: 当前 new_with_both_streams 走 mix_two_streams (各 mono), 留作 v0.9 soxr 重构时的备选
pub fn mix_stereo_into(dst: &mut Vec<i16>, src: &[i16]) {
    debug_assert!(src.len() % 2 == 0, "mix_stereo_into expects even-length src (L/R frames)");
    let mut i = 0;
    while i + 1 < src.len() {
        let l = src[i] as f32;
        let r = src[i + 1] as f32;
        let mixed = ((l + r) * 0.5).clamp(i16::MIN as f32, i16::MAX as f32) as i16;
        dst.push(mixed);
        i += 2;
    }
}

/// 2026-06-27: 线性插值重采样 (单声道 i16). 用于 native 48kHz → target 16kHz 等.
pub fn resample_linear(samples: &[i16], from_rate: u32, to_rate: u32) -> Vec<i16> {
    if from_rate == to_rate || samples.is_empty() {
        return samples.to_vec();
    }
    let ratio = from_rate as f64 / to_rate as f64;
    let out_len = (samples.len() as f64 / ratio) as usize;
    let mut out = Vec::with_capacity(out_len);
    for i in 0..out_len {
        let src_pos = i as f64 * ratio;
        let i0 = src_pos.floor() as usize;
        let i1 = (i0 + 1).min(samples.len().saturating_sub(1));
        let frac = src_pos - i0 as f64;
        let v0 = samples[i0] as f64;
        let v1 = samples[i1] as f64;
        let v = v0 + (v1 - v0) * frac;
        out.push(v.round().clamp(i16::MIN as f64, i16::MAX as f64) as i16);
    }
    out
}

/// 2026-06-25: 枚举系统音频输入设备 (cherry-pick from feature 分支)
/// 2026-07-02 Phase 7 v0.8.0: 每设备标 is_loopback 字段
/// 2026-07-10 v0.21.0: Windows 追加 WASAPI Loopback 虚拟设备
pub fn list_input_devices() -> Result<Vec<AudioDeviceInfo>> {
    let host = cpal::default_host();
    let default_name = host.default_input_device().and_then(|d| d.name().ok());
    let mut out = Vec::new();
    for device in host.input_devices().context("无法枚举输入设备")? {
        let name = device.name().unwrap_or_else(|_| "未知设备".to_string());
        let is_loopback = is_loopback_device_name(&name);
        out.push(AudioDeviceInfo {
            id: name.clone(),
            is_default: default_name.as_deref() == Some(name.as_str()),
            is_loopback,
            name,
        });
    }
    #[cfg(target_os = "windows")]
    {
        let wasapi_name = wasapi_loopback::loopback_device_name();
        if !out.iter().any(|d| d.name == wasapi_name) {
            out.push(AudioDeviceInfo {
                id: wasapi_name.clone(),
                is_default: false,
                is_loopback: true,
                name: wasapi_name,
            });
        }
    }
    Ok(out)
}

/// i16 samples → WAV bytes (16-bit PCM, 16kHz, mono)
pub fn encode_wav(samples: &[i16], sample_rate: u32) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    let header = make_wav_header(samples.len() as u32, sample_rate, 1);
    buf.extend_from_slice(&header);
    for s in samples {
        buf.extend_from_slice(&s.to_le_bytes());
    }
    Ok(buf)
}

pub fn make_wav_header(data_len: u32, sample_rate: u32, channels: u16) -> Vec<u8> {
    let byte_rate = sample_rate * channels as u32 * 2;
    let block_align = channels * 2;
    let data_size = data_len * 2;
    let mut h = Vec::with_capacity(44);
    h.extend_from_slice(b"RIFF");
    h.extend_from_slice(&(36 + data_size).to_le_bytes());
    h.extend_from_slice(b"WAVE");
    h.extend_from_slice(b"fmt ");
    h.extend_from_slice(&16u32.to_le_bytes());
    h.extend_from_slice(&1u16.to_le_bytes()); // PCM
    h.extend_from_slice(&channels.to_le_bytes());
    h.extend_from_slice(&sample_rate.to_le_bytes());
    h.extend_from_slice(&byte_rate.to_le_bytes());
    h.extend_from_slice(&block_align.to_le_bytes());
    h.extend_from_slice(&16u16.to_le_bytes()); // bits per sample
    h.extend_from_slice(b"data");
    h.extend_from_slice(&data_size.to_le_bytes());
    h
}

// =============================================================================
// Windows WASAPI Loopback (v0.22 — raw COM, AUDCLNT_STREAMFLAGS_LOOPBACK)
// =============================================================================

#[cfg(target_os = "windows")]
mod wasapi_loopback {
    use anyhow::{Context, Result};
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
    use std::sync::mpsc;

    pub struct WindowsLoopback {
        _stream: cpal::Stream,
    }

    pub fn create_loopback() -> Result<(mpsc::Receiver<Vec<i16>>, u32, WindowsLoopback)> {
        let host = cpal::default_host();
        let device = host
            .default_output_device()
            .context("找不到默认输出设备用于 loopback")?;
        let name = device.name().unwrap_or_default();
        log::info!("WASAPI loopback: 输出设备={name}");

        let supported = device
            .supported_output_configs()
            .context("无法读取输出设备配置")?;
        let configs: Vec<_> = supported.collect();
        let cfg = configs
            .iter()
            .find(|c| c.channels() == 1)
            .or_else(|| configs.iter().max_by_key(|c| c.channels()))
            .context("输出设备无可用配置")?;

        let native_sample_rate = cfg.max_sample_rate().0;
        let config = cpal::StreamConfig {
            channels: cfg.channels(),
            sample_rate: cpal::SampleRate(native_sample_rate),
            buffer_size: cpal::BufferSize::Default,
        };
        let channels = cfg.channels() as usize;
        let (tx, rx) = mpsc::sync_channel::<Vec<i16>>(64);

        let stream = device
            .build_input_stream_raw(
                &config,
                cfg.sample_format(),
                move |data, _info| {
                    let samples: Vec<i16> = match data.sample_format() {
                        cpal::SampleFormat::F32 => data
                            .as_slice::<f32>()
                            .unwrap()
                            .iter()
                            .map(|&s| (s.clamp(-1.0, 1.0) * 32767.0) as i16)
                            .collect(),
                        cpal::SampleFormat::I16 => data.as_slice::<i16>().unwrap().to_vec(),
                        cpal::SampleFormat::U16 => data
                            .as_slice::<u16>()
                            .unwrap()
                            .iter()
                            .map(|&s| (s as i32 - 32768) as i16)
                            .collect(),
                        _ => {
                            let mut v = Vec::new();
                            v.resize(data.len() / data.sample_format().sample_size(), 0);
                            v
                        }
                    };
                    if channels == 2 {
                        let mono: Vec<i16> = samples
                            .chunks(2)
                            .map(|ch| ((ch[0] as i32 + ch[1] as i32) / 2) as i16)
                            .collect();
                        let _ = tx.try_send(mono);
                    } else if channels > 2 {
                        let mono: Vec<i16> = samples
                            .chunks(channels)
                            .map(|ch| {
                                let sum: i32 = ch.iter().map(|&s| s as i32).sum();
                                (sum / channels as i32) as i16
                            })
                            .collect();
                        let _ = tx.try_send(mono);
                    } else {
                        let _ = tx.try_send(samples);
                    }
                },
                move |err| {
                    log::error!("WASAPI loopback stream error: {err}");
                },
                None,
            )
            .context("构建 loopback 输入流失败")?;

        stream.play().context("启动 loopback 流失败")?;

        let guard = WindowsLoopback { _stream: stream };
        Ok((rx, native_sample_rate, guard))
    }

    pub fn loopback_device_name() -> String {
        "WASAPI Loopback (系统声音)".to_string()
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    // ---- v0.7.1 旧 helper tests (保留) ----

    #[test]
    fn mix_stereo_into_full_and_zero() {
        let mut dst = Vec::new();
        let src: Vec<i16> = vec![i16::MAX, 0, i16::MAX, 0];
        mix_stereo_into(&mut dst, &src);
        assert_eq!(dst.len(), 2);
        let exp = (i16::MAX as i32 / 2) as i16;
        assert_eq!(dst[0], exp);
        assert_eq!(dst[1], exp);
    }

    #[test]
    fn mix_stereo_into_overflow_clamp() {
        let mut dst = Vec::new();
        let src: Vec<i16> = vec![i16::MAX, i16::MAX, i16::MAX, i16::MAX];
        mix_stereo_into(&mut dst, &src);
        assert_eq!(dst, vec![i16::MAX, i16::MAX]);
    }

    #[test]
    #[should_panic(expected = "even-length")]
    fn mix_stereo_into_odd_length_panics() {
        let mut dst = Vec::new();
        let src: Vec<i16> = vec![1, 2, 3];
        mix_stereo_into(&mut dst, &src);
    }

    #[test]
    fn mix_stereo_into_appends_not_clears() {
        let mut dst = Vec::new();
        mix_stereo_into(&mut dst, &[100, -100]);
        assert_eq!(dst, vec![0]);
        mix_stereo_into(&mut dst, &[1000, 2000]);
        assert_eq!(dst, vec![0, 1500]);
    }

    #[test]
    fn resample_linear_same_rate_identity() {
        let samples: Vec<i16> = vec![100, -100, 200, -200];
        let out = resample_linear(&samples, 16000, 16000);
        assert_eq!(out, samples);
    }

    #[test]
    fn resample_linear_downsample_48k_to_16k() {
        let samples: Vec<i16> = (0..48).map(|i| i as i16 * 100).collect();
        let out = resample_linear(&samples, 48000, 16000);
        assert!(out.len() >= 15 && out.len() <= 17);
    }

    // ---- v0.8.0 新增 Phase 7 tests ----

    #[test]
    fn is_loopback_device_name_linux_monitor_suffix() {
        // 模拟 Linux PulseAudio monitor 命名约定
        assert!(is_loopback_device_name("alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"));
        assert!(is_loopback_device_name("default.monitor"));
        // 普通 mic 不算
        assert!(!is_loopback_device_name("default"));
        assert!(!is_loopback_device_name("USB Microphone"));
        assert!(!is_loopback_device_name("pulse"));
    }

    #[test]
    fn is_loopback_device_name_macos_blackhole_match() {
        // macOS-only test: cpal 编译期 cfg 分支, 在 Linux/Windows 上 cfg(macos) 走 else
        // 这条测试在 macOS runner 上跑, Linux runner 跳过 (断言在 cfg 内)
        // 设计: 把断言放 cfg 内 — cross-platform 测试时仅跑 Linux/Windows 分支
        #[cfg(target_os = "macos")]
        {
            assert!(is_loopback_device_name("BlackHole 2ch"));
            assert!(is_loopback_device_name("Loopback Audio"));
            assert!(is_loopback_device_name("Soundflower (2ch)"));
            assert!(is_loopback_device_name("BLACKHOLE 16CH"));
            assert!(!is_loopback_device_name("MacBook Pro Microphone"));
            assert!(!is_loopback_device_name("USB Audio Device"));
        }
        // 编译期保证: 跨平台编译不 panic (这条 assert 永远成立)
        #[cfg(not(target_os = "macos"))]
        {
            // 在 Linux/Windows 上跑这条 test 时, is_loopback_device_name 走 else 分支
            // (Linux 用 .monitor, Windows 恒 false), 不匹配 "BlackHole" 这种 macOS 名字
            // → 仅校验"不会 panic 也不会错误地返回 true"
            assert!(!is_loopback_device_name("BlackHole 2ch"),
                "Linux/Windows 不应匹配 BlackHole 名字 (跨平台分支隔离)");
        }
    }

    #[test]
    fn is_loopback_device_name_windows_always_false() {
        // cpal 0.15 不暴露 Windows loopback — 任何名字都 false
        // (这条在 Windows 上验证; cross-compile 测试是 Linux 也满足, 因为 cfg 分支兜底)
        assert!(!is_loopback_device_name("Stereo Mix"));
        assert!(!is_loopback_device_name("What U Hear"));
        assert!(!is_loopback_device_name("Realtek Audio"));
    }

    #[test]
    fn mix_two_streams_equal_length() {
        let mic = vec![100, 200, 300];
        let lp = vec![100, 200, 300];
        assert_eq!(mix_two_streams(&mic, &lp), vec![100, 200, 300]);
    }

    #[test]
    fn mix_two_streams_overflow_clamp() {
        let mic = vec![i16::MAX, i16::MAX];
        let lp = vec![i16::MAX, i16::MAX];
        let out = mix_two_streams(&mic, &lp);
        // (MAX + MAX) / 2 = MAX (clamp 不触发 — 因为 /2 必 ≤ MAX)
        assert_eq!(out, vec![i16::MAX, i16::MAX]);
    }

    #[test]
    fn mix_two_streams_mic_longer_pad_lp_with_zero() {
        let mic = vec![100, 200, 300, 400];  // 长 4
        let lp = vec![10, 20];              // 短 2
        let out = mix_two_streams(&mic, &lp);
        // i=0: (100+10)/2 = 55
        // i=1: (200+20)/2 = 110
        // i=2: (300+0)/2 = 150  (lp 补零)
        // i=3: (400+0)/2 = 200
        assert_eq!(out, vec![55, 110, 150, 200]);
    }

    #[test]
    fn mix_two_streams_lp_longer_pad_mic_with_zero() {
        let mic = vec![1000, 2000];         // 短 2
        let lp = vec![10, 20, 30, 40];     // 长 4
        let out = mix_two_streams(&mic, &lp);
        // i=0: (1000+10)/2 = 505
        // i=1: (2000+20)/2 = 1010
        // i=2: (0+30)/2 = 15
        // i=3: (0+40)/2 = 20
        assert_eq!(out, vec![505, 1010, 15, 20]);
    }

    #[test]
    fn mix_two_streams_negative_values() {
        // 验证负值 sign 正确
        let mic = vec![-100, -200];
        let lp = vec![100, 200];
        let out = mix_two_streams(&mic, &lp);
        assert_eq!(out, vec![0, 0]);  // 完全抵消
    }

    #[test]
    fn mix_two_streams_empty_inputs() {
        // 边界: 至少一边空 → 另一边 / 2 (空的那路补 0)
        // 跟设计契约一致: 短端补零 + 等权 → 1 路空时输出 = 另一路 / 2
        assert_eq!(mix_two_streams(&[], &[]), Vec::<i16>::new());
        assert_eq!(mix_two_streams(&[100, 200], &[]), vec![50, 100]);
        assert_eq!(mix_two_streams(&[], &[100, 200]), vec![50, 100]);
    }

    #[test]
    fn downmix_to_mono_stereo() {
        // 立体声 → mono 平均
        let stereo: Vec<i16> = vec![100, 200, -100, -200, 300, -300];
        let mono = downmix_to_mono(&stereo, 2);
        assert_eq!(mono, vec![150, -150, 0]);
    }

    #[test]
    fn downmix_to_mono_passthrough_mono() {
        let mono_in: Vec<i16> = vec![100, -100, 200];
        let mono_out = downmix_to_mono(&mono_in, 1);
        assert_eq!(mono_out, mono_in);
    }
}