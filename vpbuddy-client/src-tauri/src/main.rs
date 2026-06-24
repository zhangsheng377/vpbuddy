// VPBuddy Desktop Client — Tauri Rust 后端
// 设计: 持续抓系统音频 (cpal) → 16kHz mono PCM → 切片 30s → 推 GPU server
//       接收 GPU SSE 流 (transcript-segment / doc-status) → emit 到前端
//
// 关键约束:
// - 跨平台音频: Linux=PipeWire, macOS=CoreAudio+BlackHole, Windows=WASAPI
// - 不上 Rust streaming funasr (太重), GPU 端切片 batch ASR
// - 复用 Python vpbuddy 的 /api/meetings/stream_start + stream_chunk + events 端点

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
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
            // 默认 GPU server 端点 (VP 装客户端时改)
            gpu_url: std::env::var("VPBUDDY_GPU_URL")
                .unwrap_or_else(|_| "http://192.168.10.63:8765".to_string()),
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
        // 启动 SSE 接收任务
        let sse_gpu_url = gpu_url.clone();
        let sse_mid = mid.clone();
        let sse_app = app_clone.clone();
        let sse_capturing = capturing.clone();
        let sse_handle = tokio::spawn(async move {
            if let Err(e) = run_sse_loop(sse_app, sse_gpu_url, sse_mid, sse_capturing).await {
                log::error!("SSE 连接错误: {e}");
            }
        });

        // 启动音频采集循环
        if let Err(e) = run_capture_loop(
            app_clone.clone(),
            gpu_url,
            mid,
            capturing,
            bytes,
            ups,
            auto_upload,
        )
        .await
        {
            let _ = app_clone.emit("error", format!("采集错误: {e}"));
        }

        // 采集结束, 等 SSE 也结束
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

/// SSE 接收循环: 连接服务端事件流, 实时推送给前端
async fn run_sse_loop(
    app: AppHandle,
    gpu_url: String,
    meeting_id: String,
    capturing: Arc<AtomicBool>,
) -> anyhow::Result<()> {
    let url = format!("{}/api/meetings/{}/events", gpu_url, meeting_id);
    log::info!("SSE 连接: {url}");

    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .send()
        .await
        .map_err(|e| anyhow::anyhow!("SSE 连接失败: {e}"))?;

    let mut stream = resp.bytes_stream();
    let mut buf = String::new();

    while capturing.load(Ordering::SeqCst) {
        match tokio::time::timeout(
            std::time::Duration::from_secs(5),
            stream.next(),
        )
        .await
        {
            Ok(Some(Ok(chunk))) => {
                buf.push_str(&String::from_utf8_lossy(&chunk));

                // 解析 SSE 事件 (按 \n\n 分割)
                while let Some(pos) = buf.find("\n\n") {
                    let event_str = buf[..pos].to_string();
                    buf = buf[pos + 2..].to_string();

                    if let Some((event_type, data)) = parse_sse_event(&event_str) {
                        match event_type.as_str() {
                            "transcript-segment" => {
                                let _ = app.emit("transcript-segment", &data);
                            }
                            "state-update" => {
                                let _ = app.emit("state-update", &data);
                            }
                            "doc-update" => {
                                let _ = app.emit("doc-status", &data);
                            }
                            "heartbeat" | "connected" => {
                                // 忽略
                            }
                            "timeout" => {
                                log::warn!("SSE timeout, 准备重连");
                                break;
                            }
                            _ => {
                                let _ = app.emit("server-event", &data);
                            }
                        }
                    }
                }
            }
            Ok(Some(Err(e))) => {
                log::error!("SSE 流错误: {e}");
                tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            }
            Ok(None) => {
                log::info!("SSE 流结束");
                break;
            }
            Err(_) => {
                // 超时, 继续
                continue;
            }
        }
    }

    Ok(())
}

/// 解析单个 SSE 事件字符串
fn parse_sse_event(text: &str) -> Option<(String, serde_json::Value)> {
    let mut event_type = "message".to_string();
    let mut data = String::new();

    for line in text.lines() {
        if line.starts_with("event: ") {
            event_type = line[7..].trim().to_string();
        } else if line.starts_with("data: ") {
            data = line[6..].trim().to_string();
        }
    }

    if data.is_empty() {
        return None;
    }

    match serde_json::from_str(&data) {
        Ok(json) => Some((event_type, json)),
        Err(_) => Some((event_type, serde_json::Value::String(data))),
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
) -> anyhow::Result<()> {
    let sample_rate = 16000u32;

    // 启动真实音频采集 (cpal)
    let mut capture = audio::AudioCapture::new()
        .map_err(|e| anyhow::anyhow!("音频采集初始化失败: {e}"))?;

    // 30s 切片缓冲 = 16000 * 30 samples
    let chunk_samples = (sample_rate as usize) * 30;
    let mut buffer: Vec<i16> = Vec::with_capacity(chunk_samples);

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
        let _ = app.emit("capture-stats", serde_json::json!({
            "bytes": n,
            "uploads": ups.load(Ordering::SeqCst),
        }));

        // 满 30s 就切片上传
        if buffer.len() >= chunk_samples && auto_upload {
            let chunk: Vec<i16> = buffer.drain(..chunk_samples).collect();
            let wav_data = audio::encode_wav(&chunk, sample_rate)?;

            // 推 GPU
            match upload::upload_chunk(&gpu_url, &meeting_id, wav_data).await {
                Ok(seg) => {
                    ups.fetch_add(1, Ordering::SeqCst);
                    let _ = app.emit("transcript-segment", &seg);
                }
                Err(e) => {
                    let _ = app.emit("error", format!("上传失败: {e}"));
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
    let url = format!("{}/api/kb/search?q={}&top_k={}",
        state.gpu_url,
        urlencoding::encode(&query),
        top_k);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("KB 请求失败: {e}"))?;
    let body: serde_json::Value = resp.json()
        .await
        .map_err(|e| format!("KB 解析失败: {e}"))?;
    Ok(body["results"].as_array().cloned().unwrap_or_default())
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
            kb_search,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
