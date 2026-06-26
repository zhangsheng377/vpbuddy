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
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::sync::Mutex;

mod audio;
mod upload;

use audio::AudioCapture;

/// 全局状态
pub struct AppState {
    pub capturing: Arc<AtomicBool>,
    pub capture_handle: Arc<Mutex<Option<tokio::task::JoinHandle<()>>>>,
    pub total_bytes: Arc<AtomicU64>,
    pub total_uploads: Arc<AtomicU64>,
    /// 2026-06-26: 改 Mutex 支持运行时修改 (设置页 GPU URL 输入)
    pub gpu_url: Arc<Mutex<String>>,
    pub meeting_id: Arc<Mutex<Option<String>>>,
}

impl AppState {
    fn new() -> Self {
        let url = std::env::var("VPBUDDY_GPU_URL")
            .unwrap_or_else(|_| "http://192.168.10.63:8765".to_string());
        Self {
            capturing: Arc::new(AtomicBool::new(false)),
            capture_handle: Arc::new(Mutex::new(None)),
            total_bytes: Arc::new(AtomicU64::new(0)),
            total_uploads: Arc::new(AtomicU64::new(0)),
            gpu_url: Arc::new(Mutex::new(url)),
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
    log::info!("=== start_capture 触发 ===");
    log::info!("  audio_device: {:?}", audio_device);
    log::info!("  auto_upload: {}", auto_upload);

    // 1. 在 GPU 端创建会议 + 取 meeting_id
    // 2026-06-26: gpu_url 改 Arc<Mutex>, 需要 lock().await
    let gpu_url = state.gpu_url.lock().await.clone();
    log::info!("  POST {}/api/meetings/stream_start", gpu_url);
    let meeting_id = upload::create_meeting(&gpu_url)
        .await
        .map_err(|e| {
            log::error!("创建会议失败: {e}");
            format!("创建会议失败: {e}")
        })?;
    log::info!("  ✓ meeting_id: {meeting_id}");
    *state.meeting_id.lock().await = Some(meeting_id.clone());

    state.capturing.store(true, Ordering::SeqCst);
    state.total_bytes.store(0, Ordering::SeqCst);
    state.total_uploads.store(0, Ordering::SeqCst);

    // 2. 启动音频采集线程 → 30s 切片 → 推 GPU
    // 3. 同时启动 SSE 连接接收实时结果 (2026-06-25 cherry-pick from feature/requirements-architecture-update)
    let gpu_url = state.gpu_url.lock().await.clone();
    let mid = meeting_id.clone();
    let capturing = state.capturing.clone();
    let bytes = state.total_bytes.clone();
    let ups = state.total_uploads.clone();
    let app_clone = app.clone();

    let handle = tokio::spawn(async move {
        // SSE 接收任务 (独立 task, 跟音频采集并行)
        let sse_gpu_url = gpu_url.clone();
        let sse_mid = mid.clone();
        let sse_app = app_clone.clone();
        let sse_capturing = capturing.clone();
        let sse_handle = tokio::spawn(async move {
            run_sse_loop(sse_app, sse_gpu_url, sse_mid, sse_capturing).await;
        });

        // 音频采集 + 上传任务 (Phase B spawn_blocking, 保留我们之前的修复)
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
    log::info!("=== stop_capture 触发 ===");
    state.capturing.store(false, Ordering::SeqCst);
    if let Some(h) = state.capture_handle.lock().await.take() {
        h.abort();
        log::info!("  采集 task 已 abort");
    } else {
        log::warn!("  capture_handle 为空, 没在跑采集?");
    }
    Ok(())
}

/// 2026-06-25: 枚举系统音频输入设备 (cherry-pick from feature 分支)
#[tauri::command]
async fn list_audio_devices() -> Result<Vec<audio::AudioDeviceInfo>, String> {
    log::info!("list_audio_devices 调用");
    let r = audio::list_input_devices().map_err(|e| {
        log::error!("枚举音频设备失败: {e}");
        format!("获取音频设备失败: {e}")
    });
    if let Ok(ref devs) = r {
        log::info!("  找到 {} 个输入设备:", devs.len());
        for d in devs {
            log::info!("    - {} {}{}", d.name, d.id, if d.is_default { " [默认]" } else { "" });
        }
    }
    r
}

/// 2026-06-26: 运行时修改 GPU server 地址 (设置页填)
#[tauri::command]
async fn set_gpu_url(state: State<'_, AppState>, url: String) -> Result<(), String> {
    let trimmed = url.trim().to_string();
    if trimmed.is_empty() {
        return Err("地址不能为空".into());
    }
    if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
        return Err("地址必须以 http:// 或 https:// 开头".into());
    }
    *state.gpu_url.lock().await = trimmed.clone();
    Ok(())
}

/// 2026-06-26: 返回当前 GPU server URL (前端 fetch API 用)
#[tauri::command]
async fn get_gpu_url(state: State<'_, AppState>) -> Result<String, String> {
    Ok(state.gpu_url.lock().await.clone())
}

/// 采集主循环: cpal 流 → 30s 切片 → WAV → multipart POST GPU
///
/// Phase B (2026-06-24): cpal::Stream 持有 *mut () 不是 Send. 拆架构:
/// - spawn_blocking 跑 cpal 采集 (sync context, 不需要 Send)
/// - spawn_blocking 通过 mpsc channel 把 samples 发给 tokio task
/// - tokio task 拼 30s 切片 + multipart POST GPU (async context, 需要 Send)
///
/// 设计取舍: 为什么不用 std::thread + crossbeam_channel?
/// - tokio 整体架构, 用 spawn_blocking 更一致
/// - 跨平台 Rust 习惯, tokio 文档推荐 pattern
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
    use tokio::sync::mpsc as tmpsc;
    let (tx, mut rx) = tmpsc::channel::<Vec<i16>>(64);

    // 1. spawn_blocking 跑 cpal 采集 — 不要求 Send (跑在专用 blocking pool)
    let capturing_bg = capturing.clone();
    let _capture_handle = tokio::task::spawn_blocking(move || -> anyhow::Result<()> {
        // 2026-06-25: 用 new_with_device 支持指定输入设备 (cherry-pick from feature 分支)
        log::info!("cpal: new_with_device({:?})", audio_device);
        let mut capture = match AudioCapture::new_with_device(audio_device) {
            Ok(c) => {
                log::info!("  ✓ AudioCapture 初始化成功 (host={:?})", cpal::default_host().id());
                c
            }
            Err(e) => {
                log::error!("  ✗ AudioCapture 初始化失败: {e}");
                log::error!("  → 检查: 麦克风权限 / PulseAudio/PipeWire / 其他进程独占");
                return Err(e);
            }
        };
        let sample_rate = 16000u32;
        let mut chunk_count: u64 = 0;
        while capturing_bg.load(Ordering::SeqCst) {
            // 0.5s blocking read — cpal 流在 std context (不需要 Send)
            let samples = capture.read_chunk_blocking(0.5, sample_rate)?;
            chunk_count += 1;
            if chunk_count == 1 {
                log::info!("cpal: 第一个 chunk 收到 {} samples ({:.1}s @ {}Hz)",
                    samples.len(), samples.len() as f32 / sample_rate as f32, sample_rate);
            } else if chunk_count % 60 == 0 {
                log::debug!("cpal: 已读 {} chunks ({}s 音频)", chunk_count, chunk_count as f32 * 0.5);
            }
            // send 是 async 的, 但我们在 blocking context → 用 blocking_send
            if let Err(e) = tx.blocking_send(samples) {
                log::warn!("audio channel closed: {e}");
                break;
            }
        }
        log::info!("cpal: 退出采集循环, 共读 {} chunks", chunk_count);
        Ok(())
    });

    let sample_rate = 16000u32;
    // 30s 切片缓冲
    let chunk_samples = (sample_rate as usize) * 30;
    let mut buffer: Vec<i16> = Vec::with_capacity(chunk_samples);
    // 2026-06-25: 切片序号 (TRAE upload_chunk API 需要)
    let mut chunk_index: u64 = 0;
    let mut total_elapsed_sec: f32 = 0.0;
    // 2026-06-27: 累计 0-RMS 帧数, 30s 内全是 0 (即 60 帧) 就 warn 一次
    let mut silence_streak: u32 = 0;

    // 2. tokio task 拼切片 + POST
    while capturing.load(Ordering::SeqCst) {
        let samples = match rx.recv().await {
            Some(s) => s,
            None => break,  // channel 关闭 (cpal 异常退出)
        };
        buffer.extend(samples.iter().copied());

        // 累计字节数
        let n = (buffer.len() * 2) as u64;
        bytes.store(n, Ordering::SeqCst);
        let _ = app.emit("capture-stats", serde_json::json!({
            "bytes": n,
            "uploads": ups.load(Ordering::SeqCst),
        }));
        if n > 0 && chunk_index == 0 && buffer.len() < chunk_samples {
            log::info!("buffering: {} / {} samples ({:.0}%)",
                buffer.len(), chunk_samples, buffer.len() as f32 / chunk_samples as f32 * 100.0);
        }

        // 2026-06-27: 算 RMS (均方根) 当波形图高度, emit 给前端画 canvas
        // 范围 0.0-1.0: 静音≈0, 正常说话 0.05-0.3, 大声 0.5+
        // 8000 samples (0.5s@16kHz) 在 i16 range [-32768, 32767]
        let mut sum_sq: u64 = 0;
        for s in &samples {
            let v = *s as i64;
            sum_sq += (v * v) as u64;
        }
        let rms = ((sum_sq as f64 / samples.len() as f64).sqrt()) / 32768.0;
        let _ = app.emit("audio-level", serde_json::json!({
            "rms": rms.min(1.0),
            "samples": samples.len(),
        }));

        // 30s 内一直静音 → 提示用户 (麦克风没声音最常见征兆)
        if rms < 0.001 {
            silence_streak += 1;
            if silence_streak == 30 {
                log::warn!("⚠️ 已连续 {}s 采集到静音 (RMS≈0)", silence_streak as f32 * 0.5);
                log::warn!("   可能原因: 1) 麦克风没启用 2) 选了错的设备 3) 系统静音/物理静音");
                log::warn!("   建议: 切右上角下拉选其他设备, 或在 '设置' 里确认");
            } else if silence_streak == 120 {
                log::warn!("⚠️ 已连续 {}s 静音, 用户可能没注意到", silence_streak as f32 * 0.5);
            }
        } else {
            if silence_streak >= 30 {
                log::info!("✓ 恢复声音 (静默 {}s 后)", silence_streak as f32 * 0.5);
            }
            silence_streak = 0;
        }

        // 满 30s 就切片上传
        if buffer.len() >= chunk_samples && auto_upload {
            let chunk: Vec<i16> = buffer.drain(..chunk_samples).collect();
            let wav_data = audio::encode_wav(&chunk, sample_rate)?;

            // 2026-06-25: TRAE 改的 upload_chunk 签名 (5 参数 + 返回 Vec)
            let overlap_sec = 0.0_f32;  // Phase B 没有 overlap, 后续 v1.2 加
            match upload::upload_chunk(
                &gpu_url,
                &meeting_id,
                wav_data,
                chunk_index,
                total_elapsed_sec,
                overlap_sec,
            )
            .await
            {
                Ok(segs) => {
                    ups.fetch_add(1, Ordering::SeqCst);
                    for seg in segs {
                        let _ = app.emit("transcript-segment", &seg);
                    }
                }
                Err(e) => {
                    let _ = app.emit("error", format!("上传失败: {e}"));
                }
            }
            chunk_index += 1;
            total_elapsed_sec += 30.0;
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
        state.gpu_url.lock().await,
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

/// 2026-06-26: fetch 改 invoke 系列 — 解决 Tauri webview 跨域 + CORS 预检
/// 之前前端用 `fetch(gpu_url + /api/meetings/.../chat)` 在 webview 里
/// 会因为 (a) 跨域 fetch 受限 (b) POST application/json 触发 OPTIONS 预检
/// → "Failed to fetch"。全部走 Rust reqwest 经 Tauri IPC 转发。
/// (2026-06-27: docs 走 SSE doc-status 自动推流, fetch_meeting_docs 删掉, 只保留 chat 系列)

#[tauri::command]
async fn fetch_meeting_chat_history(
    state: State<'_, AppState>,
    meeting_id: String,
) -> Result<serde_json::Value, String> {
    // 2026-06-27 修: GET /api/meetings/{id}/chat 在 ui_server.py 返回 404
    // (do_GET 只匹配 endswith("/chat/history")), 改成 history 路径
    let url = format!(
        "{}/api/meetings/{}/chat/history",
        state.gpu_url.lock().await,
        urlencoding::encode(&meeting_id)
    );
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("Chat 历史请求失败: {e}"))?;
    let body: serde_json::Value = resp.json()
        .await
        .map_err(|e| format!("Chat 历史解析失败: {e}"))?;
    Ok(body)
}

#[tauri::command]
async fn post_meeting_chat(
    state: State<'_, AppState>,
    meeting_id: String,
    message: String,
    context: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let url = format!(
        "{}/api/meetings/{}/chat",
        state.gpu_url.lock().await,
        urlencoding::encode(&meeting_id)
    );
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(150))
        .build()
        .map_err(|e| format!("Client 构建失败: {e}"))?;
    let resp = client
        .post(&url)
        .json(&serde_json::json!({
            "message": message,
            "context": context,
        }))
        .send()
        .await
        .map_err(|e| format!("Chat 发送失败: {e}"))?;
    let body: serde_json::Value = resp.json()
        .await
        .map_err(|e| format!("Chat 响应解析失败: {e}"))?;
    Ok(body)
}

/// SSE 接收主循环: 连接服务端事件流, 自动重连, 实时推送给前端
/// (2026-06-25 cherry-pick from feature/requirements-architecture-update 9bf5e18)
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

fn main() {
    // 2026-06-27: 客户端日志写文件 ~/.vpbuddy-client.log (排查 "采集不到声音" 等问题)
    // - 同时输出到 stderr (开发可见)
    // - 文件追加模式, 每次启动分隔一行 banner
    let log_path = std::env::var("VPBUDDY_CLIENT_LOG")
        .unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.vpbuddy-client.log")
        });
    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .ok();
    // 拼 stderr + 可选文件 双输出
    let mut builder = env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or("info,reqwest=warn,ureq=warn,h2=warn"),
    );
    builder.format(|buf, record| {
        use std::io::Write;
        writeln!(
            buf,
            "[{}] [{}] [{}:{}] {}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f"),
            record.level(),
            record.module_path().unwrap_or("?"),
            record.line().unwrap_or(0),
            record.args()
        )
    });
    if let Some(file) = log_file {
        builder.target(env_logger::Target::Pipe(Box::new(file)));
    }
    builder.init();
    log::info!("=== VPBuddy client 启动 (Tauri 2) ===");
    log::info!("日志文件: {}", log_path);
    log::info!("GPU server URL: {}", std::env::var("VPBUDDY_GPU_URL").unwrap_or_else(|_| "http://192.168.10.63:8765 (默认)".into()));
    log::info!("音频 host: {:?}, 默认输出设备: 待采集时打印",
        cpal::default_host().id());

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

            // 2026-06-26: 启动 GPU 连接心跳探针 (每 10s 探一次 /api/status)
            // 前端通过 listen("gpu-connection", ...) 收事件, 渲染绿/红/黄指示灯
            // 2026-06-27: 加防抖 — 连续 3 次失败才标红 (单次网络抖动不切)
            // 注意: tauri::State 不是 Send, 不能 spawn 后持有, 必须在每次 await 前取
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                use std::time::Duration;
                let client = reqwest::Client::builder()
                    .timeout(Duration::from_secs(3))
                    .build()
                    .unwrap();
                // 初始状态: 检测中
                let initial_url = app_handle.state::<AppState>().gpu_url.lock().await.clone();
                let _ = app_handle.emit("gpu-connection", serde_json::json!({
                    "status": "checking",
                    "detail": "正在检测 GPU 服务器...",
                    "url": initial_url,
                }));
                let mut fail_streak: u32 = 0;
                const FAIL_STREAK_RED: u32 = 3;
                loop {
                    tokio::time::sleep(Duration::from_secs(10)).await;
                    // 从 AppState 读当前 GPU URL (用户在设置页改了立刻生效)
                    let url = app_handle.state::<AppState>().gpu_url.lock().await.clone();
                    let probe = client.get(format!("{url}/api/status")).send().await;
                    match probe {
                        Ok(resp) if resp.status().is_success() => {
                            fail_streak = 0;
                            let _ = app_handle.emit("gpu-connection", serde_json::json!({
                                "status": "online",
                                "detail": format!("HTTP {}", resp.status().as_u16()),
                                "url": url,
                            }));
                        }
                        Ok(resp) => {
                            fail_streak += 1;
                            let label = if fail_streak >= FAIL_STREAK_RED { "offline" } else { "checking" };
                            let _ = app_handle.emit("gpu-connection", serde_json::json!({
                                "status": label,
                                "detail": format!("HTTP {} (连续 {fail_streak} 次)", resp.status().as_u16()),
                                "url": url,
                            }));
                        }
                        Err(e) => {
                            fail_streak += 1;
                            let label = if fail_streak >= FAIL_STREAK_RED { "offline" } else { "checking" };
                            let _ = app_handle.emit("gpu-connection", serde_json::json!({
                                "status": label,
                                "detail": format!("{e} (连续 {fail_streak} 次)"),
                                "url": url,
                            }));
                        }
                    }
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_capture,
            stop_capture,
            list_audio_devices,
            set_gpu_url,
            get_gpu_url,
            kb_search,
            fetch_meeting_chat_history,
            post_meeting_chat,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
