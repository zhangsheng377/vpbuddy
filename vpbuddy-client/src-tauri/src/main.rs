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
    *state.audio_source.lock().await = None;
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
    let meeting_id = upload::init_meeting(&gpu_url, &mid_in, &audio_source_norm, auth_token.clone())
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
    let auth_tok_sse2 = auth_token.clone();
    let sse_handle = tokio::spawn(async move {
        run_sse_loop(app_sse, gpu_url_sse, mid_sse, capturing_sse, auth_tok_sse2).await;
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

    let (init_tx, init_rx) = tokio::sync::oneshot::channel::<Result<u32, String>>();

    let capturing_bg = capturing.clone();
    let native_rate_bg = native_rate.clone();
    let audio_source_bg = audio_source.clone();
    tokio::task::spawn_blocking(move || {
        let mut capture = match AudioCapture::new_with_source(audio_device, &audio_source_bg) {
            Ok(c) => {
                let rate = c.native_sample_rate();
                let _ = init_tx.send(Ok(rate));
                c
            }
            Err(e) => {
                let _ = init_tx.send(Err(format!("{e}")));
                return;
            }
        };
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
    });

    match init_rx.await {
        Ok(Ok(_rate)) => {}
        Ok(Err(msg)) => {
            capturing.store(false, Ordering::SeqCst);
            let _ = app.emit("error", format!("音频设备初始化失败: {msg}"));
            anyhow::bail!("CPAL init failed: {msg}");
        }
        Err(_) => {
            capturing.store(false, Ordering::SeqCst);
            let _ = app.emit("error", "音频设备初始化异常");
            anyhow::bail!("CPAL init failed (channel dropped)");
        }
    }

    let sample_rate = 16000u32;
    let app_stats = app.clone();
    let app_ws = app.clone();
    let ws = match upload::BailianWsHandle::connect(
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
    ).await {
        Ok(ws) => ws,
        Err(e) => {
            capturing.store(false, Ordering::SeqCst);
            anyhow::bail!("WS connect failed: {e}");
        }
    };
    log::info!("实时模式: WS 已连接");

    let frame_size = (sample_rate as usize) / 10;
    let mut total_bytes_sent: u64 = 0;

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

        for chunk in resampled.chunks(frame_size) {
            let sum_sq: f64 = chunk.iter().map(|&s| (s as f64) * (s as f64)).sum();
            let rms = (sum_sq / chunk.len() as f64).sqrt();
            let normalized = (rms / 32768.0).min(1.0);
            let _ = app_stats.emit("audio-level", serde_json::json!({"rms": normalized}));

            let pcm_bytes: Vec<u8> = chunk.iter()
                .flat_map(|s| s.to_le_bytes())
                .collect();
            let frame_len = pcm_bytes.len() as u64;
            if let Err(e) = ws.send_frame(pcm_bytes).await {
                log::error!("实时模式: WS 发送失败: {e}");
                let _ = app_stats.emit("error", format!("实时转写连接断开: {e}"));
                capturing.store(false, Ordering::SeqCst);
                anyhow::bail!("WS send failed: {e}");
            }
            total_bytes_sent += frame_len;
            bytes.store(total_bytes_sent, Ordering::SeqCst);
            let _ = app_stats.emit("capture-stats", serde_json::json!({
                "bytes": total_bytes_sent,
                "uploads": 0u32,
            }));
        }
    }

    log::info!("实时模式: 循环结束, 等待 WS complete...");
    ws.join().await;
    log::info!("实时模式: 完成, 总计 {} bytes 发送", total_bytes_sent);

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
    auth_token: Option<String>,
) -> Result<Vec<serde_json::Value>, String> {
    log::info!("kb_search: query={:?} top_k={}", query, top_k);
    let url = format!("{}/api/kb/search?q={}&top_k={}",
        state.gpu_url.lock().await,
        urlencoding::encode(&query),
        top_k);
    let client = reqwest::Client::new();
    let mut req = client.get(&url);
    if let Some(tok) = &auth_token {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req.send()
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
    auth_token: Option<String>,
) -> Result<serde_json::Value, String> {
    log::info!("fetch_meeting_chat_history: meeting_id={:?}", meeting_id);
    let url = format!(
        "{}/api/meetings/{}/chat/history",
        state.gpu_url.lock().await,
        urlencoding::encode(&meeting_id)
    );
    let client = reqwest::Client::new();
    let mut req = client.get(&url);
    if let Some(tok) = &auth_token {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req.send()
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
    auth_token: Option<String>,
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
    let mut req = client
        .post(&url)
        .json(&serde_json::json!({
            "message": message,
            "context": context,
        }));
    if let Some(tok) = &auth_token {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req.send()
        .await
        .map_err(|e| { log::warn!("post_meeting_chat 发送失败: {e}"); format!("Chat 发送失败: {e}") })?;
    let body: serde_json::Value = resp.json()
        .await
        .map_err(|e| { log::warn!("post_meeting_chat 响应解析失败: {e}"); format!("Chat 响应解析失败: {e}") })?;
    log::info!("post_meeting_chat: 收到响应 ({} 字节)", body.to_string().len());
    Ok(body)
}
// === run_sse_loop ===
pub async fn run_sse_loop(
    app: AppHandle,
    gpu_url: String,
    meeting_id: String,
    capturing: Arc<AtomicBool>,
    auth_token: Option<String>,
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
            r = connect_and_read_sse(&app, &connect_url, capturing.clone(), &mut last_event_id, auth_token.clone()) => {
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
    auth_token: Option<String>,
) -> anyhow::Result<()> {
    // 2026-06-28: 关闭 reqwest 全局 timeout — SSE 是长连接, 30s 必报
    let client = reqwest::Client::builder()
        .build()?;

    let mut req = client.get(url);
    if let Some(tok) = &auth_token {
        req = req.header("Authorization", format!("Bearer {tok}"));
    }
    let resp = req.send().await?;
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