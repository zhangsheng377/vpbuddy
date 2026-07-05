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
///
/// 2026-07-01 ADR-0021: 加 audio_source 参数 (?audio_source=microphone|loopback|both),
/// 老客户端不传 → 默认 microphone (向后兼容).
pub async fn create_meeting(gpu_url: &str, audio_source: &str) -> Result<String> {
    let url = format!(
        "{}/api/meetings/stream_start?audio_source={}",
        gpu_url,
        urlencoding::encode(audio_source)
    );
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

/// 2026-07-01 ADR-0022: 复用 UI 选/建的 meeting_id 在服务端 init 会议.
///
/// 调 POST /api/meetings/stream_start?meeting_id=XXX&audio_source=YYY
/// 服务端: 若 XXX 已存在 → 复用 (返回原 meeting_id), 若不存在 → 用 XXX 创建新 state.
/// 返回服务端确认的 meeting_id (正常 = XXX 自身).
pub async fn init_meeting(gpu_url: &str, meeting_id: &str, audio_source: &str) -> Result<String> {
    let url = format!(
        "{}/api/meetings/stream_start?meeting_id={}&audio_source={}",
        gpu_url,
        urlencoding::encode(meeting_id),
        urlencoding::encode(audio_source)
    );
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

#[cfg(test)]
mod tests {
    use super::*;

    // ── TranscriptSegment 测试 ──

    #[test]
    fn test_transcript_segment_serde_roundtrip() {
        let seg = TranscriptSegment {
            start_sec: 1.5,
            end_sec: 3.2,
            text: "你好世界".to_string(),
            speaker_id: "SPEAKER_01".to_string(),
            chunk_index: 0,
        };
        let json = serde_json::to_string(&seg).unwrap();
        let deserialized: TranscriptSegment = serde_json::from_str(&json).unwrap();
        assert!((deserialized.start_sec - 1.5).abs() < 1e-6);
        assert!((deserialized.end_sec - 3.2).abs() < 1e-6);
        assert_eq!(deserialized.text, "你好世界");
        assert_eq!(deserialized.speaker_id, "SPEAKER_01");
        assert_eq!(deserialized.chunk_index, 0);
    }

    #[test]
    fn test_transcript_segment_default_chunk_index() {
        // chunk_index has #[serde(default)], so missing field defaults to 0
        let json = r#"{
            "start_sec": 0.0,
            "end_sec": 1.0,
            "text": "hello",
            "speaker_id": "SPK_00"
        }"#;
        let seg: TranscriptSegment = serde_json::from_str(json).unwrap();
        assert_eq!(seg.chunk_index, 0);
    }

    #[test]
    fn test_transcript_segment_debug() {
        let seg = TranscriptSegment {
            start_sec: 0.0,
            end_sec: 1.0,
            text: "test".to_string(),
            speaker_id: "SPK_00".to_string(),
            chunk_index: 0,
        };
        let debug_str = format!("{:?}", seg);
        assert!(debug_str.contains("start_sec"));
        assert!(debug_str.contains("test"));
    }

    // ── unix_now_secs 测试 ──

    #[test]
    fn test_unix_now_secs_positive() {
        let now = unix_now_secs();
        // Should be > Jan 1, 2025 (1735689600) if system clock is reasonable
        assert!(now > 1_735_689_600.0, "unix timestamp should be > 2025-01-01");
    }

    #[test]
    fn test_unix_now_secs_monotonic() {
        let t1 = unix_now_secs();
        std::thread::sleep(std::time::Duration::from_millis(5));
        let t2 = unix_now_secs();
        assert!(t2 >= t1, "unix_now_secs should be monotonic");
    }

    // ── URL 构建逻辑测试 (通过 mockito mock HTTP 响应) ──

    #[tokio::test]
    async fn test_create_meeting_url_basic() {
        let mut server = mockito::Server::new_async().await;

        let mock = server.mock("POST", "/api/meetings/stream_start?audio_source=microphone")
            .with_status(200)
            .with_body(r#"{"meeting_id": "test-meeting-001"}"#)
            .create_async()
            .await;

        let result = create_meeting(&server.url(), "microphone").await;
        assert!(result.is_ok(), "create_meeting should succeed: {:?}", result.err());
        assert_eq!(result.unwrap(), "test-meeting-001");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_create_meeting_url_encoding() {
        let mut server = mockito::Server::new_async().await;

        // urlencoding::encode("loopback") = "loopback" (no special chars)
        // urlencoding::encode("mic & line") = "mic+%26+line"
        let mock = server.mock("POST", "/api/meetings/stream_start?audio_source=mic+%26+line")
            .with_status(200)
            .with_body(r#"{"meeting_id": "enc-test"}"#)
            .create_async()
            .await;

        let result = create_meeting(&server.url(), "mic & line").await;
        assert!(result.is_ok(), "create_meeting with encoded source should succeed: {:?}", result.err());
        assert_eq!(result.unwrap(), "enc-test");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_create_meeting_http_error() {
        let mut server = mockito::Server::new_async().await;

        let mock = server.mock("POST", mockito::Matcher::Any)
            .with_status(500)
            .with_body("Internal Server Error")
            .create_async()
            .await;

        let result = create_meeting(&server.url(), "microphone").await;
        assert!(result.is_err(), "create_meeting should fail on 500");
        let err_msg = format!("{}", result.err().unwrap());
        assert!(err_msg.contains("500"), "error should mention HTTP 500");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_create_meeting_missing_meeting_id() {
        let mut server = mockito::Server::new_async().await;

        let mock = server.mock("POST", mockito::Matcher::Any)
            .with_status(200)
            .with_body(r#"{"status": "ok"}"#)  // no meeting_id field
            .create_async()
            .await;

        let result = create_meeting(&server.url(), "microphone").await;
        assert!(result.is_err(), "create_meeting should fail when meeting_id is missing");
        let err_msg = format!("{}", result.err().unwrap());
        assert!(err_msg.contains("no meeting_id"), "error should mention missing meeting_id");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_init_meeting_url_basic() {
        let mut server = mockito::Server::new_async().await;

        // URL: /api/meetings/stream_start?meeting_id=existing-999&audio_source=loopback
        let mock = server.mock("POST", "/api/meetings/stream_start?meeting_id=existing-999&audio_source=loopback")
            .with_status(200)
            .with_body(r#"{"meeting_id": "existing-999"}"#)
            .create_async()
            .await;

        let result = init_meeting(&server.url(), "existing-999", "loopback").await;
        assert!(result.is_ok(), "init_meeting should succeed: {:?}", result.err());
        assert_eq!(result.unwrap(), "existing-999");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_init_meeting_url_encoding_special_chars() {
        let mut server = mockito::Server::new_async().await;

        // meeting_id = "my meeting/with#chars" 会 URL 编码
        // urlencoding::encode("my meeting/with#chars") = "my%20meeting%2Fwith%23chars"
        let encoded = urlencoding::encode("my meeting/with#chars");
        let mock_path = format!("/api/meetings/stream_start?meeting_id={}&audio_source=loopback", encoded);
        let mock = server.mock("POST", mock_path.as_str())
            .with_status(200)
            .with_body(r#"{"meeting_id": "my meeting/with#chars"}"#)
            .create_async()
            .await;

        let result = init_meeting(&server.url(), "my meeting/with#chars", "loopback").await;
        assert!(result.is_ok(), "init_meeting with special chars should succeed: {:?}", result.err());
        assert_eq!(result.unwrap(), "my meeting/with#chars");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_init_meeting_http_error() {
        let mut server = mockito::Server::new_async().await;

        let mock = server.mock("POST", mockito::Matcher::Any)
            .with_status(404)
            .with_body("Not Found")
            .create_async()
            .await;

        let result = init_meeting(&server.url(), "nonexistent", "microphone").await;
        assert!(result.is_err(), "init_meeting should fail on 404");
        let err_msg = format!("{}", result.err().unwrap());
        assert!(err_msg.contains("404"), "error should mention HTTP 404");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_init_meeting_missing_meeting_id() {
        let mut server = mockito::Server::new_async().await;

        let mock = server.mock("POST", mockito::Matcher::Any)
            .with_status(200)
            .with_body(r#"{"ok": true}"#)  // no meeting_id field
            .create_async()
            .await;

        let result = init_meeting(&server.url(), "test-id", "microphone").await;
        assert!(result.is_err(), "init_meeting should fail when meeting_id is missing");
        let err_msg = format!("{}", result.err().unwrap());
        assert!(err_msg.contains("no meeting_id"), "error should mention missing meeting_id");

        mock.assert_async().await;
    }

    #[tokio::test]
    async fn test_create_and_init_meeting_different_audio_sources() {
        // 验证 different audio_source values 生成不同 URL
        let mut server = mockito::Server::new_async().await;

        let mock_mic = server.mock("POST", "/api/meetings/stream_start?audio_source=microphone")
            .with_status(200)
            .with_body(r#"{"meeting_id": "mic-test"}"#)
            .expect_at_least(0)  // 0 or more — we only assert that the correct mock had the right path
            .create_async()
            .await;

        let mock_loop = server.mock("POST", "/api/meetings/stream_start?audio_source=loopback")
            .with_status(200)
            .with_body(r#"{"meeting_id": "loop-test"}"#)
            .expect_at_least(0)
            .create_async()
            .await;

        let mock_both = server.mock("POST", "/api/meetings/stream_start?audio_source=both")
            .with_status(200)
            .with_body(r#"{"meeting_id": "both-test"}"#)
            .create_async()
            .await;

        let result = create_meeting(&server.url(), "both").await;
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "both-test");

        mock_both.assert_async().await;
        // 不 assert mic 和 loop — 保证 both 的 mock 调用了即可
    }

    // ── upload_chunk 不在此处测 (需要真实 WAV 数据和运行 SSE 服务) ──
    // upload_chunk 的测试建议在 e2e/integration 层面覆盖。
}
