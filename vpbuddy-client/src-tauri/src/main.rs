// VPBuddy Desktop Client — Main entry (P2#6 2026-07-04)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio;
mod config;
mod upload;
use audio::AudioCapture;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::Arc;

use config::{AppState, load_client_config, set_log_path, get_log_path, save_gpu_url_to_yaml, client_config_path, ClientConfig, AudioConfig, SseConfig};
use tauri::{AppHandle, Emitter, Manager, State};

fn main() {
    // 2026-06-27: 客户端日志写文件 (排查 "采集不到声音" 等问题)
    // - 同时输出到 stderr (开发可见)
    // - 文件追加模式, 每次启动分隔一行 banner
    // - 跨平台: Windows 用 %USERPROFILE%\AppData\Local\VPBuddy\client.log
    //            macOS/Linux 用 $HOME/.vpbuddy-client.log
    //            VPBUDDY_CLIENT_LOG 环境变量可覆盖
    let log_path = std::env::var("VPBUDDY_CLIENT_LOG").unwrap_or_else(|_| {
        #[cfg(target_os = "windows")]
        {
            let base = std::env::var("USERPROFILE")
                .or_else(|_| std::env::var("HOME"))
                .unwrap_or_else(|_| "C:\\Users\\Default".into());
            let dir = format!("{base}\\AppData\\Local\\VPBuddy");
            let _ = std::fs::create_dir_all(&dir);
            format!("{dir}\\client.log")
        }
        #[cfg(not(target_os = "windows"))]
        {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.vpbuddy-client.log")
        }
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

    // 2026-06-27: 把路径存到全局, 设置页 invoke get_log_path 读
    set_log_path(log_path.clone());

    log::info!("=== VPBuddy client 启动 (Tauri 2) ===");

    // 2026-06-28: 启动时打印版本号 (build.rs 从 git describe 注入 env var)
    // 张胜东: "客户端和服务端 log 一开始就打印版本信息, 就能确认有没有更新"
    log::info!("🏷️  VPBuddy client version: {}", env!("VPBUDDY_VERSION"));
    log::info!("日志文件: {}", log_path);

    // 2026-06-28: GPU URL 显示 — env > yaml > hardcoded (跟 AppState::new 优先级一致)
    // 2026-07-03 ADR-0039: hardcoded fallback = 公网 GPU server (47.100.182.3:28765, ADR-0038)
    let gpu_url_display = std::env::var("VPBUDDY_GPU_URL")
        .ok()
        .or_else(|| load_client_config().map(|c| c.gpu_server_url))
        .unwrap_or_else(|| "http://47.100.182.3:28765 (默认, 无 yaml)".to_string());
    log::info!("GPU server URL: {gpu_url_display}");
    log::info!("配置文件路径: {}", client_config_path().display());
    log::info!("音频 host: {:?}, 默认输出设备: 待采集时打印",
        cpal::default_host().id());

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
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
            start_realtime_capture,
            stop_capture,
            list_audio_devices,
            set_gpu_url,
            get_gpu_url,
            get_log_path_cmd,
            open_log_dir_cmd,
            open_config_dir_cmd,
            kb_search,
            fetch_meeting_chat_history,
            post_meeting_chat,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[tauri::command]
async fn start_capture(
    app: AppHandle,
    state: State<'_, AppState>,
    auto_upload: bool,
    audio_device: Option<String>,
    meeting_id: Option<String>,
    audio_source: Option<String>,
    auth_token: Option<String>,
) -> Result<String, String> {
    if state.capturing.load(Ordering::SeqCst) {
        return Err("已在采集中".into());
    }
    // 校验 meeting_id
    let mid_in = meeting_id
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or_else(|| "请先选择或输入会议名 (ADR-0022)".to_string())?
        .to_string();
    // 校验 audio_source
    let audio_source_norm = audio_source
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("microphone")
        .to_lowercase();
    if !["microphone", "loopback", "both"].contains(&audio_source_norm.as_str()) {
        return Err(format!("非法 audio_source: {audio_source_norm}"));
    }

    log::info!("=== start_capture 触发 ===");
    log::info!("  audio_device: {:?}", audio_device);
    log::info!("  auto_upload: {}", auto_upload);
    log::info!("  meeting_id (用户选/建): {mid_in}");
    log::info!("  audio_source: {audio_source_norm}");

    // 1. 在 GPU 端 init 会议 (复用 UI 选的 meeting_id)
    let gpu_url = state.gpu_url.lock().await.clone();
    let meeting_id = upload::init_meeting(&gpu_url, &mid_in, &audio_source_norm, auth_token)
        .await
        .map_err(|e| {
            log::error!("init 会议失败: {e}");
            format!("init 会议失败: {e}")
        })?;
    log::info!("  ✓ meeting_id: {meeting_id}");
    *state.meeting_id.lock().await = Some(meeting_id.clone());
    // 2026-07-02 Phase 7: 写 audio_source 到共享 state, run_capture_loop 调 AudioCapture 时读
    *state.audio_source.lock().await = Some(audio_source_norm.clone());
    log::debug!("  ✓ audio_source 写入 state: {audio_source_norm}");

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
    // 2026-06-27: 共享 native 采样率, spawn_blocking 写, 主循环读 + resample
    let native_rate = state.native_sample_rate.clone();
    // 2026-07-02 Phase 7: 共享 audio_source (microphone/loopback/both), start_capture 已写, capture 线程读
    let audio_source_for_capture = state
        .audio_source
        .lock()
        .await
        .clone()
        .unwrap_or_else(|| "microphone".to_string());
    // 2026-06-27: 各 spawn 闭包 move 各自的 clone, 避免 use-of-moved-value
    // capture_emit 用闭包外的 clone, 供 run_capture_loop 退出后 emit error
    let app_clone_sse = app.clone();
    let app_clone_cap = app.clone();
    let app_clone_emit = app.clone();
    let gpu_url_sse = gpu_url.clone();
    let mid_sse = mid.clone();
    let capturing_sse = capturing.clone();

    // 2026-06-27: SSE task 提到 outer scope, 让 start_capture 能拿 JoinHandle 存进 state
    // (JoinHandle 不 Clone, 必须在 spawn caller 范围内)
    let sse_handle = tokio::spawn(async move {
        run_sse_loop(app_clone_sse, gpu_url_sse, mid_sse, capturing_sse).await;
    });
    *state.sse_handle.lock().await = Some(sse_handle);

    let handle = tokio::spawn(async move {
        // 音频采集 + 上传任务 (Phase B spawn_blocking, 保留我们之前的修复)
        // 2026-07-02 Phase 7: audio_source 已在 start_capture outer scope 读完 clone 进 audio_source_for_capture
        if let Err(e) = run_capture_loop(
            app_clone_cap,
            gpu_url,
            mid,
            capturing,
            bytes,
            ups,
            auto_upload,
            audio_device,
            native_rate,
            audio_source_for_capture,
        )
        .await
        {
            let _ = app_clone_emit.emit("error", format!("采集错误: {e}"));
        }

        // 采集结束, SSE task 自己检测 capturing=false 后退出 (loop 条件)
        // 不再 abort — stop_capture 会 await 这个 handle
    });

    *state.capture_handle.lock().await = Some(handle);
    Ok(meeting_id)
}

#[tauri::command]
async fn stop_capture(state: State<'_, AppState>) -> Result<(), String> {
    log::info!("=== stop_capture 触发 ===");
    log::info!("  capturing={} (设 false 中)", state.capturing.load(Ordering::SeqCst));
    state.capturing.store(false, Ordering::SeqCst);
    if let Some(h) = state.capture_handle.lock().await.take() {
        h.abort();
        log::info!("  采集 task 已 abort");
    } else {
        log::warn!("  capture_handle 为空, 没在跑采集?");
    }

    if let Some(mid) = state.meeting_id.lock().await.clone() {
        let gpu_url = state.gpu_url.lock().await.clone();
        let stop_url = format!("{}/api/meetings/{}/stream_stop", gpu_url, mid);
        log::info!("  POST {}", stop_url);
        match reqwest::Client::new()
            .post(&stop_url)
            .timeout(std::time::Duration::from_secs(3))
            .send()
            .await
        {
            Ok(r) => log::info!("  ✓ stream_stop 响应 {}", r.status()),
            Err(e) => log::warn!("  stream_stop 调用失败 (可忽略): {e}"),
        }
        log::info!("  meeting_id={}, SSE 继续等 GPU docs 完成", mid);
    } else {
        log::warn!("  meeting_id 为空, 没有活跃会议");
    }
    *state.audio_source.lock().await = None;
    log::debug!("  ✓ audio_source state 重置为 None (v0.8.0 cleanup)");
    log::info!("=== stop_capture 完成 ===");
    Ok(())
}

// ── 百炼实时转写模式 (v0.16) ──

#[tauri::command]
async fn start_realtime_capture(
    app: AppHandle,
    state: State<'_, AppState>,
    audio_device: Option<String>,
    meeting_id: Option<String>,
    audio_source: Option<String>,
    auth_token: Option<String>,
) -> Result<String, String> {
    if state.capturing.load(Ordering::SeqCst) {
        return Err("已在采集中".into());
    }
    let mid_in = meeting_id
        .as_deref().map(str::trim).filter(|s| !s.is_empty())
        .ok_or_else(|| "请先选择或输入会议名 (ADR-0022)".to_string())?
        .to_string();
    let audio_source_norm = audio_source
        .as_deref().map(str::trim).filter(|s| !s.is_empty())
        .unwrap_or("microphone").to_lowercase();
    if !["microphone", "loopback", "both"].contains(&audio_source_norm.as_str()) {
        return Err(format!("非法 audio_source: {audio_source_norm}"));
    }

    log::info!("=== start_realtime_capture (百炼 WS 模式) ===");
    log::info!("  meeting_id: {mid_in}, audio_source: {audio_source_norm}");

    let gpu_url = state.gpu_url.lock().await.clone();
    let meeting_id = upload::init_meeting(&gpu_url, &mid_in, &audio_source_norm, auth_token)
        .await.map_err(|e| format!("init 会议失败: {e}"))?;
    log::info!("  ✓ meeting_id: {meeting_id}");
    *state.meeting_id.lock().await = Some(meeting_id.clone());
    *state.audio_source.lock().await = Some(audio_source_norm.clone());

    state.capturing.store(true, Ordering::SeqCst);
    state.total_bytes.store(0, Ordering::SeqCst);
    state.total_uploads.store(0, Ordering::SeqCst);

    // SSE 照常启动 (收文档更新)
    let app_sse = app.clone();
    let gpu_url_sse = gpu_url.clone();
    let mid_sse = meeting_id.clone();
    let capturing_sse = state.capturing.clone();
    let sse_handle = tokio::spawn(async move {
        run_sse_loop(app_sse, gpu_url_sse, mid_sse, capturing_sse).await;
    });
    *state.sse_handle.lock().await = Some(sse_handle);

    let capturing2 = state.capturing.clone();
     let bytes2 = state.total_bytes.clone();
     let native2 = state.native_sample_rate.clone();
     let mid2 = meeting_id.clone();
     let src2 = audio_source_norm.clone();
     let dev2 = audio_device.clone();
     let app2 = app.clone();
     let handle = tokio::spawn(async move {
         if let Err(e) = run_realtime_loop(
             app, gpu_url, mid2,
             capturing2, bytes2,
             dev2, src2, native2,
         ).await {
             let _ = app2.emit("error", format!("实时采集错误: {e}"));
         }
     });

    *state.capture_handle.lock().await = Some(handle);
    Ok(meeting_id)
}

/// 百炼 WS 实时采集循环: cpal → resample 16kHz → 100ms PCM frame → WS send
pub async fn run_realtime_loop(
    app: AppHandle,
    gpu_url: String,
    meeting_id: String,
    capturing: Arc<AtomicBool>,
    bytes: Arc<AtomicU64>,
    audio_device: Option<String>,
    audio_source: String,
    native_rate: Arc<AtomicU32>,
) -> anyhow::Result<()> {
    use tokio::sync::mpsc as tmpsc;
    let (tx, mut rx) = tmpsc::channel::<Vec<i16>>(128);

    // cpal 采集 (spawn_blocking)
    let capturing_bg = capturing.clone();
    let native_rate_bg = native_rate.clone();
    let audio_source_bg = audio_source.clone();
    tokio::task::spawn_blocking(move || -> anyhow::Result<()> {
        let mut capture = AudioCapture::new_with_source(audio_device, &audio_source_bg)?;
        native_rate_bg.store(capture.native_sample_rate(), Ordering::SeqCst);
        log::info!("实时模式: cpal 就绪, native_rate={}", capture.native_sample_rate());
        while capturing_bg.load(Ordering::SeqCst) {
            match capture.read_chunk_blocking(0.1) {
                Ok(samples) if !samples.is_empty() => {
                    let _ = tx.blocking_send(samples);
                }
                Ok(_) => {}
                Err(e) => { log::error!("cpal 采集中断: {e}"); break; }
            }
        }
        log::info!("实时模式: cpal 退出");
        Ok(())
    });

    let sample_rate = 16000u32;
    // 连接百炼 WS
    let app_ws = app.clone();
    let ws = upload::BailianWsHandle::connect(
        &gpu_url, &meeting_id, sample_rate,
        move |text, bt, et, is_end| {
            let _ = app_ws.emit("transcript-segment", serde_json::json!({
                "text": text, "start_sec": bt, "end_sec": et,
                "speaker_id": "SPEAKER_00", "chunk_index": 0,
                "is_sentence_end": is_end,
            }));
        },
        move |err| {
            let _ = app.emit("error", format!("WS: {err}"));
        },
    ).await?;
    log::info!("实时模式: WS 已连接");

    let frame_size = (sample_rate as usize) / 10; // 100ms PCM frame
    let mut total_bytes_sent: u64 = 0;

    // 主循环: 收 audio → resample → 满 100ms → WS send
    loop {
        let samples = match tokio::time::timeout(
            std::time::Duration::from_secs(2), rx.recv(),
        ).await {
            Ok(Some(s)) => s,
            Ok(None) => break,
            Err(_) => {
                if !capturing.load(Ordering::SeqCst) { break; }
                continue;
            }
        };

        let native = native_rate.load(Ordering::SeqCst);
        let resampled: Vec<i16> = if native == sample_rate || native == 0 {
            samples
        } else {
            audio::resample_linear(&samples, native, sample_rate)
        };

        // 按 100ms 帧 pushing
        for chunk in resampled.chunks(frame_size) {
            let pcm_bytes: Vec<u8> = chunk.iter()
                .flat_map(|s| s.to_le_bytes())
                .collect();
            let frame_len = pcm_bytes.len() as u64;
            if ws.send_frame(pcm_bytes).await.is_err() {
                log::warn!("实时模式: WS 发送失败");
                break;
            }
            total_bytes_sent += frame_len;
            bytes.store(total_bytes_sent, Ordering::SeqCst);
        }
    }

    log::info!("实时模式: 循环结束, 等待 WS complete...");
    ws.join().await;
    log::info!("实时模式: 完成, 总计 {} bytes 发送", total_bytes_sent);

    // 触发文档生成 (等同 stream_stop → close)
    let close_url = format!("{}/api/meetings/{}/close", gpu_url, meeting_id);
    match reqwest::Client::new()
        .post(&close_url)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
    {
        Ok(r) => log::info!("close_meeting: HTTP {}", r.status()),
        Err(e) => log::warn!("close_meeting 调用失败: {e}"),
    }

    Ok(())
}

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

#[tauri::command]
async fn set_gpu_url(state: State<'_, AppState>, url: String) -> Result<(), String> {
    let trimmed = url.trim().to_string();
    if trimmed.is_empty() {
        return Err("地址不能为空".into());
    }
    if !trimmed.starts_with("http://") && !trimmed.starts_with("https://") {
        return Err("地址必须以 http:// 或 https:// 开头".into());
    }
    log::info!("set_gpu_url: {} -> {}", state.gpu_url.lock().await, trimmed);
    *state.gpu_url.lock().await = trimmed.clone();
    // 2026-06-28: 写回 ~/.vpbuddy-client.yaml, 重启后保持设置
    if let Err(e) = save_gpu_url_to_yaml(&trimmed) {
        log::warn!("set_gpu_url 写回 yaml 失败 (改 GPU URL 仍生效, 仅不持久化): {e}");
    }
    Ok(())
}

#[tauri::command]
async fn get_gpu_url(state: State<'_, AppState>) -> Result<String, String> {
    let url = state.gpu_url.lock().await.clone();
    log::info!("get_gpu_url: {}", url);
    Ok(url)
}

#[tauri::command]
async fn get_log_path_cmd() -> Result<String, String> {
    let p = get_log_path();
    log::info!("get_log_path_cmd: {}", p);
    Ok(p)
}

#[tauri::command]
async fn open_log_dir_cmd(app: AppHandle) -> Result<String, String> {
    use tauri_plugin_opener::OpenerExt;
    let p = get_log_path();
    log::info!("open_log_dir_cmd: reveal {}", p);
    if p == "(log path not initialized)" {
        return Err("日志未初始化".into());
    }
    app.opener()
        .reveal_item_in_dir(&p)
        .map_err(|e| format!("打开目录失败: {e}"))?;
    Ok(p)
}

#[tauri::command]
async fn open_config_dir_cmd(app: AppHandle) -> Result<String, String> {
    use tauri_plugin_opener::OpenerExt;
    let p = client_config_path();
    log::info!("open_config_dir_cmd: reveal {}", p.display());
    if !p.exists() {
        // 文件不存在, 写一个默认模板 (用户能直接编辑)
        let template = ClientConfig {
            // 2026-07-03 ADR-0039: 公网 GPU server 默认值 (ADR-0038)
            gpu_server_url: "http://47.100.182.3:28765".to_string(),
            audio: AudioConfig::default(),
            sse: SseConfig::default(),
        };
        if let Some(parent) = p.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(yaml) = serde_yaml::to_string(&template) {
            let _ = std::fs::write(&p, yaml);
            log::info!("已创建默认配置模板: {}", p.display());
        }
    }
    app.opener()
        .reveal_item_in_dir(&p)
        .map_err(|e| format!("打开目录失败: {e}"))?;
    Ok(p.to_string_lossy().to_string())
}

#[tauri::command]
async fn kb_search(
    state: State<'_, AppState>,
    query: String,
    top_k: u32,
) -> Result<Vec<serde_json::Value>, String> {
    log::info!("kb_search: query={:?} top_k={}", query, top_k);
    let url = format!("{}/api/kb/search?q={}&top_k={}",
        state.gpu_url.lock().await,
        urlencoding::encode(&query),
        top_k);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| { log::warn!("kb_search 请求失败: {e}"); format!("KB 请求失败: {e}") })?;
    let body: serde_json::Value = resp.json()
        .await
        .map_err(|e| { log::warn!("kb_search 解析失败: {e}"); format!("KB 解析失败: {e}") })?;
    let n = body["results"].as_array().map(|a| a.len()).unwrap_or(0);
    log::info!("kb_search: 命中 {} 条", n);
    Ok(body["results"].as_array().cloned().unwrap_or_default())
}

#[tauri::command]
async fn fetch_meeting_chat_history(
    state: State<'_, AppState>,
    meeting_id: String,
) -> Result<serde_json::Value, String> {
    // 2026-06-27 修: GET /api/meetings/{id}/chat 在 ui_server.py 返回 404
    // (do_GET 只匹配 endswith("/chat/history")), 改成 history 路径
    log::info!("fetch_meeting_chat_history: meeting_id={:?}", meeting_id);
    let url = format!(
        "{}/api/meetings/{}/chat/history",
        state.gpu_url.lock().await,
        urlencoding::encode(&meeting_id)
    );
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| { log::warn!("fetch_meeting_chat_history 请求失败: {e}"); format!("Chat 历史请求失败: {e}") })?;
    let body: serde_json::Value = resp.json()
        .await
        .map_err(|e| { log::warn!("fetch_meeting_chat_history 解析失败: {e}"); format!("Chat 历史解析失败: {e}") })?;
    log::info!("fetch_meeting_chat_history: 收到 {} 字节", body.to_string().len());
    Ok(body)
}

#[tauri::command]
async fn post_meeting_chat(
    state: State<'_, AppState>,
    meeting_id: String,
    message: String,
    context: serde_json::Value,
) -> Result<serde_json::Value, String> {
    log::info!("post_meeting_chat: meeting_id={:?} message={:?}", meeting_id, message);
    let url = format!(
        "{}/api/meetings/{}/chat",
        state.gpu_url.lock().await,
        urlencoding::encode(&meeting_id)
    );
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(150))
        .build()
        .map_err(|e| { log::warn!("post_meeting_chat Client 构建失败: {e}"); format!("Client 构建失败: {e}") })?;
    let resp = client
        .post(&url)
        .json(&serde_json::json!({
            "message": message,
            "context": context,
        }))
        .send()
        .await
        .map_err(|e| { log::warn!("post_meeting_chat 发送失败: {e}"); format!("Chat 发送失败: {e}") })?;
    let body: serde_json::Value = resp.json()
        .await
        .map_err(|e| { log::warn!("post_meeting_chat 响应解析失败: {e}"); format!("Chat 响应解析失败: {e}") })?;
    log::info!("post_meeting_chat: 收到响应 ({} 字节)", body.to_string().len());
    Ok(body)
}
// === run_capture_loop ===
pub async fn run_capture_loop(
    app: AppHandle,
    gpu_url: String,
    meeting_id: String,
    capturing: Arc<AtomicBool>,
    bytes: Arc<AtomicU64>,
    ups: Arc<AtomicU64>,
    auto_upload: bool,
    audio_device: Option<String>,
    // 2026-06-27: 共享 capture 设备原生采样率, 主循环 resample 用
    native_rate: Arc<AtomicU32>,
    // 2026-07-02 Phase 7: audio_source (microphone/loopback/both), 传给 AudioCapture new_with_source
    audio_source: String,
) -> anyhow::Result<()> {
    use tokio::sync::mpsc as tmpsc;
    let (tx, mut rx) = tmpsc::channel::<Vec<i16>>(64);

    // 1. spawn_blocking 跑 cpal 采集 — 不要求 Send (跑在专用 blocking pool)
    let capturing_bg = capturing.clone();
    let native_rate_bg = native_rate.clone();  // 2026-06-27: clone 给 bg, 主循环保留自己的
    let audio_source_bg = audio_source.clone();  // 2026-07-02 Phase 7: clone 给 bg, AudioCapture 用
    let _capture_handle = tokio::task::spawn_blocking(move || -> anyhow::Result<()> {
        // 2026-07-02 Phase 7: 走 new_with_source 路由 (microphone/loopback/both)
        log::info!("cpal: new_with_source({:?}, audio_source={:?})", audio_device, audio_source_bg);
        let mut capture = match AudioCapture::new_with_source(audio_device, &audio_source_bg) {
            Ok(c) => {
                log::info!("  ✓ AudioCapture 初始化成功 (host={:?})", cpal::default_host().id());
                // 2026-06-27: 把设备 native 采样率写到共享 atomic, 主循环 resample 用
                native_rate_bg.store(c.native_sample_rate(), Ordering::SeqCst);
                log::info!("  → native_sample_rate = {}", c.native_sample_rate());
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
            // 2026-06-27: read_chunk_blocking 现在内部用 capture.native_sample_rate (设备原生)
            let samples = capture.read_chunk_blocking(0.5)?;
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
    // 30s 切片缓冲 (480000 samples = 30s @ 16kHz)
    let chunk_samples = (sample_rate as usize) * 30;
    let mut buffer: Vec<i16> = Vec::with_capacity(chunk_samples);
    // 2026-06-25: 切片序号 (TRAE upload_chunk API 需要)
    let mut chunk_index: u64 = 0;
    let mut total_elapsed_sec: f32 = 0.0;
    // 2026-06-27: 累计 0-RMS 帧数, 30s 内全是 0 (即 60 帧) 就 warn 一次
    let mut silence_streak: u32 = 0;
    // 2026-06-28: 音频卡死检测 — rx.recv() 等不到 chunk 时, 5s 后 warn + 强制 break
    // 让外层 while 重新检查 capturing, 而不是永远 hang (张胜东 02:13 案例)
    let mut last_recv = std::time::Instant::now();
    const STALL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);
    // 2026-06-29: time-based 切片 — 30s 到了强制切 (不用等满 480000 samples)
    // 张胜东反馈: 录 30s 差 0.5s 没满 chunk_samples, 切片永远不触发
    let mut last_chunk_at = std::time::Instant::now();
    const CHUNK_TIME_SECS: f32 = 30.0;

    // 2. tokio task 拼切片 + POST
    while capturing.load(Ordering::SeqCst) {
        let samples = match tokio::time::timeout(STALL_TIMEOUT, rx.recv()).await {
            Ok(Some(s)) => s,
            Ok(None) => break,  // channel 关闭 (cpal 异常退出)
            Err(_) => {
                // 5s 没新 chunk — 音频驱动 hang, 不要永远 hang
                log::warn!("⚠️ audio stall: 5s 没新 sample (cpal/USB 麦克风驱动可能挂)");
                log::warn!("   建议: 切换右上角下拉麦克风, 或重启 Windows Audio 服务");
                break;
            }
        };
        last_recv = std::time::Instant::now();
        let _ = last_recv; // suppress unused warning
        // 2026-06-27: 设备 native 采样率 != 16kHz 时, 软件重采样到 16kHz
        // (cpal 用设备原生采样率避免 WASAPI 拒 16kHz, 主循环做重采样)
        let native = native_rate.load(Ordering::SeqCst);
        if native == sample_rate || native == 0 {
            buffer.extend(samples.iter().copied());
        } else {
            let resampled = audio::resample_linear(&samples, native, sample_rate);
            buffer.extend(resampled);
        }

        // 累计字节数
        let n = (buffer.len() * 2) as u64;
        bytes.store(n, Ordering::SeqCst);
        let _ = app.emit("capture-stats", serde_json::json!({
            "bytes": n,
            "uploads": ups.load(Ordering::SeqCst),
        }));
        // 2026-06-29: 每 10% 记录一次缓冲状态 (张胜东要求所有行为加日志)
        let buf_pct = buffer.len() as f32 / chunk_samples as f32 * 100.0;
        if chunk_index == 0 && buf_pct > 0.0 && (buf_pct as u32) % 10 == 0 {
            log::info!("📦 缓冲进度: {:.0}% ({} / {} samples, {:.1}s)",
                buf_pct, buffer.len(), chunk_samples,
                buffer.len() as f32 / sample_rate as f32);
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

        // 满 30s 就切片上传 (size-based OR time-based)
        // 张胜东反馈: 30s 差 0.5s 没满 480000, 切片永远不触发 — 改 time-based
        let elapsed_sec = last_chunk_at.elapsed().as_secs_f32();
        let should_chunk_size = buffer.len() >= chunk_samples;
        let should_chunk_time = elapsed_sec >= CHUNK_TIME_SECS && !buffer.is_empty();
        if (should_chunk_size || should_chunk_time) && auto_upload {
            let (start_t, end_t) = if should_chunk_size {
                (buffer.len() - chunk_samples, buffer.len())
            } else {
                log::info!("⏰ time-based 切片触发 ({}s, buffer={} samples < 满)",
                    elapsed_sec, buffer.len());
                (0, buffer.len())
            };
            let chunk: Vec<i16> = buffer.drain(start_t..end_t).collect();
            let wav_data = audio::encode_wav(&chunk, sample_rate)?;
            log::info!("📤 上传 chunk #{}: {} samples ({:.1}s 音频, trigger={})",
                chunk_index, chunk.len(), chunk.len() as f32 / sample_rate as f32,
                if should_chunk_size { "size" } else { "time" });

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
                    log::info!("✅ chunk #{} 上传成功, server 返回 {} 段 transcript-segment",
                        chunk_index, segs.len());
                    for seg in segs {
                        let _ = app.emit("transcript-segment", &seg);
                    }
                }
                Err(e) => {
                    log::error!("❌ chunk #{} 上传失败: {e}", chunk_index);
                    let _ = app.emit("error", format!("上传失败: {e}"));
                }
            }
            chunk_index += 1;
            total_elapsed_sec += chunk.len() as f32 / sample_rate as f32;
            last_chunk_at = std::time::Instant::now();
        }
    }

    Ok(())
}

/// SSE 接收主循环: 连接服务端事件流, 自动重连, 实时推送给前端

// === run_sse_loop ===
pub async fn run_sse_loop(
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

        // 2026-06-27: 用 tokio::select! 让 capturing=false 立即退出 (不等到 reqwest close)
        tokio::select! {
            r = connect_and_read_sse(&app, &connect_url, capturing.clone(), &mut last_event_id) => {
                match r {
                    Ok(()) => {
                        log::info!("SSE 正常断开");
                        let _ = app.emit("connection-status", serde_json::json!({"sse": "disconnected"}));
                        break;
                    }
                    Err(e) => {
                        log::warn!("SSE 断开: {e}, 准备重连...");
                        let _ = app.emit("connection-status", serde_json::json!({"sse": "disconnected"}));
                        retry_count += 1;
                        // 指数退避: 1s, 2s, 4s, 8s, 最多 10s
                        let delay = (1u64 << retry_count.min(3)) * 1000;
                        let delay = delay.min(10_000);
                        tokio::time::sleep(std::time::Duration::from_millis(delay)).await;
                    }
                }
            }
            _ = wait_capturing_false(capturing.clone()) => {
                log::info!("SSE loop 检测到 capturing=false, 立即退出");
                break;
            }
        }
    }
}

/// 2026-06-27: 等待 capturing 变 false (被 stop_capture 设) — 用于 select! 立即退出
async fn wait_capturing_false(capturing: Arc<AtomicBool>) {
    while capturing.load(Ordering::SeqCst) {
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
}

/// 单次 SSE 连接与读取
async fn connect_and_read_sse(
    app: &AppHandle,
    url: &str,
    capturing: Arc<AtomicBool>,
    last_event_id: &mut Option<String>,
) -> anyhow::Result<()> {
    // 2026-06-28: 关闭 reqwest 全局 timeout — SSE 是长连接, 30s 必报
    // "error decoding response body" 然后断开。原 timeout(30) 是 bug。
    // 我们用 tokio::select! + wait_capturing_false 自己控退出。
    let client = reqwest::Client::builder()
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
            // 2026-06-27: 加日志 — 用户要求"客户端日志记录所有 SSE 事件"
            let cleaned = payload.get("cleaned").and_then(|v| v.as_bool()).unwrap_or(false);
            let text = payload.get("text").and_then(|v| v.as_str()).map(|s| &s[..s.len().min(80)]);
            log::info!("📝 transcript-segment: cleaned={} spk={:?} text={:?}",
                cleaned, payload.get("speaker_id"), text);
            // 2026-06-29: 收到 SSE transcript-segment 时, 记录完整 payload (去重音)
            let text_len = payload.get("text").and_then(|v| v.as_str()).map(|s| s.len()).unwrap_or(0);
            log::debug!("transcript-segment payload keys: {:?}, text_len={}",
                payload.as_object().map(|o| o.keys().collect::<Vec<_>>()), text_len);
            let _ = app.emit("transcript-segment", &payload);
        }
        "state-update" => {
            log::info!("📊 state-update: {}", payload);
            let _ = app.emit("state-update", &payload);
        }
        "doc-update" => {
            log::info!("📄 doc-update: {}", payload);
            let _ = app.emit("doc-status", &payload);
        }
        // 2026-06-28 ADR-0018: GPU 6 docs 全 stored 后推 meeting-complete
        "meeting-complete" => {
            log::info!("🎉 meeting-complete: 6 docs 全 stored, SSE 即将关闭");
            let _ = app.emit("meeting-complete", &payload);
        }
        "chat-message" => {
            log::info!("💬 chat-message");
            let _ = app.emit("chat-message", &payload);
        }
        "metrics-update" => {
            log::debug!("📈 metrics-update");
            let _ = app.emit("metrics-update", &payload);
        }
        "connected" => {
            log::info!("✅ SSE connected: {}", payload);
            let _ = app.emit("connection-status", serde_json::json!({"sse": "connected"}));
        }
        "heartbeat" => {
            // heartbeat 每 30s 一次, 用 debug 级别避免日志爆炸
            log::debug!("💓 heartbeat: {}", payload);
            let _ = app.emit("connection-status", serde_json::json!({"sse": "heartbeat"}));
        }
        "timeout" => {
            log::warn!("⏱️ SSE timeout event received");
        }
        other => {
            // 其他事件统一转发
            let _ = app.emit("server-event", &payload);
            log::info!("🔔 SSE event [{}]: {}", other, payload);
        }
    }
}