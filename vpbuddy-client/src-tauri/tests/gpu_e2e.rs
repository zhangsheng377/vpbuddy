//! Tauri 客户端 ↔ GPU server 端到端联调
//!
//! 旧 30s 切片模式 (stream_chunk) 已废弃，改为 WebSocket 实时 ASR (realtime_asr)。
//! 保留此文件作为 WS 模式 E2E 测试的模板，待重写。
//!
//! 跑法:
//!   GPU_URL=http://47.100.182.3:28765 cargo test --test gpu_e2e -- --ignored --nocapture

use std::env;
use std::time::Duration;
use vpbuddy_client_lib::audio::{encode_wav, make_wav_header};

fn gpu_url() -> Option<String> {
    env::var("GPU_URL").ok().or_else(|| {
        env::var("VPBUDDY_GPU_URL").ok()
    })
}

#[tokio::test]
#[ignore = "待重写为 WS 实时模式 E2E: stream_start → realtime_asr WS → docs"]
async fn e2e_stream_chunk_creates_6_docs() {
    let url = match gpu_url() {
        Some(u) => u,
        None => {
            eprintln!("跳过: 设 GPU_URL=http://192.168.10.63:8765 启用");
            return;
        }
    };

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(300))  // 2026-06-25: funasr + 6 docs in-process 首次加载模型可能要 120-200s, 提到 300s buffer
        .build()
        .unwrap();

    // 1. POST /api/meetings/stream_start
    eprintln!("[1/4] POST {}/api/meetings/stream_start", url);
    let resp = client.post(format!("{}/api/meetings/stream_start", url))
        .json(&serde_json::json!({"platform": "desktop_client_e2e_test"}))
        .send().await.expect("stream_start HTTP");
    assert!(resp.status().is_success(), "stream_start status: {}", resp.status());
    let body: serde_json::Value = resp.json().await.unwrap();
    let meeting_id = body["meeting_id"].as_str()
        .expect("meeting_id in response").to_string();
    eprintln!("[1/4] ✓ meeting_id = {}", meeting_id);

    // 2. 生成 0.5s 静音 WAV (16000 samples i16 = 32000 bytes)
    eprintln!("[2/4] 生成 0.5s 静音 WAV");
    let samples = vec![0i16; 8000];
    let wav = encode_wav(&samples, 16000).unwrap();
    eprintln!("[2/4] ✓ WAV size = {} bytes (header 44 + 16000 PCM)", wav.len());
    assert_eq!(wav.len(), 44 + 16000);
    // 验证 header magic
    assert_eq!(&wav[0..4], b"RIFF");
    assert_eq!(&wav[8..12], b"WAVE");

    // 3. multipart POST /api/meetings/{id}/stream_chunk
    eprintln!("[3/4] POST {}/api/meetings/{}/stream_chunk", url, meeting_id);
    let part = reqwest::multipart::Part::bytes(wav)
        .file_name("chunk.wav")
        .mime_str("audio/wav").unwrap();
    let form = reqwest::multipart::Form::new()
        .part("audio", part);
    let resp = client.post(format!("{}/api/meetings/{}/stream_chunk", url, meeting_id))
        .multipart(form)
        .send().await.expect("stream_chunk HTTP");
    let status = resp.status();
    let body_text = resp.text().await.unwrap_or_default();
    eprintln!("[3/4] status = {} body = {}", status, &body_text[..body_text.len().min(200)]);
    assert!(status.is_success() || status.as_u16() == 202,
            "stream_chunk status {}: {}", status, body_text);

    // 4. 等 controller 30s 周期 + 余量
    eprintln!("[4/4] 等待 35s 让 controller 触发 6 子 session");
    tokio::time::sleep(Duration::from_secs(35)).await;

    // 5. 验证 docs/{meeting_id}/ 下 6 个文件 (从服务端文档存储目录)
    // 通过 GET /api/meetings 查 — 简化版, 只验证 meeting 存在
    let resp = client.get(format!("{}/api/meetings", url))
        .send().await.expect("list meetings HTTP");
    assert!(resp.status().is_success());
    let body: serde_json::Value = resp.json().await.unwrap();
    let meetings = body["meetings"].as_array().expect("meetings array");
    eprintln!("[verify] /api/meetings returned {} meetings", meetings.len());
    // 修复 (2026-06-24): API 返回对象数组 [{meeting_id, ...}], 不是字符串数组
    let meeting_ids: Vec<&str> = meetings.iter()
        .filter_map(|m| m["meeting_id"].as_str())
        .collect();
    assert!(meeting_ids.contains(&meeting_id.as_str()),
            "刚创建的 meeting {} 应在列表里, 实际列表: {:?}", meeting_id, meeting_ids);

    eprintln!("✅ E2E 通过: stream_start → stream_chunk → meeting 注册成功");
    eprintln!("⚠️ 当前测试用 0.5s 静音 WAV, funasr 返回 0 segments → 6 docs 不写 (预期)");
    eprintln!("    真音频 (Phase B 联调 VP 笔记本) 才会有 segments → 6 docs 写盘");
    eprintln!("    验证命令: ls /home/zsd/vpbuddy/docs/{}/", meeting_id);
    eprintln!("    或推带内容的 WAV (e.g. TTS 生成的 5s 中文):");
    eprintln!("      python3 stream_client.py --gpu http://localhost:8765 --meeting {}", meeting_id);
}

#[test]
fn test_wav_header_magic_bytes() {
    // 验证 RIFF/WAVE/fmt /data 4 个 magic 都在
    let h = make_wav_header(100, 16000, 1);
    assert_eq!(&h[0..4], b"RIFF");
    assert_eq!(&h[8..12], b"WAVE");
    assert_eq!(&h[12..16], b"fmt ");
    assert_eq!(&h[36..40], b"data");
}