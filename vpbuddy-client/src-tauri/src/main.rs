// VPBuddy Desktop Client — Main entry (P2#6 2026-07-04)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio;
mod config;
mod commands;
mod upload;

use config::AppState;
use commands::*;

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
