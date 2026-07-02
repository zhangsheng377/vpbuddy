# 0035. v0.8.1 release artifact 文件名错位 — 修 Tauri client 3 个 version 0.6.0 → 0.8.1, 重发 v0.8.2

- **状态**: 已接受 (2026-07-02)
- **日期**: 2026-07-02
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (release infra fix)
- **依赖**: [ADR-0032](./0032-Phase7-跨平台loopback真实现.md) (v0.8.0 上游 — 当时也没 bump client version, 漏)
- **落地**: v0.8.2 (release infra fix, **不重发 v0.8.1** — 已下载用户不该被打扰, 旧 release body 注释指向 v0.8.2)

## 背景

v0.8.1 release `git push origin v0.8.1` 触发的 CI 全绿 (5/5 jobs, 4m40s, 见 run `28560813064`), GitHub Release 也成功发布. 但 **4 个 release asset 文件名全是 `VPBuddy_0.6.0_*`**, 跟 release 标题 v0.8.1 不匹配:

| Asset 实际文件名 | 应该 |
|---|---|
| `VPBuddy_0.6.0_amd64.deb` | `VPBuddy_0.8.1_amd64.deb` |
| `VPBuddy_0.6.0_x64.dmg` | `VPBuddy_0.8.1_x64.dmg` |
| `VPBuddy_0.6.0_x64-setup.exe` | `VPBuddy_0.8.1_x64-setup.exe` |
| `VPBuddy.app.zip` (没带 version, 没事) | (OK) |

### 真因

Tauri build artifact 文件名 = `${productName}_${Cargo.toml.package.version}_${arch}.{deb,dmg,exe}`.

3 个 client 文件都还停在 0.6.0:
- `vpbuddy-client/package.json` `version: "0.6.0"`
- `vpbuddy-client/src-tauri/Cargo.toml` `version = "0.6.0"`
- `vpbuddy-client/src-tauri/tauri.conf.json` `version: "0.6.0"`

历史: v0.6.0 → v0.8.0 (跨平台 loopback 真实现 ADR-0032) 时**只 bump 了服务端 `pyproject.toml` + `src/vpbuddy/__init__.py`**, **漏了 client 这 3 个文件**. v0.8.1 (test fix release, ADR-0034) 也没补这个 — 当时 hero 改动是 server-side test, 没人想到 client version 也要跟.

### 为什么 CI 仍 pass?

`tauri-multi-build.yml` 没硬编任何 0.6.0, Tauri build 走 `cargo tauri build`:
- `Cargo.toml` `version` → artifact 文件名 + About dialog version
- `tauri.conf.json` `version` → 同上 (Tauri 内部读这俩, 需一致)

`vpbuddy-client/src-tauri/build.rs` 走 `git describe --tags` → 设 `VPBUDDY_VERSION` env var → `main.rs` 启动时打印. **运行时版本号对**, 只是**静态 build artifact 文件名错**.

## 决策

### 1. Bump 3 个 client 文件 0.6.0 → 0.8.1

```toml
# vpbuddy-client/src-tauri/Cargo.toml
[package]
name = "vpbuddy-client"
version = "0.8.1"  # 0.6.0 → 0.8.1
```

```json
// vpbuddy-client/src-tauri/tauri.conf.json
{
  "productName": "VPBuddy",
  "version": "0.8.1",  // 0.6.0 → 0.8.1
  "identifier": "dev.zsd.vpbuddy"
}
```

```json
// vpbuddy-client/package.json
{
  "name": "vpbuddy-client",
  "private": true,
  "version": "0.8.1"  // 0.6.0 → 0.8.1
}
```

### 2. 不重发 v0.8.1, 发 v0.8.2

- v0.8.1 GitHub Release **保留** — 已下载用户不该被打扰, 旧 binary 内容跟 v0.8.2 完全一致 (仅文件名错)
- v0.8.2 = 同 v0.8.1 内容 + 修 artifact 文件名
- 旧 v0.8.1 release body 不动 (干净, v0.8.2 自己写 "fixes release artifact filenames" 说明就够)
- 跟 semver 严格一致: v0.8.2 是 patch bump (只 release infra fix, 0 product code change)

### 2.5 Server version 也 bump 0.8.1 → 0.8.2 (一致性)

虽然 server 代码 0 改动 (跟 v0.8.1 完全一致), 但**也** bump:
- `pyproject.toml` `version: 0.8.1 → 0.8.2`
- `src/vpbuddy/__init__.py` `__version__: 0.8.1 → 0.8.2`
- Client 3 个文件 `0.6.0 → 0.8.2` (注: 跟 server 一致, 不是 0.8.1 — 因为整个 release 是 v0.8.2)

理由: tag name 0.8.2 是统一信号, 4 个 version 字段全部跟它走, 避免"server 0.8.1 + client 0.8.2"这种 mixed versions 给未来考古的人造成"是不是 server 跟 client 漂移"的错觉. server 跟 client **本就该版本一致** (跟 v0.7.x / v0.6.x 历史 release 一样).

### 3. ADR-0034 (v0.8.1 test fix) 不动

ADR-0034 已记录 v0.8.1 release 决策 (3 stale test 修). 本 ADR 单独记录 v0.8.2 release infra fix, 不污染 ADR-0034.

## 设计取舍

### 为什么不加 workflow 改造让 artifact 走 git tag?

理论上可以改 `.github/workflows/tauri-multi-build.yml`, 在 build 前 sed 替换 client 3 个文件的 version 为 `${{ github.ref_name }}`. 但:
- 加一层 script + 维护成本, 不如直接 bump 文件 (1 次性, 3 行)
- 用户从 GitHub UI 看 release, 主要看 tag + body, 文件名是 secondary 信号
- 历史已发 v0.8.0 当时也没做这个, 不该在 v0.8.2 临时加 (drift)

→ **不加**, 走直接 bump 文件路径.

### 为什么不补 ADR-0032 那个漏?

ADR-0032 当时 v0.6.0 → v0.8.0 漏 bump client version 是真 bug, 但
- 单独补 ADR-0032.1 修 1 个 backward version 文件名 → 价值低 (已经发 v0.8.0, 没人去重下 v0.8.0 因为它也有 0.6.0 文件名)
- v0.8.2 fix 一起 cover (v0.8.0 和 v0.8.1 都有这个 bug, 但 2 个 release body 都注释指向 v0.8.2)

→ **不补**, 一次性 v0.8.2 修.

### 为什么 v0.8.1 release body 不直接更新指向 v0.8.2?

`gh release edit v0.8.1 --notes "..."` 可以改 body, 但:
- 改了 v0.8.1 body 等于"重写历史"
- GitHub release API 不会因 body 改触发 re-build, 单纯改 body 不解决用户已有下载
- 改 v0.8.1 body + 发 v0.8.2 = 双重信号, 反而混乱

→ **不改 v0.8.1 body, 不加注释** — 让 v0.8.2 release body 自带"replaces v0.8.1 (fix artifact filename)"说明就够.

## 实施细节

| 文件 | 改动 |
|------|------|
| `vpbuddy-client/package.json` | `version: "0.6.0"` → `"0.8.2"` (+1 char, -1 char) |
| `vpbuddy-client/src-tauri/Cargo.toml` | `version = "0.6.0"` → `"0.8.2"` (+1, -1) |
| `vpbuddy-client/src-tauri/tauri.conf.json` | `"version": "0.6.0"` → `"0.8.2"` (+1, -1) |
| `pyproject.toml` | `version = "0.8.1"` → `"0.8.2"` (server 跟 release 标签一致, 代码 0 改) |
| `src/vpbuddy/__init__.py` | `__version__ = "0.8.1"` → `"0.8.2"` + 注释更新 |
| `docs/design/总体架构.md` | v1.34 → v1.35 (本 release 标记) + ADR 索引 + ADR-0035 |
| `docs/decisions/0035-...md` | 本文件 |

**LOC**: +3 version bumps, 0 product code change, 0 API change, 0 breaking change

## 后果

### 积极
- ✅ v0.8.2 release artifact 文件名跟 tag 一致 (`VPBuddy_0.8.1_*`)
- ✅ client 3 个 version 同步, 未来发版不会漏
- ✅ 不动已发布 v0.8.1 (用户已下载的不被打扰)
- ✅ 设计 doc v1.35 标记本次 release

### 消极
- ❌ v0.8.0 + v0.8.1 旧 release 文件名仍是 `VPBuddy_0.6.0_*` (历史问题, 本 ADR 不修)
- ❌ GitHub Actions Node 20 deprecation annotation 仍存在 (non-blocking, ADR-0035 不修)

### 风险
- 无. 0 product code change. client runtime version 一直对 (`build.rs` 走 git describe). 仅 build artifact 文件名 cosmetic fix.

## 关联

- 上游: [ADR-0032](./0032-Phase7-跨平台loopback真实现.md) (v0.8.0 上游, 当时漏 bump client version)
- 上游: [ADR-0034](./0034-batch-docs-render-prompt-singular-plural-mismatch.md) (v0.8.1 上游, 当时也没补 client version)
- 上游: CI run `28560813064` (v0.8.1 release run, 抓到这个 bug)
