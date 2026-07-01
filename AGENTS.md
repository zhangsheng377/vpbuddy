# VPBuddy — AI 协作铁律 (必读)

> 适用对象: 所有 AI 协作 agent (Hermes / Claude / Codex / Copilot 等) 和人类贡献者。
> 维护: 张胜东 (起草: Hermes 2026-07-01)
> 最后更新: 2026-07-01

---

## 〇. 不可违反的 5 条铁律

### 铁律 1: 事实陈述必须有真命令验证

不准用 `lsof` 推断"没装"、不准用 `ps` 推断"在跑"、不准凭印象说"X 模块使用 Y 库"。
**任何"X 没装 / Y 用 Z / 函数 W 调用了 V"的陈述,必须用真命令验证**:

- Python 库: `python -c "import X; print(X.__file__)"` 或 `pip show X`
- CLI 工具: `which X` + `X --version`
- 代码路径: `grep -rn "def W" src/`
- 运行状态: 真实启动一次, 看 log

**来源**: 2026-06-22 张胜东纠错 (Python 库是 conda env 静默装着, 不是 daemon 进程)。

### 铁律 2: ADR 驱动 + 1 提交 (代码 + 文档 + 部署同步)

每次架构变更**必须**:

1. 写 1 个新 ADR (格式: `docs/decisions/00XX-主题.md`), 顶部标状态 / 日期 / 作者 / 替代 / 依赖
2. 同步更新 `docs/design/总体架构.md` 状态号 (v1.20 → v1.21) + 顶部 ADR 索引
3. 同步更新 `docs/product-spec/VPBuddy_产品说明书.md` 相关章节
4. 同步更新 `pyproject.toml` (版本号 / 依赖) + `src/vpbuddy/_version.py` (如版本变化)
5. 同步更新 `README.md` (如用户可见功能变化)
6. **1 个 commit** 包含以上所有变更, 标题用 `feat/fix/refactor(scope): ...` 格式

**禁止**:
- 改代码不写 ADR
- 写 ADR 不改 design/spec/README (drift)
- 跨多 commit 拆同一逻辑变更 (散)
- 把"实现"和"设计"分成两个 PR/commit

### 铁律 3: 代码先于文档

`docs/decisions/00XX-*.md` 是历史快照, 反映"当时为什么这么决定"。
**真实架构在代码里**。所以:

- 看 ADR 之前先 `find src -name "*.py" | xargs head -30` 看 docstring
- ADR 顶部如果标 "Superseded by 00YY", 跳到 00YY, 别读旧的
- 发现 ADR 跟代码不一致, **立刻承认+立刻修文档** (不准用旧 ADR 做架构假设)

### 铁律 4: 抓虚晃 — 说"let me check"必须同回合真起工具

任何"我看看 X" / "我检查下 Y" / "let me check" 的承诺, **同一回合必须起工具** (read_file / search_files / terminal)。禁止:

- "让我先看看..." 然后开始写结论
- 答应"我查一下" 然后 commit 不带证据
- 编造一个看起来合理的 API 路径

### 铁律 5: 真实部署驱动, 不接受"为 dev 方便"的设计

- 部署配置 (`requirements.txt` / `requirements-gpu.txt` / `pyproject.toml` `[gpu]` extra) 是真路径
- **禁止** 写"开发用 sqlite 凑合, 生产再换 postgres" 这种设计 — 部署什么就写什么
- **禁止** 写"先 mock 一下, 之后接真" 超过 2 周还没接的临时代码
- 用户的 NFS / 飞牛 fnOS / GPU 服务器 是真环境, 设计必须直接 work

---

## 一. 项目目录速查

| 路径 | 用途 |
|------|------|
| `src/vpbuddy/` | 服务端 Python 包 (UI server / engine / storage / KB / RAG / 6 doc agent / skill 入口) |
| `src/tests/` | pytest 测试 (用 conftest 控制环境) |
| `vpbuddy-client/` | Tauri 桌面客户端 (Rust 后端 + Vite/JS 前端) |
| `vpbuddy-client/src-tauri/src/audio.rs` | 跨平台音频采集 (cpal) |
| `vpbuddy-client/ui/` | 客户端前端 (index.html / main.js / style.css) |
| `ui/` | 服务端 Web UI (旧版, 客户端化后基本只参考) |
| `docs/decisions/00XX-*.md` | 架构决策记录 (ADR), 编号严格递增 |
| `docs/design/总体架构.md` | 总体架构 (状态号 v1.20+) |
| `docs/product-spec/VPBuddy_产品说明书.md` | 产品说明书 (用户视角, 当前 v1.20) |
| `pyproject.toml` | 包声明 + 依赖 (含 [gpu] / [dev] extras) |
| `README.md` | 用户上手 (中英双语) |
| `data/` | 运行时数据 (会议 / KB / 上传 — **gitignored**) |
| `samples/` | 测试音频样本 (gitignored 大文件) |

---

## 二. 客户端 ↔ 服务端职责边界

| 关注点 | 客户端 (Tauri) | 服务端 (Python) |
|--------|----------------|-----------------|
| 音频采集 | ✓ (cpal 跨平台麦克风) | ✗ (从客户端收 wav) |
| 实时转写展示 | ✓ (SSE 推流 + 波形 + cleaned) | ✓ (funasr ASR) |
| 6 doc 生成 | ✗ | ✓ (6 sub-session) |
| demo 生成 | ✗ | ✓ (demo agent) |
| KB 检索 | ✓ (UI 输入 query) | ✓ (RAG 后端) |
| 会议状态 | ✓ (list / 选) | ✓ (持久化) |
| 录音开关 | ✓ (start_capture / stop_capture) | ✗ |
| 会议创建 | ✓ (UI 选旧 / 输入新) | ✓ (建档 + 6 doc 监听) |

**数据传输**: 客户端 → 服务端 = WAV (16kHz mono PCM) via HTTP multipart; 服务端 → 客户端 = SSE 实时事件流 (`/api/meetings/{id}/events`)。

---

## 三. 跨平台部署注意

- **Linux (开发/服务端主)**: PipeWire / PulseAudio 内录可用 (`src/vpbuddy/loopback.py`)
- **macOS (Tauri 客户端)**: BlackHole / Soundflower 需用户安装, Tauri 端 `audio.rs` 待加 loopback 选项
- **Windows (Tauri 客户端)**: WASAPI loopback 待实现 (Tauri 2.6+ 用 `cpal` host WASAPI)

`is_loopback_supported()` 平台分支在 `vpbuddy-client/src-tauri/src/audio.rs` 待加, 详见 ADR-0021。

---

## 四. 已知陷阱 (踩过)

| 坑 | 教训 | 出处 |
|---|------|------|
| sqlite-vec `INSERT OR REPLACE` + `lastrowid` 产生 orphan vec row | 先查老 id → 删老 vec → 删老 doc → INSERT 新 doc → INSERT 新 vec | commit `44a701a` |
| sqlite3 单连接多线程不安全 → 6 docs trigger database is locked | 加 `RLock` 串行化 + WAL + 30s busy_timeout | commit `3fee650` |
| funasr 是 batch 不是 streaming, 用户说话后最长等 30s 才出字 | 客户端加 latency ticker + banner 解释 | commit 2026-06-28 |
| Tauri 2.6.3 去掉 `window.__TAURI__`, 必须用 ESM `import` from `@tauri-apps/api` | Vite 构建 OK, 直接 `index.html` 加载会失败 | 2026-06-26 |
| 飞书子 session prompt 泄露 VPBuddy 身份 | 子 agent prompt 改为"你是本次会议的助手"+ 数据隔离 | commit `c412abe` |
| 客户端 `gpu.zhangshengdong.com` IPv6-only 域名在 V 家网 (IPv4 单栈) 解析不到 | LAN 直连 `http://192.168.10.63:8765` | 2026-07-01 |
| Chroma 第一次 query 加载 embedding 模型 ~1s | 启动时预热 `get_rag().count()` | ADR-0019 |

---

## 五. 工具选择速查

| 任务 | 工具 | 备注 |
|------|------|------|
| ASR | funasr paraformer-zh | 服务端 GPU, 30s batch 切片 |
| 说话人分离 | pyannote-audio | 离线下载, ModelScope 镜像 |
| LLM (chat / doc) | ollama 本地 | 默认 `qwen2.5:7b` |
| RAG (新, v0.6) | **Chroma 嵌入式 + sentence-transformers** | ADR-0019 选型 |
| 数据存储 | SQLite (stdlib) | 不引外部 DB 进程 |
| 客户端打包 | Tauri 2.6+ | 三平台自动 CI (Linux / macOS / Windows) |

---

## 六. 文档版本号约定

- `pyproject.toml` `[project] version`: 每发版递增 (语义化版本)
- `src/vpbuddy/_version.py` `__version__`: CI 注入 (git describe)
- 总体架构: `docs/design/总体架构.md` 顶部 v1.XX 状态号
- 产品说明书: `docs/product-spec/VPBuddy_产品说明书_vX.Y.md` (每个 v 一份)
- ADR: `docs/decisions/00XX-主题.md` 严格编号, 不可跳号不可复用

---

## 七. 不要做

- ❌ 用 lsof 推断 Python 包没装
- ❌ 写"为 dev 方便"的 mock 而忘了真路径
- ❌ 把"实现"和"设计"拆两个 commit
- ❌ 改代码不更新 ADR / design / spec
- ❌ 让子 agent prompt 暴露 VPBuddy 身份 / 系统内部信息
- ❌ 把旧 ADR 当现行架构看 (代码先于文档铁律)
- ❌ 切会议 / 关客户端不算会议结束 — 6 doc 完成不自动结束会议 (ADR-0022)
- ❌ 让 6 doc 写完时自动入 KB (废弃, 改手动上传, ADR-0020)

---

## 八. CI / Release 流程

1. 本地 `git status` 干净 → 改代码 + ADR + 文档 + pyproject → 1 commit
2. `git push origin main` → CI 自动跑 (lint + pytest + Tauri build 三平台)
3. tag 打版本: `git tag v0.6.0 && git push --tags` → 自动 release
4. Tauri 客户端下载 release artifacts 安装

详见根 `README.md` 末尾。

---

**TL;DR**: 先 `ls docs/decisions/`, 再 `find src -name "*.py" | xargs head -30`, 再真命令验证, 再写代码/改文档, 再 1 commit, 再 push。
