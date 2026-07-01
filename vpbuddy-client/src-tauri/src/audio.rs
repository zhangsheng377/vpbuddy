// 跨平台音频采集 (cpal 抽象)
// 设计: 16kHz mono i16 PCM → 返回 Vec<i16>
//       Linux/macOS/Windows 自动用对应后端

use anyhow::{Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::Serialize;
use std::sync::mpsc;
use std::time::Duration;

#[derive(Debug, Serialize)]
pub struct AudioDeviceInfo {
    pub id: String,
    pub name: String,
    pub is_default: bool,
}

pub struct AudioCapture {
    _stream: cpal::Stream,
    rx: mpsc::Receiver<Vec<i16>>,
    /// 2026-06-27: 设备原生采样率, read_chunk_blocking 用来重采样到目标 16kHz
    native_sample_rate: u32,
}

impl AudioCapture {
    pub fn new() -> Result<Self> {
        Self::new_with_device(None, "microphone")
    }

    /// 2026-07-02 Phase 7: 公开入口, 路由 audio_source
    /// - "microphone" (默认): 用系统默认输入设备 (现行实现)
    /// - "loopback" / "both": 跨平台 cpal 内录暂未实现 (TODO)
    ///   Fallback 策略: warn log + 切到 microphone 兜底, 保证不破 v0.7.x 录音
    /// - 未知值: warn + 兜底 microphone
    ///
    /// 之所以 KISS 兜底: v0.7.1 是不破 mic 主流程的最小变更.
    /// 真 cross-platform loopback (Linux PulseAudio mon / macOS BlackHole / Windows WASAPI)
    /// 是 v0.8.x 的独立 PR (用 cpal 支持 host-specific loopback API).
    pub fn new_with_source(device_id: Option<String>, audio_source: &str) -> Result<Self> {
        match audio_source {
            "microphone" | "mic" => Self::new_with_device(device_id, "microphone"),
            "loopback" => {
                log::warn!(
                    "audio_source=loopback 暂未实现 (Phase 7 stub, v0.8 跟随平台分支) — 兜底用 microphone"
                );
                Self::new_with_device(device_id, "microphone")
            }
            "both" => {
                log::warn!(
                    "audio_source=both 暂未实现 (Phase 7 stub, v0.8 跟随平台分支) — 兜底用 microphone"
                );
                Self::new_with_device(device_id, "microphone")
            }
            other => {
                log::warn!(
                    "未知 audio_source={other:?}, 期望 microphone|loopback|both — 兜底用 microphone"
                );
                Self::new_with_device(device_id, "microphone")
            }
        }
    }

    /// 2026-06-25: cherry-pick from feature/requirements-architecture-update
    /// device_id=None 用系统默认, 否则按 name 匹配
    /// 2026-06-27: 用设备原生采样率 (不写死 16kHz, Realtek/WASAPI 不支持会 fail)
    ///            然后在 read_chunk_blocking 重采样到目标 16kHz
    /// 2026-07-02 Phase 7: 内部多一个 ignored 参 (audio_source), 仅用于 log.
    ///                    Public API 走 new_with_source(), 此处保留向后兼容签名.
    pub fn new_with_device(device_id: Option<String>, audio_source: &str) -> Result<Self> {
        log::debug!("audio_source={audio_source} (本期仅 log, mic path)");
        Self::self_new_with_device_inner(device_id)
    }

    /// 实际设备 init (v0.7.0 原 logic 拆出, 内部用)
    fn self_new_with_device_inner(device_id: Option<String>) -> Result<Self> {
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
        // supported_input_configs 第一个 = 设备偏好的默认配置
        let supported = device
            .supported_input_configs()
            .context("无法读取设备支持的输入配置")?;
        // 收集为 Vec 避免迭代器 borrow 冲突 (cpal 0.15 的 API 需要)
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
        let stream = device.build_input_stream(
            &config,
            move |data: &[i16], _: &cpal::InputCallbackInfo| {
                // 多声道 → 单声道 downmix
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
                let _ = tx.try_send(mono);
            },
            |err| log::error!("cpal stream error: {err}"),
            None,
        )?;

        stream.play()?;
        log::info!(
            "cpal: 采集循环启动 — native {}Hz, target 16kHz (软件重采样)",
            native_sample_rate
        );
        Ok(Self { _stream: stream, rx, native_sample_rate })
    }

    /// 2026-06-27: 给外部读 native 采样率 (主循环 resample 用)
    pub fn native_sample_rate(&self) -> u32 { self.native_sample_rate }

    /// 读 N 秒 native 采样率 audio (blocking — 用于 spawn_blocking context)
    /// ⚠️ 不能 .await — cpal::Stream 持有 *mut () 跨 await 不是 Send.
    /// 必须在 spawn_blocking 跑, 跟 run_capture_loop 的设计配套.
    /// 取代之前的 async fn read_chunk (持有 cpal::Stream 跨 await 不是 Send, 已废)
    /// 2026-06-27: 改成内部用 self.native_sample_rate, 不再传参
    ///            (主循环想要 16kHz 切片时, 调用 resample_to 单独处理)
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

/// 2026-07-02 Phase 7 (v0.7.1): 双声道 → 单声道等权混合.
/// 用于 `audio_source=both` 时把 mic 半幅 + loopback 半幅 求均值, 防止削顶.
/// `dst` 追加结果 (push 风格; 切片场景复用 buffer)
/// `src` 长度必须是 偶数 (左/右 帧) — 否则最后一个 sample 抛 drop (调试 assert).
///
/// 注意: 这是 KISS 平均; 真实产品应该用 soxr + bytes_per_sample 重采样 + gain control.
/// v0.7.1 仅 stub 落库, 真实 both path 是 v0.8 跟随平台 PR.
pub fn mix_stereo_into(dst: &mut Vec<i16>, src: &[i16]) {
    debug_assert!(src.len() % 2 == 0, "mix_stereo_into expects even-length src (L/R frames)");
    let mut i = 0;
    while i + 1 < src.len() {
        let l = src[i] as i32;
        let r = src[i + 1] as i32;
        let mixed = ((l + r) / 2).clamp(i16::MIN as i32, i16::MAX as i32) as i16;
        dst.push(mixed);
        i += 2;
    }
}

/// 2026-06-27: 线性插值重采样 (单声道 i16). 用于 native 48kHz → target 16kHz 等.
/// 工业标准做法: cpal 用设备原生采样率, 客户端软件重采样到 funasr 期望的 16kHz.
/// 简单线性插值足够 funasr inference (它会自己提取特征), 不需要 sinc/polyphase.
pub fn resample_linear(samples: &[i16], from_rate: u32, to_rate: u32) -> Vec<i16> {
    if from_rate == to_rate || samples.is_empty() {
        return samples.to_vec();
    }
    let ratio = from_rate as f64 / to_rate as f64;  // >1 表示下采样
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
pub fn list_input_devices() -> Result<Vec<AudioDeviceInfo>> {
    let host = cpal::default_host();
    let default_name = host.default_input_device().and_then(|d| d.name().ok());
    let mut out = Vec::new();
    for device in host.input_devices().context("无法枚举输入设备")? {
        let name = device.name().unwrap_or_else(|_| "未知设备".to_string());
        out.push(AudioDeviceInfo {
            id: name.clone(),
            is_default: default_name.as_deref() == Some(name.as_str()),
            name,
        });
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

// 2026-07-02 Phase 7 (v0.7.1) inline unit tests.
// 没有 mock cpal 的方便手段, 所以只测 pure helper `mix_stereo_into` + `resample_linear` 边界.
// 真实 e2e 录音在 install-client.sh + GPU 端集成测.

#[cfg(test)]
mod tests {
    use super::*;

    // 满幅左 + 0 右 → 半幅右 走 diff (-) 而不是 sum 反向 — 验证 sign 正确
    #[test]
    fn mix_stereo_into_full_and_zero() {
        let mut dst = Vec::new();
        let src: Vec<i16> = vec![i16::MAX, 0, i16::MAX, 0];
        mix_stereo_into(&mut dst, &src);
        assert_eq!(dst.len(), 2);
        let exp = (i16::MAX as i32 / 2) as i16;
        assert_eq!(dst[0], exp, "half-amplitude on full+zero");
        assert_eq!(dst[1], exp);
    }

    // 双满幅 → 应 clamp 到 i16::MAX 不溢出
    #[test]
    fn mix_stereo_into_overflow_clamp() {
        let mut dst = Vec::new();
        let src: Vec<i16> = vec![i16::MAX, i16::MAX, i16::MAX, i16::MAX];
        mix_stereo_into(&mut dst, &src);
        assert_eq!(dst, vec![i16::MAX, i16::MAX]);
    }

    // 奇数长度 → 断言失败 (debug_assert!)
    #[test]
    #[should_panic(expected = "even-length")]
    fn mix_stereo_into_odd_length_panics() {
        let mut dst = Vec::new();
        let src: Vec<i16> = vec![1, 2, 3]; // 3 = 奇 → panic
        mix_stereo_into(&mut dst, &src);
    }

    // dst push 风格: 多次调用累积
    #[test]
    fn mix_stereo_into_appends_not_clears() {
        let mut dst = Vec::new();
        mix_stereo_into(&mut dst, &[100, -100]); // 0
        assert_eq!(dst, vec![0]);
        mix_stereo_into(&mut dst, &[1000, 2000]); // 1500
        assert_eq!(dst, vec![0, 1500]);
    }

    // resample_linear 边界: 同采样率直通
    #[test]
    fn resample_linear_same_rate_identity() {
        let samples: Vec<i16> = vec![100, -100, 200, -200];
        let out = resample_linear(&samples, 16000, 16000);
        assert_eq!(out, samples);
    }

    // resample_linear 下采样: 48k → 16k (ratio=3)
    #[test]
    fn resample_linear_downsample_48k_to_16k() {
        let samples: Vec<i16> = (0..48).map(|i| i as i16 * 100).collect();
        let out = resample_linear(&samples, 48000, 16000);
        // ratio=3, 48 samples → ~16 samples
        assert!(out.len() >= 15 && out.len() <= 17,
                "expected ~16 samples for 48→16, got {}", out.len());
    }
}
