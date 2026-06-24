// 跨平台音频采集 (cpal 抽象)
// 设计: 16kHz mono i16 PCM → 返回 Vec<i16>
//       Linux/macOS/Windows 自动用对应后端

use anyhow::{Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::sync::mpsc;
use std::time::Duration;

pub struct AudioCapture {
    _stream: cpal::Stream,
    rx: mpsc::Receiver<Vec<i16>>,
}

impl AudioCapture {
    pub fn new() -> Result<Self> {
        let host = cpal::default_host();
        let device = host.default_input_device()
            .context("找不到默认输入设备 (麦克风/loopback)")?;

        let config = cpal::StreamConfig {
            channels: 1,
            sample_rate: cpal::SampleRate(16000),
            buffer_size: cpal::BufferSize::Default,
        };

        let (tx, rx) = mpsc::sync_channel::<Vec<i16>>(64);
        let stream = device.build_input_stream(
            &config,
            move |data: &[i16], _: &cpal::InputCallbackInfo| {
                // 复制到 channel (小批量)
                let _ = tx.try_send(data.to_vec());
            },
            |err| log::error!("cpal stream error: {err}"),
            None,
        )?;

        stream.play()?;
        Ok(Self { _stream: stream, rx })
    }

    /// 读 N 秒的 audio (blocking — 用于 spawn_blocking context)
    /// ⚠️ 不能 .await — cpal::Stream 持有 *mut () 跨 await 不是 Send.
    /// 必须在 spawn_blocking 跑, 跟 run_capture_loop 的设计配套.
    /// 取代之前的 async fn read_chunk (持有 cpal::Stream 跨 await 不是 Send, 已废)
    pub fn read_chunk_blocking(&mut self, seconds: f32, sample_rate: u32) -> Result<Vec<i16>> {
        let needed = (sample_rate as f32 * seconds) as usize;
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
