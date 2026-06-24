// GPU server 通信: 创建会议 + 上传 30s 切片 (multipart)

use anyhow::Result;
use reqwest::multipart;
use serde::Deserialize;

#[derive(Debug, Deserialize, Clone)]
pub struct TranscriptSegment {
    pub start_sec: f32,
    pub end_sec: f32,
    pub text: String,
    pub speaker_id: String,
}

/// 在 GPU 端创建一个"长连接"会议, 后续 chunk 都 push 到这个 meeting
pub async fn create_meeting(gpu_url: &str) -> Result<String> {
    let url = format!("{}/api/meetings/stream_start", gpu_url);
    let client = reqwest::Client::new();
    let resp = client.post(&url)
        .json(&serde_json::json!({"platform": "desktop_client"}))
        .send()
        .await?;
    let body: serde_json::Value = resp.json().await?;
    Ok(body["meeting_id"].as_str()
        .ok_or_else(|| anyhow::anyhow!("no meeting_id in response"))?
        .to_string())
}

/// 上传一个 30s WAV 切片 → GPU 端 funasr 增量转写 → 返回新 segments
pub async fn upload_chunk(gpu_url: &str, meeting_id: &str, wav_data: Vec<u8>) -> Result<TranscriptSegment> {
    let url = format!("{}/api/meetings/{}/stream_chunk", gpu_url, meeting_id);
    let part = multipart::Part::bytes(wav_data)
        .file_name("chunk.wav")
        .mime_str("audio/wav")?;
    let form = multipart::Form::new().part("audio", part);
    let client = reqwest::Client::new();
    let resp = client.post(&url)
        .multipart(form)
        .send()
        .await?;
    let body: serde_json::Value = resp.json().await?;
    // 取第一个新 segment
    let seg = &body["new_segments"][0];
    Ok(TranscriptSegment {
        start_sec: seg["start_sec"].as_f64().unwrap_or(0.0) as f32,
        end_sec: seg["end_sec"].as_f64().unwrap_or(0.0) as f32,
        text: seg["text"].as_str().unwrap_or("").to_string(),
        speaker_id: seg["speaker_id"].as_str().unwrap_or("SPEAKER_00").to_string(),
    })
}
