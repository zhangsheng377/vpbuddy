pub mod audio;
pub mod upload;
pub mod config;

#[cfg(test)]
mod tests {
    use super::*;

    // ── 验证模块导出 ──

    #[test]
    fn test_config_module_accessible() {
        // 验证 config 模块的类型和函数可通过 lib 访问
        let audio = config::AudioConfig::default();
        assert_eq!(audio.sample_rate, 16000);
        assert_eq!(audio.chunk_seconds, 30);
        assert_eq!(audio.overlap_seconds, 0);

        let sse = config::SseConfig::default();
        assert!(sse.reconnect);
        assert_eq!(sse.max_events_per_chunk, 50);

        let app_state = config::AppState::new();
        assert!(!app_state.capturing.load(std::sync::atomic::Ordering::SeqCst));
    }

    #[test]
    fn test_upload_module_accessible() {
        // 验证 upload 模块的类型可通过 lib 访问
        let seg = upload::TranscriptSegment {
            start_sec: 1.0,
            end_sec: 2.5,
            text: "hello".to_string(),
            speaker_id: "SPEAKER_00".to_string(),
            chunk_index: 0,
        };
        assert_eq!(seg.text, "hello");
        assert_eq!(seg.speaker_id, "SPEAKER_00");
    }

    #[test]
    fn test_audio_module_accessible() {
        // 验证 audio 模块的类型可通过 lib 访问
        let device = audio::AudioDeviceInfo {
            id: "test-device".to_string(),
            name: "Test Microphone".to_string(),
            is_default: true,
            is_loopback: false,
        };
        assert_eq!(device.id, "test-device");
        assert!(device.is_default);
        assert!(!device.is_loopback);
    }

    // ── 验证 config 默认值函数 ──

    #[test]
    fn test_default_functions_via_lib() {
        assert_eq!(config::default_sample_rate(), 16000);
        assert_eq!(config::default_chunk_seconds(), 30);
        assert!(config::default_true());
        assert_eq!(config::default_max_events(), 50);
    }

    // ── 验证 client_config_path 可用 ──

    #[test]
    fn test_config_path_via_lib() {
        let path = config::client_config_path();
        assert!(path.ends_with(".vpbuddy-client.yaml"));
    }

    // ── 验证 log_path 函数可用 ──

    #[test]
    fn test_log_path_via_lib() {
        let p = config::get_log_path();
        assert!(!p.is_empty());
        assert!(p.ends_with("vpbuddy-client.log"));
    }

    // ── 验证 set_log_path 不 panic ──

    #[test]
    fn test_set_log_path_via_lib() {
        config::set_log_path("/tmp/lib-test.log".to_string());
        // 无 assert — 仅验证不 panic
    }

    // ── 验证 upload 模块 AudioSourceKind 枚举 ──
    // AudioSourceKind 定义在 audio.rs 而非 upload.rs,
    // 此处验证 audio 模块类型可用即可
    #[test]
    fn test_upload_transcript_segment_serde() {
        let json = r#"{
            "start_sec": 0.0,
            "end_sec": 1.0,
            "text": "lib test",
            "speaker_id": "SPK_00"
        }"#;
        let seg: upload::TranscriptSegment = serde_json::from_str(json).unwrap();
        assert_eq!(seg.text, "lib test");
        assert_eq!(seg.chunk_index, 0);
    }
}
