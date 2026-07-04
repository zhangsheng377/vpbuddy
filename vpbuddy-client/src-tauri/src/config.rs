// VPBuddy Desktop Client -- Config module (P2#6 2026-07-04)

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64};
use std::sync::Arc;
use tokio::sync::Mutex;

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


pub fn client_config_path() -> std::path::PathBuf {
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

    pub fn new() -> Self {
        // 2026-06-28: GPU URL 优先级链 — env var > yaml > hardcoded fallback
        // env var 用于临时调试 (不开 GUI 改文件), yaml 用于持久化 (用户改设置)
        // 2026-07-03 ADR-0039: hardcoded fallback = 公网 GPU server (47.100.182.3:28765, ADR-0038)
        // 之前是 http://gpu.zhangshengdong.com:8765 (LAN IPv6-only 域名, V 家网解析不到, ADR-0036)
        let url = std::env::var("VPBUDDY_GPU_URL")
            .ok()
            .or_else(|| load_client_config().map(|c| c.gpu_server_url))
            .unwrap_or_else(|| "http://47.100.182.3:28765".to_string());
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

#[allow(dead_code)]
pub fn get_log_path() -> String {
    let p = dirs::data_dir().unwrap_or_else(|| PathBuf::from("."));
    let dir = p.join("vpbuddy-client");
    std::fs::create_dir_all(&dir).ok();
    dir.join("vpbuddy-client.log").to_string_lossy().to_string()
}

/// Set the log path for the client
pub fn set_log_path(p: String) {
    use std::sync::OnceLock;
    static LOG_PATH: OnceLock<std::sync::Mutex<String>> = OnceLock::new();
    let lock = LOG_PATH.get_or_init(|| std::sync::Mutex::new(String::new()));
    if let Ok(mut v) = lock.lock() {
        *v = p;
    }
}

