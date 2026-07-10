// VPBuddy Desktop Client -- Config module (P2#6 2026-07-04)

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

use std::sync::OnceLock;
use std::sync::Mutex as StdMutex;

static LOG_PATH: OnceLock<StdMutex<String>> = OnceLock::new();

fn log_path_lock() -> &'static StdMutex<String> {
    LOG_PATH.get_or_init(|| StdMutex::new(String::new()))
}

#[allow(dead_code)]
pub fn get_log_path() -> String {
    if let Ok(v) = log_path_lock().lock() {
        let s = v.clone();
        if !s.is_empty() {
            return s;
        }
    }
    let p = dirs::data_dir().unwrap_or_else(|| PathBuf::from("."));
    let dir = p.join("vpbuddy-client");
    std::fs::create_dir_all(&dir).ok();
    dir.join("vpbuddy-client.log").to_string_lossy().to_string()
}

/// Set the log path for the client
pub fn set_log_path(p: String) {
    if let Ok(mut v) = log_path_lock().lock() {
        *v = p;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── 默认值函数测试 ──

    #[test]
    fn test_default_sample_rate() {
        assert_eq!(default_sample_rate(), 16000);
    }

    #[test]
    fn test_default_chunk_seconds() {
        assert_eq!(default_chunk_seconds(), 30);
    }

    #[test]
    fn test_default_true() {
        assert!(default_true());
    }

    #[test]
    fn test_default_max_events() {
        assert_eq!(default_max_events(), 50);
    }

    // ── AudioConfig 测试 ──

    #[test]
    fn test_audio_config_default() {
        let cfg = AudioConfig::default();
        assert_eq!(cfg.sample_rate, 16000);
        assert_eq!(cfg.chunk_seconds, 30);
        assert_eq!(cfg.overlap_seconds, 0);
    }

    #[test]
    fn test_audio_config_serde_roundtrip() {
        let cfg = AudioConfig {
            sample_rate: 44100,
            chunk_seconds: 60,
            overlap_seconds: 5,
        };
        let json = serde_json::to_string(&cfg).unwrap();
        let deserialized: AudioConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.sample_rate, 44100);
        assert_eq!(deserialized.chunk_seconds, 60);
        assert_eq!(deserialized.overlap_seconds, 5);
    }

    #[test]
    fn test_audio_config_default_serde() {
        // 验证 serde(default) 属性: 缺失字段走默认值
        let json = r#"{"sample_rate": 22050}"#;
        let cfg: AudioConfig = serde_json::from_str(json).unwrap();
        assert_eq!(cfg.sample_rate, 22050);
        assert_eq!(cfg.chunk_seconds, 30);       // default
        assert_eq!(cfg.overlap_seconds, 0);       // default
    }

    // ── SseConfig 测试 ──

    #[test]
    fn test_sse_config_default() {
        let cfg = SseConfig::default();
        assert!(cfg.reconnect);
        assert_eq!(cfg.max_events_per_chunk, 50);
    }

    #[test]
    fn test_sse_config_serde_roundtrip() {
        let cfg = SseConfig {
            reconnect: false,
            max_events_per_chunk: 10,
        };
        let json = serde_json::to_string(&cfg).unwrap();
        let deserialized: SseConfig = serde_json::from_str(&json).unwrap();
        assert!(!deserialized.reconnect);
        assert_eq!(deserialized.max_events_per_chunk, 10);
    }

    #[test]
    fn test_sse_config_default_serde() {
        let json = r#"{"reconnect": false}"#;
        let cfg: SseConfig = serde_json::from_str(json).unwrap();
        assert!(!cfg.reconnect);
        assert_eq!(cfg.max_events_per_chunk, 50); // default
    }

    // ── ClientConfig 测试 ──

    #[test]
    fn test_client_config_serde_roundtrip() {
        let cfg = ClientConfig {
            gpu_server_url: "http://test:8080".to_string(),
            audio: AudioConfig {
                sample_rate: 48000,
                chunk_seconds: 30,
                overlap_seconds: 2,
            },
            sse: SseConfig {
                reconnect: false,
                max_events_per_chunk: 100,
            },
        };
        let json = serde_json::to_string(&cfg).unwrap();
        let deserialized: ClientConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.gpu_server_url, "http://test:8080");
        assert_eq!(deserialized.audio.sample_rate, 48000);
        assert!(!deserialized.sse.reconnect);
    }

    #[test]
    fn test_client_config_defaults_on_missing_fields() {
        // 验证 serde(default) 对嵌套结构生效
        let json = r#"{"gpu_server_url": "http://example.com"}"#;
        let cfg: ClientConfig = serde_json::from_str(json).unwrap();
        assert_eq!(cfg.gpu_server_url, "http://example.com");
        assert_eq!(cfg.audio.sample_rate, 16000);  // default
        assert!(cfg.sse.reconnect);                 // default
    }

    // ── AppState 测试 ──

    #[test]
    fn test_app_state_new_initial_values() {
        let state = AppState::new();
        use std::sync::atomic::Ordering;
        assert!(!state.capturing.load(Ordering::SeqCst));
        assert_eq!(state.total_bytes.load(Ordering::SeqCst), 0);
        assert_eq!(state.total_uploads.load(Ordering::SeqCst), 0);
        assert_eq!(state.native_sample_rate.load(Ordering::SeqCst), 16000);
    }

    #[test]
    fn test_app_state_gpu_url_initialized() {
        let state = AppState::new();
        let url = state.gpu_url.try_lock().unwrap();
        assert!(!url.is_empty());
        // 默认 fallback URL (无 env var 也无 yaml 文件时)
        assert_eq!(*url, "http://47.100.182.3:28765");
    }

    #[test]
    fn test_app_state_meeting_id_initial_none() {
        let state = AppState::new();
        let mid = state.meeting_id.try_lock().unwrap();
        assert!(mid.is_none());
    }

    #[test]
    fn test_app_state_audio_source_initial_none() {
        let state = AppState::new();
        let src = state.audio_source.try_lock().unwrap();
        assert!(src.is_none());
    }

    #[test]
    fn test_app_state_log_path_initial_empty() {
        let state = AppState::new();
        let path = state.log_path.try_lock().unwrap();
        assert!(path.is_empty());
    }

    #[test]
    fn test_app_state_sse_handle_initial_none() {
        let state = AppState::new();
        let h = state.sse_handle.try_lock().unwrap();
        assert!(h.is_none());
    }

    #[test]
    fn test_app_state_capture_handle_initial_none() {
        let state = AppState::new();
        let h = state.capture_handle.try_lock().unwrap();
        assert!(h.is_none());
    }

    #[test]
    fn test_app_state_atomic_updates() {
        let state = AppState::new();
        use std::sync::atomic::Ordering;
        state.total_bytes.store(999, Ordering::SeqCst);
        state.total_uploads.store(42, Ordering::SeqCst);
        state.capturing.store(true, Ordering::SeqCst);
        assert_eq!(state.total_bytes.load(Ordering::SeqCst), 999);
        assert_eq!(state.total_uploads.load(Ordering::SeqCst), 42);
        assert!(state.capturing.load(Ordering::SeqCst));
    }

    // ── 路径相关测试 ──

    #[test]
    fn test_client_config_path_ends_with_correct_filename() {
        let path = client_config_path();
        assert!(path.ends_with(".vpbuddy-client.yaml"));
    }

    #[test]
    fn test_client_config_path_has_base_dir() {
        let path = client_config_path();
        let parent = path.parent();
        assert!(parent.is_some(), "path should have a parent directory");
    }

    #[test]
    fn test_get_log_path_ends_with_log_file() {
        let p = get_log_path();
        assert!(!p.is_empty(), "log path should not be empty");
        assert!(p.ends_with("vpbuddy-client.log"));
    }

    #[test]
    fn test_set_log_path_does_not_panic() {
        // set_log_path uses OnceLock; ensure no panic on multiple calls
        set_log_path("/tmp/test-vpbuddy.log".to_string());
        set_log_path("/tmp/another-test.log".to_string());
        // No assertion — just verifying no crash/panic
    }

    // ── save_gpu_url_to_yaml / load_client_config 测试 ──
    // 这两个函数依赖真实文件 I/O, 不适合纯单元测试。
    // 集成测试在 e2e 层面覆盖。

    // ── AudioConfig / SseConfig Debug 特征可用 ──

    #[test]
    fn test_audio_config_debug() {
        let cfg = AudioConfig::default();
        let debug_str = format!("{:?}", cfg);
        assert!(debug_str.contains("sample_rate"));
        assert!(debug_str.contains("chunk_seconds"));
    }

    #[test]
    fn test_sse_config_debug() {
        let cfg = SseConfig::default();
        let debug_str = format!("{:?}", cfg);
        assert!(debug_str.contains("reconnect"));
        assert!(debug_str.contains("max_events_per_chunk"));
    }

    #[test]
    fn test_client_config_debug() {
        let cfg = ClientConfig {
            gpu_server_url: "http://localhost".to_string(),
            audio: AudioConfig::default(),
            sse: SseConfig::default(),
        };
        let debug_str = format!("{:?}", cfg);
        assert!(debug_str.contains("gpu_server_url"));
    }
}

