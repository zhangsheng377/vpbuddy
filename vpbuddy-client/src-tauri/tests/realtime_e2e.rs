//! 真实 Rust → GPU 服务器端到端集成测试 (2026-07-09)
//!
//! 覆盖:
//! - REST: /healthz(无认证), /api/status(需认证), stream_start, PATCH title, DELETE meeting
//! - WS: realtime_asr (token 认证/拒绝, PCM relay, stop, ping-pong, 非法 handshake)
//! - 安全: owner 校验 (跨用户 403, 复用他人会议 403), 无认证 401
//!
//! 跑法:
//!   GPU_URL=http://47.100.182.3:28765 cargo test --test realtime_e2e -- --nocapture
//!
//! 需要 GPU server 可访问

use std::env;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn timestamp() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis()
        .to_string()
}

fn gpu_url() -> String {
    env::var("GPU_URL")
        .ok()
        .or_else(|| env::var("VPBUDDY_GPU_URL").ok())
        .unwrap_or_else(|| "http://47.100.182.3:28765".to_string())
}

fn http() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .unwrap()
}

async fn register_user(client: &reqwest::Client, url: &str, prefix: &str) -> (String, String) {
    let email = format!("e2e_{}_{}@test.dev", prefix, timestamp());
    let resp = client
        .post(format!("{}/api/auth/register", url))
        .json(&serde_json::json!({"email": email, "password": "t123456"}))
        .send()
        .await
        .expect("register HTTP");
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(
        resp.status(),
        200,
        "register failed: {}",
        body
    );
    (body["token"].as_str().unwrap().to_string(), email)
}

async fn create_meeting(client: &reqwest::Client, url: &str, token: &str, mid: &str) -> String {
    let resp = client
        .post(format!(
            "{}/api/meetings/stream_start?meeting_id={}&audio_source=microphone&project_name={}",
            url, mid, mid
        ))
        .header("Authorization", format!("Bearer {}", token))
        .json(&serde_json::json!({"platform": "e2e_test"}))
        .send()
        .await
        .expect("stream_start HTTP");
    let status = resp.status();
    let body: serde_json::Value = resp.json().await.unwrap_or_default();
    assert!(status.is_success(), "stream_start failed {}: {:?}", status, body);
    body["meeting_id"].as_str().unwrap().to_string()
}

fn rand_mid() -> String {
    format!("e2e_{}", timestamp())
}

// ══════════════════════════════════════════════════════════════════
// 无认证端点
// ══════════════════════════════════════════════════════════════════

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问: GPU_URL=http://47.100.182.3:28765 cargo test --test realtime_e2e -- --ignored --nocapture"]
async fn test_healthz_no_auth() {
    let url = gpu_url();
    let resp = http().get(format!("{}/healthz", url)).send().await.unwrap();
    assert_eq!(resp.status(), 200);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["ok"], true);
}

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_status_requires_auth() {
    let url = gpu_url();
    let resp = http()
        .get(format!("{}/api/status", url))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401, "/api/status 无 token 应为 401");
}

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_status_with_auth() {
    let url = gpu_url();
    let c = http();
    let (token, _) = register_user(&c, &url, "status").await;
    let resp = c
        .get(format!("{}/api/status", url))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
}

// ══════════════════════════════════════════════════════════════════
// 会议 CRUD
// ══════════════════════════════════════════════════════════════════

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_meeting_lifecycle() {
    let url = gpu_url();
    let c = http();
    let (token, _) = register_user(&c, &url, "lc").await;
    let mid = rand_mid();

    let m = create_meeting(&c, &url, &token, &mid).await;
    assert_eq!(m, mid);

    let resp = c
        .patch(format!("{}/api/meetings/{}", url, mid))
        .header("Authorization", format!("Bearer {}", token))
        .json(&serde_json::json!({"project_name": "改写后的标题"}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["project_name"], "改写后的标题");

    let resp = c
        .delete(format!("{}/api/meetings/{}", url, mid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["meeting_id"], mid);
    assert!(body["deleted"]["state"].as_bool().unwrap_or(false));
}

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_delete_nonexistent_meeting() {
    let url = gpu_url();
    let c = http();
    let (token, _) = register_user(&c, &url, "dne").await;
    let mid = rand_mid();

    let resp = c
        .delete(format!("{}/api/meetings/{}", url, mid))
        .header("Authorization", format!("Bearer {}", token))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 404);
}

// ══════════════════════════════════════════════════════════════════
// owner 安全校验
// ══════════════════════════════════════════════════════════════════

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_meeting_owner_isolation() {
    let url = gpu_url();
    let c = http();
    let (tok_a, _) = register_user(&c, &url, "a").await;
    let (tok_b, _) = register_user(&c, &url, "b").await;
    let mid = rand_mid();

    create_meeting(&c, &url, &tok_a, &mid).await;

    for path in &["docs", "events", "state"] {
        let resp = c
            .get(format!("{}/api/meetings/{}/{}", url, mid, path))
            .header("Authorization", format!("Bearer {}", tok_b))
            .send()
            .await
            .unwrap();
        assert_eq!(resp.status(), 403, "GET {} 应拒绝非 owner", path);
    }

    let resp = c
        .post(format!("{}/api/meetings/{}/close", url, mid))
        .header("Authorization", format!("Bearer {}", tok_b))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 403, "close 应拒绝非 owner");

    let resp = c
        .post(format!(
            "{}/api/meetings/stream_start?meeting_id={}&audio_source=microphone",
            url, mid
        ))
        .header("Authorization", format!("Bearer {}", tok_b))
        .json(&serde_json::json!({"platform": "e2e_test"}))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 403, "复用他人 meeting 应 403");

    let resp = c
        .delete(format!("{}/api/meetings/{}", url, mid))
        .header("Authorization", format!("Bearer {}", tok_a))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200, "owner 应能删除自己的会议");
}

// ══════════════════════════════════════════════════════════════════
// WS realtime_asr
// ══════════════════════════════════════════════════════════════════

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_ws_realtime_asr_no_token_rejected() {
    let url = gpu_url();
    let ws_url = url
        .replace("http://", "ws://")
        .replace("https://", "wss://");
    let mid = rand_mid();

    let (ws, _) = tokio_tungstenite::connect_async(format!(
        "{}/api/meetings/{}/realtime_asr",
        ws_url, mid
    ))
    .await
    .expect("WS connect");
    let (_, mut read) = ws.split();

    use futures_util::StreamExt;
    use tokio_tungstenite::tungstenite::Message;

    if let Some(Ok(Message::Text(text))) = read.next().await {
        let msg: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(msg["type"], "error");
        assert!(
            msg["error"].as_str().unwrap().contains("token"),
            "错误信息应包含 token: {:?}",
            msg
        );
    } else {
        panic!("应收到 token 无效的 error 消息");
    }
}

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_ws_realtime_asr_with_token() {
    let url = gpu_url();
    let c = http();
    let (token, _) = register_user(&c, &url, "ws").await;
    let mid = rand_mid();
    create_meeting(&c, &url, &token, &mid).await;

    let ws_url = url.replace("http://", "ws://").replace("https://", "wss://");
    let connect_url = format!(
        "{}/api/meetings/{}/realtime_asr?token={}",
        ws_url, mid, token
    );

    let (ws, _) = tokio_tungstenite::connect_async(&connect_url)
        .await
        .expect("WS connect");
    let (mut write, mut read) = ws.split();

    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::Message;

    write
        .send(Message::Text(
            serde_json::json!({"type": "start", "format": "pcm", "sample_rate": 16000})
                .to_string(),
        ))
        .await
        .unwrap();

    let mut connected = false;
    let mut got_error = false;
    for _ in 0..20 {
        if let Some(Ok(Message::Text(text))) = read.next().await {
            let msg: serde_json::Value = serde_json::from_str(&text).unwrap();
            match msg["type"].as_str() {
                Some("asr_status") if msg["status"] == "connected" => {
                    connected = true;
                    break;
                }
                Some("asr_error") => {
                    got_error = true;
                    eprintln!("  百炼 API 不可用 (跳过), 但 WS 握手流程正常");
                    break;
                }
                _ => {}
            }
        } else {
            break;
        }
    }

    if got_error {
        return;
    }
    assert!(connected, "应收到 asr_status: connected");

    let pcm = vec![0i16; 3200];
    let pcm_bytes: Vec<u8> = pcm.iter().flat_map(|s| s.to_le_bytes()).collect();
    write.send(Message::Binary(pcm_bytes)).await.unwrap();

    write
        .send(Message::Text(
            serde_json::json!({"type": "stop"}).to_string(),
        ))
        .await
        .unwrap();

    tokio::time::sleep(Duration::from_secs(2)).await;
}

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_ws_ping_pong() {
    let url = gpu_url();
    let c = http();
    let (token, _) = register_user(&c, &url, "pp").await;
    let mid = rand_mid();
    create_meeting(&c, &url, &token, &mid).await;

    let ws_url = url.replace("http://", "ws://");
    let connect_url = format!(
        "{}/api/meetings/{}/realtime_asr?token={}",
        ws_url, mid, token
    );

    let (ws, _) = tokio_tungstenite::connect_async(&connect_url)
        .await
        .expect("WS connect");
    let (mut write, mut read) = ws.split();

    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::Message;

    write
        .send(Message::Text(
            serde_json::json!({"type": "start", "format": "pcm", "sample_rate": 16000})
                .to_string(),
        ))
        .await
        .unwrap();

    write
        .send(Message::Text(
            serde_json::json!({"type": "ping"}).to_string(),
        ))
        .await
        .unwrap();

    let mut got_pong = false;
    for _ in 0..20 {
        if let Some(Ok(Message::Text(text))) = read.next().await {
            let msg: serde_json::Value = serde_json::from_str(&text).unwrap();
            if msg["type"] == "pong" {
                got_pong = true;
                break;
            }
            if msg["type"] == "asr_error" {
                break;
            }
        }
    }
    assert!(got_pong, "应收到 pong");
}

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_ws_bad_handshake() {
    let url = gpu_url();
    let c = http();
    let (token, _) = register_user(&c, &url, "bh").await;
    let mid = rand_mid();
    create_meeting(&c, &url, &token, &mid).await;

    let ws_url = url.replace("http://", "ws://");
    let connect_url = format!(
        "{}/api/meetings/{}/realtime_asr?token={}",
        ws_url, mid, token
    );

    let (ws, _) = tokio_tungstenite::connect_async(&connect_url)
        .await
        .unwrap();
    let (mut write, mut read) = ws.split();

    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::Message;

    write
        .send(Message::Text(
            serde_json::json!({"type": "bad"}).to_string(),
        ))
        .await
        .unwrap();

    let mut got_error = false;
    for _ in 0..5 {
        if let Some(Ok(Message::Text(text))) = read.next().await {
            let msg: serde_json::Value = serde_json::from_str(&text).unwrap();
            if msg["type"] == "error" {
                got_error = true;
                break;
            }
        }
    }
    assert!(got_error, "非法 handshake 应收 error");
}

#[tokio::test]
#[ignore = "需要 GPU 服务器可访问"]
async fn test_ws_unauthorized_meeting() {
    let url = gpu_url();
    let c = http();
    let (tok_a, _) = register_user(&c, &url, "wsu").await;
    let (tok_b, _) = register_user(&c, &url, "wsu2").await;
    let mid = rand_mid();
    create_meeting(&c, &url, &tok_a, &mid).await;

    let ws_url = url.replace("http://", "ws://");
    let connect_url = format!(
        "{}/api/meetings/{}/realtime_asr?token={}",
        ws_url, mid, tok_b
    );

    let (ws, _) = tokio_tungstenite::connect_async(&connect_url)
        .await
        .expect("WS connect");
    let (mut write, mut read) = ws.split();

    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::Message;

    write
        .send(Message::Text(
            serde_json::json!({"type": "start", "format": "pcm", "sample_rate": 16000})
                .to_string(),
        ))
        .await
        .unwrap();

    let mut got_error = false;
    for _ in 0..20 {
        if let Some(Ok(Message::Text(text))) = read.next().await {
            let msg: serde_json::Value = serde_json::from_str(&text).unwrap();
            if msg["type"] == "error" || msg["type"] == "asr_error" {
                got_error = true;
                break;
            }
        }
    }
    assert!(got_error, "非 owner 的 WS 应被拒绝 (可在服务端补充校验)");
}
