// VPBuddy Desktop Client — Tauri Rust 后端
// 设计: 持续抓系统音频 (cpal) → 16kHz mono PCM → 切片 30s → 推 GPU server
//       同时通过 SSE 接收服务端实时推送 → emit 到前端
//
// 关键约束:
// - 跨平台音频: Linux=PipeWire, macOS=CoreAudio+BlackHole, Windows=WASAPI
// - 不上 Rust streaming funasr (太重), GPU 端切片 batch ASR
// - 复用 Python vpbuddy 的 /api/meetings/stream_start + stream_chunk + events 端点
// - SSE 自动重连, 指数退避

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tauri::{AppHandle, Emitter, State};
use tokio::sync::Mutex;

mod audio;
mod upload;

/// 全局状态
pub struct AppState {
    pub capturing: Arc<AtomicBool>,
    pub capture_handle: Arc<Mutex<Option<tokio::task::JoinHandle<()>>>>,
    pub total_bytes: Arc<AtomicU64>,
    pub total_uploads: Arc<AtomicU64>,
    pub gpu_url: String,
    pub meeting_id: Arc<Mutex<Option<String>>>,
}

impl AppState {
    fn new() -> Self {
        Self {
            capturing: Arc::new(AtomicBool::new(false)),
            capture_handle: Arc::new(Mutex::new(None)),
            total_bytes: Arc::new(AtomicU64::new(0)),
            total_uploads: Arc::new(AtomicU64::new(0)),
            gpu_url: std::env::var("VPBUDDY_GPU_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string()),
            meeting_id: Arc::new(Mutex::new(None)),
        }
    }
}

/// 启动采集 (VP 点"开始录音")
#[tauri::command]
async fn start_capture(
    app: AppHandle,
    state: State<'_, AppState>,
    auto_upload: bool,
    audio_device: Option<String>,
) -> Result<String, String> {
    if state.capturing.load(Ordering::SeqCst) {
        return Err("已在采集中".into());
    }

    // 1. 在 GPU 端创建会议 + 取 meeting_id
    let meeting_id = upload::create_meeting(&state.gpu_url)
        .await
        .map_err(|e| format!("创建会议失败: {e}"))?;
    *state.meeting_id.lock().await = Some(meeting_id.clone());

    state.capturing.store(true, Ordering::SeqCst);
    state.total_bytes.store(0, Ordering::SeqCst);
    state.total_uploads.store(0, Ordering::SeqCst);

    // 2. 启动音频采集线程 → 30s 切片 → 推 GPU
    // 3. 同时启动 SSE 连接接收实时结果
    let gpu_url = state.gpu_url.clone();
    let mid = meeting_id.clone();
    let capturing = state.capturing.clone();
    let bytes = state.total_bytes.clone();
    let ups = state.total_uploads.clone();
    let app_clone = app.clone();

    let handle = tokio::spawn(async move {
        // SSE 接收任务 (独立 task)
        let sse_gpu_url = gpu_url.clone();
        let sse_mid = mid.clone();
        let sse_app = app_clone.clone();
        let sse_capturing = capturing.clone();
        let sse_handle = tokio::spawn(async move {
            run_sse_loop(sse_app, sse_gpu_url, sse_mid, sse_capturing).await;
        });

        // 音频采集 + 上传任务
        if let Err(e) = run_capture_loop(
            app_clone.clone(),
            gpu_url,
            mid,
            capturing,
            bytes,
            ups,
            auto_upload,
            audio_device,
        )
        .await
        {
            let _ = app_clone.emit("error", format!("采集错误: {e}"));
        }

        // 采集结束, 终止 SSE
        sse_handle.abort();
    });

    *state.capture_handle.lock().await = Some(handle);
    Ok(meeting_id)
}

/// 停止采集
#[tauri::command]
async fn stop_capture(state: State<'_, AppState>) -> Result<(), String> {
    state.capturing.store(false, Ordering::SeqCst);
    if let Some(h) = state.capture_handle.lock().await.take() {
        h.abort();
    }
    Ok(())
}

/// 枚举系统音频输入设备
#[tauri::command]
async fn list_audio_devices() -> Result<Vec<audio::AudioDeviceInfo>, String> {
    audio::list_input_devices().map_err(|e| format!("获取音频设备失败: {e}"))
}

/// SSE 接收主循环: 连接服务端事件流, 自动重连, 实时推送给前端
async fn run_sse_loop(
    app: AppHandle,
    gpu_url: String,
    meeting_id: String,
    capturing: Arc<AtomicBool>,
) {
    let url = format!("{}/api/meetings/{}/events", gpu_url, meeting_id);
    let mut retry_count = 0u32;
    let mut last_event_id: Option<String> = None;

    while capturing.load(Ordering::SeqCst) {
        let connect_url = if let Some(id) = &last_event_id {
            format!("{url}?last_event_id={}", urlencoding::encode(id))
        } else {
            url.clone()
        };
        log::info!("SSE 连接: {connect_url}");

        match connect_and_read_sse(&app, &connect_url, capturing.clone(), &mut last_event_id).await
        {
            Ok(()) => {
                // 正常断开, 退出
                break;
            }
            Err(e) => {
                log::warn!("SSE 断开: {e}, 准备重连...");
                retry_count += 1;
                // 指数退避: 1s, 2s, 4s, 8s, 最多 10s
                let delay = (1u64 << retry_count.min(3)) * 1000;
                let delay = delay.min(10_000);
                tokio::time::sleep(std::time::Duration::from_millis(delay)).await;
            }
        }
    }
}

/// 单次 SSE 连接与读取
async fn connect_and_read_sse(
    app: &AppHandle,
    url: &str,
    capturing: Arc<AtomicBool>,
    last_event_id: &mut Option<String>,
) -> anyhow::Result<()> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;

    let resp = client.get(url).send().await?;
    if !resp.status().is_success() {
        anyhow::bail!("HTTP {}", resp.status());
    }

    let mut stream = resp.bytes_stream();
    let mut buf = String::new();

    while capturing.load(Ordering::SeqCst) {
        use futures_util::StreamExt;
        match tokio::time::timeout(std::time::Duration::from_secs(15), stream.next()).await {
            Ok(Some(Ok(chunk))) => {
                buf.push_str(&String::from_utf8_lossy(&chunk));
                // 解析所有完整事件
                while let Some(pos) = buf.find("\n\n") {
                    let event_str = buf[..pos].to_string();
                    buf = buf[pos + 2..].to_string();
                    handle_sse_event(app, &event_str, last_event_id);
                }
            }
            Ok(Some(Err(e))) => {
                anyhow::bail!("SSE 流错误: {e}");
            }
            Ok(None) => {
                anyhow::bail!("SSE 流结束");
            }
            Err(_) => {
                // 超时, 继续 (心跳保活)
                continue;
            }
        }
    }

    Ok(())
}

/// 处理单个 SSE 事件, 分发 emit 给前端
fn handle_sse_event(app: &AppHandle, event_str: &str, last_event_id: &mut Option<String>) {
    let mut event_type = "message".to_string();
    let mut data = String::new();

    for line in event_str.lines() {
        if let Some(rest) = line.strip_prefix("id: ") {
            *last_event_id = Some(rest.trim().to_string());
        } else if let Some(rest) = line.strip_prefix("event: ") {
            event_type = rest.trim().to_string();
        } else if let Some(rest) = line.strip_prefix("data: ") {
            data = rest.trim().to_string();
        }
    }

    if data.is_empty() {
        return;
    }

    // 解析 JSON
    let payload: serde_json::Value = match serde_json::from_str(&data) {
        Ok(v) => v,
        Err(_) => return,
    };

    match event_type.as_str() {
        "transcript-segment" => {
            let _ = app.emit("transcript-segment", &payload);
        }
        "state-update" => {
            let _ = app.emit("state-update", &payload);
        }
        "doc-update" => {
            let _ = app.emit("doc-status", &payload);
        }
        "chat-message" => {
            let _ = app.emit("chat-message", &payload);
        }
        "metrics-update" => {
            let _ = app.emit("metrics-update", &payload);
        }
        "connected" => {
            let _ = app.emit("connection-status", serde_json::json!({"sse": "connected"}));
        }
        "heartbeat" => {
            let _ = app.emit("connection-status", serde_json::json!({"sse": "heartbeat"}));
        }
        "timeout" => {
            log::warn!("SSE timeout event received");
        }
        other => {
            // 其他事件统一转发
            let _ = app.emit("server-event", &payload);
            log::debug!("SSE event: {other}");
        }
    }
}

/// 采集主循环: cpal 流 → 30s 切片 → WAV → multipart POST GPU
async fn run_capture_loop(
    app: AppHandle,
    gpu_url: String,
    meeting_id: String,
    capturing: Arc<AtomicBool>,
    bytes: Arc<AtomicU64>,
    ups: Arc<AtomicU64>,
    auto_upload: bool,
    audio_device: Option<String>,
) -> anyhow::Result<()> {
    let sample_rate = 16000u32;

    // 启动真实音频采集 (cpal)
    let mut capture = audio::AudioCapture::new_with_device(audio_device)
        .map_err(|e| anyhow::anyhow!("音频采集初始化失败: {e}"))?;

    // 30s 切片缓冲 = 16000 * 30 samples
    let chunk_samples = (sample_rate as usize) * 30;
    let overlap_samples = (sample_rate as usize) * 2;
    let overlap_sec = overlap_samples as f32 / sample_rate as f32;
    let mut buffer: Vec<i16> = Vec::with_capacity(chunk_samples);
    let mut chunk_index: u64 = 0;

    while capturing.load(Ordering::SeqCst) {
        // 读 0.5s 真实音频
        match capture.read_chunk(0.5, sample_rate).await {
            Ok(samples) => {
                buffer.extend(samples);
            }
            Err(e) => {
                log::error!("音频读取错误: {e}");
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                continue;
            }
        }

        // 累计字节数
        let n = (buffer.len() * 2) as u64;
        bytes.store(n, Ordering::SeqCst);
        let _ = app.emit(
            "capture-stats",
            serde_json::json!({
                "bytes": n,
                "uploads": ups.load(Ordering::SeqCst),
            }),
        );

        // 满 30s 就切片上传
        if buffer.len() >= chunk_samples && auto_upload {
            let chunk: Vec<i16> = buffer[..chunk_samples].to_vec();
            let keep_from = chunk_samples.saturating_sub(overlap_samples);
            buffer = buffer[keep_from..].to_vec();
            let wav_data = audio::encode_wav(&chunk, sample_rate)?;
            let chunk_start_sec = (chunk_index as f32
                * (chunk_samples.saturating_sub(overlap_samples)) as f32)
                / sample_rate as f32;

            // 推 GPU
            match upload::upload_chunk(
                &gpu_url,
                &meeting_id,
                wav_data,
                chunk_index,
                chunk_start_sec,
                overlap_sec,
            )
            .await
            {
                Ok(segments) => {
                    ups.fetch_add(1, Ordering::SeqCst);
                    chunk_index += 1;
                    // HTTP 响应里的 segments 也 emit 一份 (双保险, SSE 可能延迟)
                    for seg in &segments {
                        let _ = app.emit("transcript-segment", seg);
                    }
                }
                Err(e) => {
                    let _ = app.emit("error", format!("上传失败: {e}"));
                    let _ = app.emit("connection-status", serde_json::json!({"upload": "failed"}));
                }
            }
        }
    }

    Ok(())
}

/// GPU 端 KB 检索 (本地客户端不能直接连 KB, 走 GPU server 代理)
#[tauri::command]
async fn kb_search(
    state: State<'_, AppState>,
    query: String,
    top_k: u32,
) -> Result<Vec<serde_json::Value>, String> {
    let url = format!(
        "{}/api/kb/search?q={}&top_k={}",
        state.gpu_url,
        urlencoding::encode(&query),
        top_k
    );
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("KB 请求失败: {e}"))?;
    let body: serde_json::Value = resp.json().await.map_err(|e| format!("KB 解析失败: {e}"))?;
    Ok(body["results"].as_array().cloned().unwrap_or_default())
}

#[tauri::command]
async fn get_current_meeting(state: State<'_, AppState>) -> Result<Option<String>, String> {
    Ok(state.meeting_id.lock().await.clone())
}

#[tauri::command]
async fn get_meeting_state(
    state: State<'_, AppState>,
    meeting_id: String,
) -> Result<serde_json::Value, String> {
    let url = format!("{}/api/meetings/{}/state", state.gpu_url, meeting_id);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("会议状态请求失败: {e}"))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| format!("会议状态解析失败: {e}"))
}

#[tauri::command]
async fn get_meeting_docs(
    state: State<'_, AppState>,
    meeting_id: String,
) -> Result<serde_json::Value, String> {
    let url = format!("{}/api/meetings/{}/docs", state.gpu_url, meeting_id);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("文档请求失败: {e}"))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| format!("文档解析失败: {e}"))
}

#[tauri::command]
async fn send_chat_message(
    state: State<'_, AppState>,
    meeting_id: String,
    message: String,
    context: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let url = format!("{}/api/meetings/{}/chat", state.gpu_url, meeting_id);
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&serde_json::json!({
            "message": message,
            "context": context.unwrap_or_else(|| serde_json::json!({})),
        }))
        .send()
        .await
        .map_err(|e| format!("Chat 请求失败: {e}"))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| format!("Chat 响应解析失败: {e}"))
}

#[tauri::command]
async fn get_chat_history(
    state: State<'_, AppState>,
    meeting_id: String,
) -> Result<serde_json::Value, String> {
    let url = format!("{}/api/meetings/{}/chat/history", state.gpu_url, meeting_id);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("Chat 历史请求失败: {e}"))?;
    resp.json::<serde_json::Value>()
        .await
        .map_err(|e| format!("Chat 历史解析失败: {e}"))
}

fn main() {
    env_logger::init();
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .manage(AppState::new())
        .setup(|app| {
            // 系统托盘: 状态指示 + 退出
            use tauri::tray::TrayIconBuilder;
            let _tray = TrayIconBuilder::with_id("main-tray")
                .tooltip("VPBuddy")
                .icon(app.default_window_icon().unwrap().clone())
                .on_tray_icon_event(|_tray, event| {
                    if let tauri::tray::TrayIconEvent::Click { .. } = event {
                        // 切回主窗口
                    }
                })
                .build(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_capture,
            stop_capture,
            list_audio_devices,
            kb_search,
            get_current_meeting,
            get_meeting_state,
            get_meeting_docs,
            send_chat_message,
            get_chat_history,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
