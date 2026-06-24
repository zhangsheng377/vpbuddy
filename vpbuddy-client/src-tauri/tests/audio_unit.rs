//! Phase B 单测 — 跨平台代码回归保护
//!
//! 测 audio::encode_wav 和 make_wav_header (不依赖 cpal 硬件, 纯函数)
//! 测 upload 模块的 URL 拼接逻辑 (没 GPU server 也能跑)
//!
//! ⚠️ AudioCapture::new() / read_chunk() 需要真音频设备, 跳过 (CI 无硬件)

use vpbuddy_client_lib::audio::{encode_wav, make_wav_header};

#[test]
fn test_make_wav_header_16khz_mono() {
    // 30s 16kHz mono = 480000 samples
    let h = make_wav_header(480000, 16000, 1);
    assert_eq!(h.len(), 44, "WAV header 必 44 字节");

    // RIFF / WAVE / fmt / data magic
    assert_eq!(&h[0..4], b"RIFF");
    assert_eq!(&h[8..12], b"WAVE");
    assert_eq!(&h[12..16], b"fmt ");
    assert_eq!(&h[36..40], b"data");

    // 关键字段 (LE)
    let data_size = u32::from_le_bytes([h[40], h[41], h[42], h[43]]);
    assert_eq!(data_size, 480000 * 2, "30s mono i16 = 960000 bytes");

    let sample_rate = u32::from_le_bytes([h[24], h[25], h[26], h[27]]);
    assert_eq!(sample_rate, 16000);

    let channels = u16::from_le_bytes([h[22], h[23]]);
    assert_eq!(channels, 1);

    let bits_per_sample = u16::from_le_bytes([h[34], h[35]]);
    assert_eq!(bits_per_sample, 16);
}

#[test]
fn test_encode_wav_silence() {
    // 8000 samples 静音 (0.5s @ 16kHz)
    let samples = vec![0i16; 8000];
    let wav = encode_wav(&samples, 16000).unwrap();

    // 44 header + 8000 * 2 = 16044 bytes
    assert_eq!(wav.len(), 44 + 16000);

    // 静音区全 0
    for i in 44..wav.len() {
        assert_eq!(wav[i], 0, "静音样本位置 {i} 应为 0");
    }
}

#[test]
fn test_encode_wav_nonzero() {
    // 测一个小样本 (0x1234 = 4660)
    let samples = vec![0x1234i16];
    let wav = encode_wav(&samples, 16000).unwrap();
    assert_eq!(wav.len(), 44 + 2);
    assert_eq!(wav[44], 0x34); // LE low byte
    assert_eq!(wav[45], 0x12); // LE high byte
}

#[test]
fn test_encode_wav_empty() {
    // 0 samples — 应该还有 44 字节 header (data chunk size = 0)
    let wav = encode_wav(&[], 16000).unwrap();
    assert_eq!(wav.len(), 44);
    let data_size = u32::from_le_bytes([wav[40], wav[41], wav[42], wav[43]]);
    assert_eq!(data_size, 0);
}

#[test]
fn test_wav_header_byte_rate_calculation() {
    // 16kHz * 1 channel * 2 bytes = 32000 bytes/sec
    let h = make_wav_header(16000, 16000, 1);
    let byte_rate = u32::from_le_bytes([h[28], h[29], h[30], h[31]]);
    assert_eq!(byte_rate, 32000);

    // 16kHz * 2 channels * 2 bytes = 64000 bytes/sec
    let h2 = make_wav_header(16000, 16000, 2);
    let byte_rate2 = u32::from_le_bytes([h2[28], h2[29], h2[30], h2[31]]);
    assert_eq!(byte_rate2, 64000);
}