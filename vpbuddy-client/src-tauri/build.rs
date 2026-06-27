use std::process::Command;

fn main() {
    tauri_build::build();

    // 2026-06-28: 注入 git 版本号到环境变量, main() 启动时打印
    // 用户的反馈: "客户端和服务端 log 一开始就打印版本信息,
    // 这样就能确认有没有更新了"
    let version = Command::new("git")
        .args(["describe", "--tags", "--always", "--dirty=-modified"])
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                String::from_utf8(o.stdout).ok()
            } else {
                None
            }
        })
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".to_string());

    println!("cargo:rustc-env=VPBUDDY_VERSION={}", version);
}