// GPU server 通信: 创建会议 + 上传 30s 切片 (multipart)

use anyhow::Result;
use reqwest::multipart;
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct TranscriptSegment {
    pub start_sec: f32,
    pub end_sec: f32,
    pub text: String,
    pub speaker_id: String,
    #[serde(default)]
    pub chunk_index: u64,
}

/// 在 GPU 端创建一个"长连接"会议, 后续 chunk 都 push 到这个 meeting
pub async fn create_meeting(gpu_url: &str) -> Result<String> {
    let url = format!("{}/api/meetings/stream_start", gpu_url);
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&serde_json::json!({"platform": "desktop_client"}))
        .send()
        .await?;
    let body: serde_json::Value = resp.json().await?;
    Ok(body["meeting_id"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("no meeting_id in response"))?
        .to_string())
}

/// 上传一个 30s WAV 切片 → GPU 端 funasr 增量转写 → 返回所有新 segments
pub async fn upload_chunk(
    gpu_url: &str,
    meeting_id: &str,
    wav_data: Vec<u8>,
    chunk_index: u64,
    chunk_start_sec: f32,
    overlap_sec: f32,
) -> Result<Vec<TranscriptSegment>> {
    // 2026-06-25: 加 ?sync=false 让 server 立即返回 (不阻塞等 funasr + 6 docs)
    // 客户端通过 SSE /api/meetings/{id}/events 收 transcript-segment / state-update / doc-update
    let url = format!("{}/api/meetings/{}/stream_chunk?sync=false", gpu_url, meeting_id);
    let client = reqwest::Client::builder()
        // 2026-06-25: ?sync=false 模式 server 立即返回 (毫秒级), client 30s 够用
        .timeout(std::time::Duration::from_secs(30))
        .build()?;

    let mut last_err: Option<anyhow::Error> = None;
    let mut body: Option<serde_json::Value> = None;
    for attempt in 1..=3 {
        let part = multipart::Part::bytes(wav_data.clone())
            .file_name(format!("chunk-{chunk_index}.wav"))
            .mime_str("audio/wav")?;
        let form = multipart::Form::new()
            .part("audio", part)
            .text("chunk_index", chunk_index.to_string())
            .text("chunk_start_sec", format!("{chunk_start_sec:.3}"))
            .text("overlap_sec", format!("{overlap_sec:.3}"))
            .text("client_sent_at", format!("{:.3}", unix_now_secs()));
        match client.post(&url).multipart(form).send().await {
            Ok(resp) if resp.status().is_success() => {
                body = Some(resp.json().await?);
                break;
            }
            Ok(resp) => {
                let status = resp.status();
                let text = resp.text().await.unwrap_or_default();
                last_err = Some(anyhow::anyhow!("HTTP {status}: {text}"));
            }
            Err(e) => {
                last_err = Some(anyhow::anyhow!(e));
            }
        }
        let delay_ms = 500u64 * (1u64 << (attempt - 1));
        tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
    }

    let body = body.ok_or_else(|| last_err.unwrap_or_else(|| anyhow::anyhow!("上传失败")))?;

    // 解析所有 new_segments
    let mut segments = Vec::new();
    if let Some(arr) = body["new_segments"].as_array() {
        for seg in arr {
            segments.push(TranscriptSegment {
                start_sec: seg["start_sec"].as_f64().unwrap_or(0.0) as f32,
                end_sec: seg["end_sec"].as_f64().unwrap_or(0.0) as f32,
                text: seg["text"].as_str().unwrap_or("").to_string(),
                speaker_id: seg["speaker_id"]
                    .as_str()
                    .unwrap_or("SPEAKER_00")
                    .to_string(),
                chunk_index: seg["chunk_index"].as_u64().unwrap_or(chunk_index),
            });
        }
    }
    Ok(segments)
}

fn unix_now_secs() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}
