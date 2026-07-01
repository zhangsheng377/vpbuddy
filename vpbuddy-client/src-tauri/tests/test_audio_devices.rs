// 2026-07-02 Phase 7 v0.8.0: 验证 list_input_devices 返回 AudioDeviceInfo (含 is_loopback 字段)
// 注: cpal 编译期 cfg, 在 Linux/Windows/macOS 上跑 host enumerate
// 期望: 至少跑通, 不 panic; 设备数 ≥ 0 (没设备 = 静默 return Ok([]))
use vpbuddy_client_lib::audio;
use vpbuddy_client_lib::audio::{is_loopback_device_name, detect_default_loopback};

#[test]
fn test_list_input_devices_runs() {
    let devs = audio::list_input_devices();
    assert!(devs.is_ok(), "list_input_devices 不应 panic: {:?}", devs.err());
    let list = devs.unwrap();
    println!("找到 {} 个设备:", list.len());
    for d in &list {
        println!("  - {} (default={}, loopback={})", d.name, d.is_default, d.is_loopback);
    }
    // 不强求设备数 — headless runner / container / 无 mic 都可能 0 设备
}

#[test]
fn test_is_loopback_device_name_linux_suffix() {
    // 平台分支已在 inline test 覆盖, 这里只验函数可调用
    #[cfg(target_os = "linux")]
    {
        assert!(is_loopback_device_name("alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"));
        assert!(!is_loopback_device_name("default"));
    }
}

#[test]
fn test_detect_default_loopback_returns_string_or_none() {
    let result = detect_default_loopback();
    println!("detect_default_loopback: {:?}", result);
    // 不强求 Some/None — 平台 + PulseAudio/PipeWire 启不启决定
    // Linux: 有 PulseAudio + monitor source → Some(name)
    // Linux: 无 PulseAudio → None
    // macOS: 有 BlackHole → Some("BlackHole 2ch"), 无 → None
    // Windows: 永远 None
}
