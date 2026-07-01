# 0026. macOS CI 去掉 `--no-bundle` — 产出 .app 和 .dmg

- **状态**: 已接受
- **日期**: 2026-07-01
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无
- **依赖**: 无 (单文件 CI 改动)

## 背景

2026-07-01 张胜东反馈: "macos 的问题修一下" — 之前 v0.6.0 release 没有 macOS asset。

**现状(查 `.github/workflows/tauri-multi-build.yml:129`)**:

```yaml
- name: Tauri build (macOS)
  working-directory: vpbuddy-client/src-tauri
  run: npx tauri build --target x86_64-apple-darwin --no-bundle
```

`--no-bundle` 跳过了 bundle 阶段,只编译二进制不打包。`tauri.conf.json` 的 `bundle.targets="all"` 在 Linux/Windows CI 都跑得通(macOS 的 .app / .dmg 在 `all` 列表里),**只有 macOS CI 显式跳过了**。

**历史 release 同样无 macOS asset**, 但没人去修 — 用户最终用 vpbuddy 主要是 Linux 服务器 + Windows 客户端, macOS 是次要目标。

**目标**: macOS CI 跟 Linux/Windows 一样走完整 bundle,产出 `.app` 和 `.dmg`,release assets 三平台齐。

## 决策

### 1. 去掉 `--no-bundle`,不传 `--bundles`

```yaml
- run: npx tauri build --target x86_64-apple-darwin
```

走 `tauri.conf.json` 的 `bundle.targets="all"`,产出会包含:

- `target/x86_64-apple-darwin/release/bundle/macos/VPBuddy.app` — 双击运行的 .app bundle
- `target/x86_64-apple-darwin/release/bundle/dmg/VPBuddy_0.6.0_x64.dmg` — 可分发的磁盘镜像

### 2. artifact 拆两个名字上传

```yaml
- name: Upload .app artifact
  uses: actions/upload-artifact@v4
  with:
    name: vpbuddy-client-macos-app
    path: .../bundle/macos/*.app
    if-no-files-found: error   # 之前是 warn, 改 error 让缺失即 fail

- name: Upload .dmg artifact
  uses: actions/upload-artifact@v4
  with:
    name: vpbuddy-client-macos-dmg
    path: .../bundle/dmg/*.dmg
    if-no-files-found: error
```

`if-no-files-found: warn` 之前会"装没装"都成功,改 `error` 让缺失即 fail,避免再次出现"看起来 CI 过了但其实没产物"的 silent bug。

### 3. release workflow 不用改

`.github/workflows/tauri-multi-build.yml:196-199`:

```yaml
files: |
  artifacts/**/*.deb
  artifacts/**/*.app
  artifacts/**/*.exe
```

`*.app` glob 已经在,`.dmg` 没在 — 但 `*.app` 是用户主要使用形式,.dmg 仅作分发镜像**可选**。先不上 .dmg,等用户要求再加。

## 取舍

| 选项 | 优 | 劣 |
|------|----|----|
| 保留 `--no-bundle` 上传裸 binary | 快 (~5 min) | 用户没 GUI 包,没有 .app 启动 |
| 去 `--no-bundle` 上传 .app + .dmg (本决策) | 用户双击即用, 跟 Linux/Windows 一致 | macOS CI 时间多 ~3-5 min |
| 改 `bundle.targets: "app"` 只产 .app | 最快 | 没 .dmg 镜像, 文件分发不便 |

选第二个,等价的 `bundle.targets="all"` 已经在用,只是 CI 显式 skip。

## 影响

- **CI 改动**: `.github/workflows/tauri-multi-build.yml` 单文件, build-macos job 改 build 命令 + 拆 artifact 上传
- **首次跑**: 预计 ~10-12 min (比之前多 3-5 min, 主要是 DMG 打包)
- **不需 code signing**: 本项目未配置 Apple Developer ID, .app/.dmg 会是 "unidentified developer" — 用户首次双击需右键 → 打开绕过 Gatekeeper。 后续要分发签名版另开 ADR
- **不需 notarization**: 同上, 本地/局域网用没问题
- **不需改 tauri.conf.json**: `bundle.targets="all"` 已包含 macOS 目标

## 验证

push 后 watch macos job:

- 出现 `vpbuddy-client-macos-app` 和 `vpbuddy-client-macos-dmg` 两个 artifact
- `if-no-files-found: error` 触发即知道路径不对
- 重新打 `v0.6.1` tag, release 自动包含 3 平台 assets (Linux .deb + Windows .exe + macOS .app/.dmg)

## 没做

- ❌ Apple code signing (需要 Developer ID, $99/yr)
- ❌ Notarization (需要同上)
- ❌ Universal binary (arm64 + x86_64) — 当前只 x86_64, Apple Silicon 用户需 Rosetta
- ❌ DMG 上传 release page (只本地 build 出, .app 已够用)
