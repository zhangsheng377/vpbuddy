// VPBuddy Desktop Client — Tauri Rust 后端
// 设计: 持续抓系统音频 (cpal) → 16kHz mono PCM → 切片 30s → 推 GPU server
//       接收 GPU SSE 流 (transcript-segment / doc-status) → emit 到前端
//
// 关键约束:
// - 跨平台音频: Linux=PipeWire, macOS=CoreAudio+BlackHole, Windows=WASAPI
// - 不上 Rust streaming funasr (太重), GPU 端切片 batch ASR
// - 复用 Python vpbuddy 的 /api/meetings/upload 端点 (已 commit 05a2664)

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
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
    /// 2026-06-27: 改 Mutex 支持运行时修改 (设置页 GPU URL 输入)
    pub gpu_url: Arc<Mutex<String>>,
    pub meeting_id: Arc<Mutex<Option<String>>>,
    /// 2026-06-27: SSE 接收 task handle, stop_capture 时 await 它自然退出
    pub sse_handle: Arc<Mutex<Option<tokio::task::JoinHandle<()>>>>,
    /// 2026-06-27: 客户端日志路径 (init 时填, 设置页 invoke get_log_path 读)
    pub log_path: Arc<Mutex<String>>,
    /// 2026-06-27: 设备原生采样率 (capture 创建时填), 主循环用来 resample 16kHz
    pub native_sample_rate: Arc<AtomicU32>,
    /// 2026-07-02 Phase 7: 当前会议 audio_source (microphone/loopback/both)
    /// start_capture 写, run_capture_loop 读后传给 AudioCapture.
    pub audio_source: Arc<Mutex<Option<String>>>,
}

/// 2026-06-27: 全局日志路径, get_log_path invoke 命令读这里
static LOG_PATH: std::sync::OnceLock<String> = std::sync::OnceLock::new();
fn set_log_path(p: String) { let _ = LOG_PATH.set(p); }
pub fn get_log_path() -> String {
    LOG_PATH.get().cloned().unwrap_or_else(|| "(log path not initialized)".to_string())
}

/// 2026-06-28: 客户端配置 (从 ~/.vpbuddy-client.yaml 读, install-client.sh 自动生成)
/// 统一管理 GPU server URL + 音频参数 + SSE 配置 — 不再 3 处硬编码默认值
#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
pub struct ClientConfig {
    pub gpu_server_url: String,
    #[serde(default)]
    pub audio: AudioConfig,
    #[serde(default)]
    pub sse: SseConfig,
}

#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
pub struct AudioConfig {
    #[serde(default = "default_sample_rate")]
    pub sample_rate: u32,
    #[serde(default = "default_chunk_seconds")]
    pub chunk_seconds: u32,
    #[serde(default)]
    pub overlap_seconds: u32,
}
impl Default for AudioConfig {
    fn default() -> Self {
        Self {
            sample_rate: default_sample_rate(),
            chunk_seconds: default_chunk_seconds(),
            overlap_seconds: 0,
        }
    }
}

#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
pub struct SseConfig {
    #[serde(default = "default_true")]
    pub reconnect: bool,
    #[serde(default = "default_max_events")]
    pub max_events_per_chunk: u32,
}
impl Default for SseConfig {
    fn default() -> Self {
        Self {
            reconnect: true,
            max_events_per_chunk: default_max_events(),
        }
    }
}

fn default_sample_rate() -> u32 { 16000 }
fn default_chunk_seconds() -> u32 { 30 }
fn default_true() -> bool { true }
fn default_max_events() -> u32 { 50 }

/// 客户端配置文件路径 (跨平台: Linux/macOS=$HOME, Windows=%USERPROFILE%)
fn client_config_path() -> std::path::PathBuf {
    #[cfg(target_os = "windows")]
    let base = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| "C:\\Users\\Default".into());
    #[cfg(not(target_os = "windows"))]
    let base = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    std::path::PathBuf::from(base).join(".vpbuddy-client.yaml")
}

/// 从 ~/.vpbuddy-client.yaml 读 config — 文件不存在/解析失败 返回 None
/// (install-client.sh 负责首次写入默认 yaml, Rust 不主动生成)
pub fn load_client_config() -> Option<ClientConfig> {
    let path = client_config_path();
    if !path.exists() {
        log::warn!("客户端配置不存在: {} — 用硬编码默认值", path.display());
        return None;
    }
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) => {
            log::warn!("读 {} 失败: {e} — 用硬编码默认值", path.display());
            return None;
        }
    };
    match serde_yaml::from_str::<ClientConfig>(&content) {
        Ok(c) => {
            log::info!("客户端配置已加载: {}", path.display());
            Some(c)
        }
        Err(e) => {
            log::warn!("yaml 解析失败: {e} — 用硬编码默认值");
            None
        }
    }
}

/// set_gpu_url 改后写回 yaml — 用户改设置自动持久化
pub fn save_gpu_url_to_yaml(url: &str) -> anyhow::Result<()> {
    let path = client_config_path();
    // 读现有 yaml (或用默认), 改 gpu_server_url, 写回
    let mut cfg: ClientConfig = match std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_yaml::from_str(&s).ok())
    {
        Some(c) => c,
        None => ClientConfig {
            gpu_server_url: url.to_string(),
            audio: AudioConfig::default(),
            sse: SseConfig::default(),
        },
    };
    cfg.gpu_server_url = url.to_string();
    let yaml = serde_yaml::to_string(&cfg).map_err(|e| anyhow::anyhow!("yaml 序列化: {e}"))?;
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    std::fs::write(&path, yaml).map_err(|e| anyhow::anyhow!("写 {} 失败: {e}", path.display()))?;
    log::info!("GPU URL 已写回 yaml: {}", path.display());
    Ok(())
}

impl AppState {
    fn new() -> Self {
        // 2026-06-28: GPU URL 优先级链 — env var > yaml > hardcoded fallback
        // env var 用于临时调试 (不开 GUI 改文件), yaml 用于持久化 (用户改设置)
        let url = std::env::var("VPBUDDY_GPU_URL")
            .ok()
            .or_else(|| load_client_config().map(|c| c.gpu_server_url))
            .unwrap_or_else(|| "http://gpu.zhangshengdong.com:8765".to_string());
        Self {
            capturing: Arc::new(AtomicBool::new(false)),
            capture_handle: Arc::new(Mutex::new(None)),
            total_bytes: Arc::new(AtomicU64::new(0)),
            total_uploads: Arc::new(AtomicU64::new(0)),
            gpu_url: Arc::new(Mutex::new(url)),
            meeting_id: Arc::new(Mutex::new(None)),
            sse_handle: Arc::new(Mutex::new(None)),
            log_path: Arc::new(Mutex::new(String::new())),
            // 2026-06-27: 初始 16000 (假设 16kHz native), capture 创建时更新到设备实际值
            native_sample_rate: Arc::new(AtomicU32::new(16000)),
            // 2026-07-02 Phase 7: 初始 None, start_capture 时填
            audio_source: Arc::new(Mutex::new(None)),
        }
    }
}

/// 启动采集 (VP 点"开始录音")
///
/// 2026-07-01:
/// - ADR-0022: meeting_id 必填 (UI 选/建的), 不允许自动建
/// - ADR-0021: audio_source 参数 (microphone/loopback/both, 默认 microphone)
#[tauri::command]
async fn start_capture(
    app: AppHandle,
    state: State<'_, AppState>,
    auto_upload: bool,
    audio_device: Option<String>,
    meeting_id: Option<String>,
    audio_source: Option<String>,
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
    let meeting_id = upload::init_meeting(&gpu_url, &mid_in, &audio_source_norm)
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

/// 停止采集 (2026-06-28 ADR-0018: 改语义)
/// - 旧: 关 SSE, 立即断 → GPU 后台 6 docs 推送不到客户端
/// - 新: 只设 capturing=false 让音频停 + POST stream_stop 通知服务端
///   SSE **不立即关**, 让 GPU 后台 6 docs 完成时 push 过来
///   GPU 6 docs 全 stored 后 close_meeting → SSE 自然退出
/// - "会议真正停止"留给以后加"结束会议"按钮 (张胜东决策)
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

    // 2026-06-28 ADR-0018: 通知服务端 audio capture 已停, 但 SSE 继续
    // 等 GPU 后台 6 docs 全 stored 后 close_meeting 触发 SSE 自然退出
    // 不再 await sse_handle (老逻辑 1.5s 超时)
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
    // 2026-07-02 Phase 7 v0.8.0 cleanup: 重置 audio_source 状态
    // (v0.7.1 留值不重置, ADR-0032 写明本 PR 收尾)
    *state.audio_source.lock().await = None;
    log::debug!("  ✓ audio_source state 重置为 None (v0.8.0 cleanup)");

    // 注意: sse_handle 不 await — SSE 继续等 GPU 6 docs 完成 + close_meeting
    log::info!("=== stop_capture 完成 ===");
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
    log::info!("set_gpu_url: {} -> {}", state.gpu_url.lock().await, trimmed);
    *state.gpu_url.lock().await = trimmed.clone();
    // 2026-06-28: 写回 ~/.vpbuddy-client.yaml, 重启后保持设置
    if let Err(e) = save_gpu_url_to_yaml(&trimmed) {
        log::warn!("set_gpu_url 写回 yaml 失败 (改 GPU URL 仍生效, 仅不持久化): {e}");
    }
    Ok(())
}

/// 2026-06-26: 返回当前 GPU server URL (前端 fetch API 用)
#[tauri::command]
async fn get_gpu_url(state: State<'_, AppState>) -> Result<String, String> {
    let url = state.gpu_url.lock().await.clone();
    log::info!("get_gpu_url: {}", url);
    Ok(url)
}

/// 2026-06-27: 返回客户端日志文件路径 (设置页展示)
#[tauri::command]
async fn get_log_path_cmd() -> Result<String, String> {
    let p = get_log_path();
    log::info!("get_log_path_cmd: {}", p);
    Ok(p)
}

/// 2026-06-27: 在系统文件管理器中显示日志文件 (跨平台 reveal-in-folder)
/// 用 tauri-plugin-opener 的 reveal_item_in_dir (Win/Mac/Linux 都支持)
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

/// 2026-06-28: 在系统文件管理器中显示 ~/.vpbuddy-client.yaml
/// 文件不存在则创建空模板, 让用户看到能立刻编辑
#[tauri::command]
async fn open_config_dir_cmd(app: AppHandle) -> Result<String, String> {
    use tauri_plugin_opener::OpenerExt;
    let p = client_config_path();
    log::info!("open_config_dir_cmd: reveal {}", p.display());
    if !p.exists() {
        // 文件不存在, 写一个默认模板 (用户能直接编辑)
        let template = ClientConfig {
            gpu_server_url: "http://gpu.zhangshengdong.com:8765".to_string(),
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

/// GPU 端 KB 检索 (本地客户端不能直接连 KB, 走 GPU server 代理)
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
    let gpu_url_display = std::env::var("VPBUDDY_GPU_URL")
        .ok()
        .or_else(|| load_client_config().map(|c| c.gpu_server_url))
        .unwrap_or_else(|| "http://gpu.zhangshengdong.com:8765 (默认, 无 yaml)".to_string());
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
