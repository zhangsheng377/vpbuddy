// GPU server 通信: 创建会议 + WS 实时转写 (百炼)

use anyhow::Result;
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

// ── 百炼 WebSocket 实时转写 ──

/// 百炼 WS 实时转写客户端.
pub struct BailianWsHandle {
    write_half: tokio::sync::mpsc::Sender<Vec<u8>>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    recv_handle: tokio::task::JoinHandle<()>,
    _disconnected: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl BailianWsHandle {
    pub async fn connect(
        gpu_url: &str,
        meeting_id: &str,
        auth_token: &str,
        sample_rate: u32,
        on_transcript: impl Fn(String, f32, f32, bool) + Send + 'static,
        on_error: impl Fn(String) + Send + 'static,
    ) -> Result<Self> {
        let ws_url = gpu_url
            .replace("http://", "ws://")
            .replace("https://", "wss://");
        let url = format!(
            "{}/api/meetings/{}/realtime_asr?token={}",
            ws_url.trim_end_matches('/'),
            urlencoding::encode(meeting_id),
            urlencoding::encode(auth_token),
        );

        use tokio_tungstenite::connect_async;
        use tokio_tungstenite::tungstenite::Message;
        use futures_util::{SinkExt, StreamExt};

        let (ws_stream, _) = connect_async(&url).await?;
        let (mut write, mut read) = ws_stream.split();

        let start_msg = serde_json::json!({
            "type": "start",
            "format": "pcm",
            "sample_rate": sample_rate,
        });
        write.send(Message::Text(start_msg.to_string())).await?;

        let (tx, mut rx) = tokio::sync::mpsc::channel::<Vec<u8>>(1024);
        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let stop2 = stop.clone();
        let disconnected = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let disconnected_send = disconnected.clone();

        let send_handle = tokio::spawn(async move {
            while let Some(data) = rx.recv().await {
                if stop2.load(std::sync::atomic::Ordering::Relaxed) {
                    break;
                }
                if write.send(Message::Binary(data)).await.is_err() {
                    disconnected_send.store(true, std::sync::atomic::Ordering::SeqCst);
                    break;
                }
            }
            let _ = write
                .send(Message::Text(
                    serde_json::json!({"type": "stop"}).to_string(),
                ))
                .await;
            let _ = write.close().await;
        });

        // Recv task: parse transcripts from WS → callback
        let stop_recv = stop.clone();
        let recv_handle = tokio::spawn(async move {
            loop {
                match read.next().await {
                    Some(Ok(Message::Text(text))) => {
                        if let Ok(msg) = serde_json::from_str::<serde_json::Value>(&text) {
                            match msg["type"].as_str() {
                                Some("transcript") => {
                                    let txt = msg["text"].as_str().unwrap_or("").to_string();
                                    let bt = msg["begin_time"].as_f64().unwrap_or(0.0) as f32 / 1000.0;
                                    let et = msg["end_time"].as_f64().unwrap_or(0.0) as f32 / 1000.0;
                                    let is_end = msg["is_sentence_end"].as_bool().unwrap_or(false);
                                    if !txt.is_empty() {
                                        on_transcript(txt, bt, et, is_end);
                                    }
                                }
                                Some("asr_error") | Some("error") => {
                                    let err = msg["error"].as_str().unwrap_or("unknown").to_string();
                                    on_error(err);
                                }
                                Some("asr_complete") => {
                                    break;
                                }
                                _ => {}
                            }
                        }
                    }
                    Some(Err(e)) => {
                        on_error(format!("WS error: {e}"));
                        break;
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    _ => {}
                }
            }
            // Wait for send to finish
            stop_recv.store(true, std::sync::atomic::Ordering::Relaxed);
            let _ = send_handle.await;
        });

        Ok(Self {
            write_half: tx,
            stop_flag: stop,
            recv_handle,
            _disconnected: disconnected,
        })
    }

    /// 发送一帧 PCM 音频 (100ms/chunk, 3200 bytes @ 16kHz mono 16bit)
    pub async fn send_frame(&self, data: Vec<u8>) -> Result<()> {
        if self.write_half.send(data).await.is_err() {
            anyhow::bail!("WS send channel closed");
        }
        Ok(())
    }

    /// 等待转写完成 (blocking)
    pub async fn join(self) {
        self.stop_flag.store(true, std::sync::atomic::Ordering::Relaxed);
        let _ = self.recv_handle.await;
    }
}

/// 2026-07-01 ADR-0022: 复用 UI 选/建的 meeting_id 在服务端 init 会议.
///
/// 调 POST /api/meetings/stream_start?meeting_id=XXX&audio_source=YYY
/// 服务端: 若 XXX 已存在 → 复用 (返回原 meeting_id), 若不存在 → 用 XXX 创建新 state.
/// 返回服务端确认的 meeting_id (正常 = XXX 自身).
pub async fn init_meeting(gpu_url: &str, meeting_id: &str, audio_source: &str, auth_token: Option<String>) -> Result<String> {
    let url = format!(
        "{}/api/meetings/stream_start?meeting_id={}&audio_source={}",
        gpu_url,
        urlencoding::encode(meeting_id),
        urlencoding::encode(audio_source)
    );
    let client = reqwest::Client::new();
    let mut req = client
        .post(&url)
        .json(&serde_json::json!({"platform": "desktop_client"}));
    if let Some(tok) = auth_token {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req.send().await?;
    let body: serde_json::Value = resp.json().await?;
    Ok(body["meeting_id"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("no meeting_id in response"))?
        .to_string())
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

    #[tokio::test]
    async fn test_init_meeting_url_basic() {
        let mut server = mockito::Server::new_async().await;

        // URL: /api/meetings/stream_start?meeting_id=existing-999&audio_source=loopback
        let mock = server.mock("POST", "/api/meetings/stream_start?meeting_id=existing-999&audio_source=loopback")
            .with_status(200)
            .with_body(r#"{"meeting_id": "existing-999"}"#)
            .create_async()
            .await;

        let result = init_meeting(&server.url(), "existing-999", "loopback", None).await;
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

        let result = init_meeting(&server.url(), "my meeting/with#chars", "loopback", None).await;
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

        let result = init_meeting(&server.url(), "nonexistent", "microphone", None).await;
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

        let result = init_meeting(&server.url(), "test-id", "microphone", None).await;
        assert!(result.is_err(), "init_meeting should fail when meeting_id is missing");
        let err_msg = format!("{}", result.err().unwrap());
        assert!(err_msg.contains("no meeting_id"), "error should mention missing meeting_id");

        mock.assert_async().await;
    }
}
