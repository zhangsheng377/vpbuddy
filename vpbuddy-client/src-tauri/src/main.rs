// VPBuddy Desktop Client — Tauri Rust 后端
// 设计: 持续抓系统音频 (cpal) → 16kHz mono PCM → 切片 30s → 推 GPU server
//       接收 GPU SSE 流 (transcript-segment / doc-status) → emit 到前端
//
// 关键约束:
// - 跨平台音频: Linux=PipeWire, macOS=CoreAudio+BlackHole, Windows=WASAPI
// - 不上 Rust streaming funasr (太重), GPU 端切片 batch ASR
// - 复用 Python vpbuddy 的 /api/meetings/upload 端点 (已 commit 05a2664)

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use tauri::{AppHandle, Emitter, State};
use tokio::sync::Mutex;

mod audio;
mod upload;

// ⚠️ Phase A: AudioCapture 暂未用 (cpal::Stream 跨 await 不是 Send, 留 Phase B 修)
// use audio::AudioCapture;

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
    let gpu_url = state.gpu_url.clone();
    let mid = meeting_id.clone();
    let capturing = state.capturing.clone();
    let bytes = state.total_bytes.clone();
    let ups = state.total_uploads.clone();
    let app_clone = app.clone();

    let handle = tokio::spawn(async move {
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

/// 采集主循环: cpal 流 → 30s 切片 → WAV → multipart POST GPU
///
/// ⚠️ Phase A (2026-06-24): 当前是 stub — 真音频采集在 Phase B 实现
/// Phase A 只需要 cargo check 通过 + 空白窗口能开. 真 Phase B 要做:
/// 1. cpal 流跑在 std::thread (不是 tokio task), cpal::Stream 内部 *mut () 不是 Send
/// 2. samples 通过 mpsc::channel 发给 tokio task
/// 3. tokio task 拼 30s 切片 + multipart POST
///
/// 当前 stub: 生成 0.5s 静音 samples 让循环跑通 + stats emit 验证全链路.
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
    // 0.5s 静音 (8000 samples i16) — Phase A stub
    let chunk_05s: Vec<i16> = vec![0i16; (sample_rate / 2) as usize];

    // 30s 切片缓冲 = 16000 * 30 samples
    let chunk_samples = (sample_rate as usize) * 30;
    let mut buffer: Vec<i16> = Vec::with_capacity(chunk_samples);

    while capturing.load(Ordering::SeqCst) {
        // Phase A stub: 不调 AudioCapture (cpal 不是 Send)
        let samples = chunk_05s.clone();
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        buffer.extend(samples);

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
