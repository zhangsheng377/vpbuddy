# VPBuddy

> **本地优先的会议操作系统级 AI 助手** —— 为 VP / 售前 / 项目负责人设计,运行在 VP 自己桌面客户端,数据完全本地化。

**v0.15.3** (2026-07-06) — ASR VAD 修复 + Vision API 调通(data:image base64) + 图片双路(文件路径+KB)。详见 [Releases](https://github.com/zhangsheng377/vpbuddy/releases)。

[English](#english) | [中文](#中文)

---

## 中文

### 什么是 VPBuddy?

VPBuddy 是一款**本地优先的会议 AI 系统**,桌面客户端直接捕获系统音频(腾讯会议/钉钉/企微 不需要 SDK),在会议过程中自动:

- 🎙️ **实时转写** — funasr paraformer-zh (本地 ASR,中文识别率 > 90%) + **支持麦克风 + 系统内录双轨** (v0.6, 详见 ADR-0021)
- 📋 **结构化累积** — REQ / GOAL / FEAT / RISK / QUE 5 类事实自动分类
- 📄 **6 种文档自动生成** — 需求 / 架构 / 任务 / API / 风险 / 演示 (**演示按版本号增量存档**, v0.6 详见 ADR-0024)
- 🧠 **用户主动维护的知识库** — v0.6 改用 Chroma 嵌入式 (in-process) + 用户上传文件入库,不再自动 ingest 6 docs,默认按会议隔离检索 (详见 ADR-0019/0020)
- 🤖 **agent 工具** — 6 doc / demo / chat agent 都能调网络搜索 (DuckDuckGo) + KB 检索当前会议 (v0.6 详见 ADR-0025)
- 💬 **chat 上传文件 + agent 主动提问** — v0.6 (详见 ADR-0023)
- 🖥️ **桌面客户端** — Tauri 2.6+ 跨平台 (Linux / macOS / Windows), **首页必须先选/输入会议才能开始录音** (v0.6 详见 ADR-0022)

**关键特性**: 完全运行在 VP 自己机器,数据不上传云端。单租户,单实例,单镜像。

### 5 分钟上手

```bash
# 1. 安装(开发机/桌面客户端)
git clone https://github.com/zhangsheng377/vpbuddy.git
cd vpbuddy
pip install -e .  # v0.6 自动装 chromadb + pypdf + duckduckgo-search

# 2. 启动 UI
vpbuddy ui
# → 浏览器打开 http://localhost:8765

# 3. 启动后台 controller(7×24 监听)
vpbuddy controller --start
```

**第一次启动会自动**:
- 预下载 embedding 模型 (`paraphrase-multilingual-MiniLM-L12-v2` 384 维, Chroma 首次运行时下载)
- 创建本地 RAG 库 (`data/chroma/` 单文件夹持久化)
- 初始化 6 种子 session prompts

### 下载预编译客户端安装包

VPBuddy 桌面客户端通过 GitHub Actions 自动编译并发布到 Releases 页面：

1. 打开 [Releases 页面](https://github.com/zhangsheng377/vpbuddy/releases)
2. 选择最新版本（如 `v0.8.6`）
3. 下载对应平台安装包：

| 平台 | 下载 | 安装方法 |
|------|------|----------|
| **Linux** | `VPBuddy_*_amd64.deb` | `sudo dpkg -i VPBuddy_*.deb` <br> 或双击安装 |
| **macOS** | `VPBuddy.app.zip` + `VPBuddy_*_x64.dmg` | 下载 `.dmg` 拖到 `应用程序` 文件夹 (Apple Silicon 需 Rosetta) |
| **Windows** | `VPBuddy_*_x64-setup.exe` | 直接运行 exe 安装 |

**启动客户端**（编译或解压后）:
```bash
# 命令行: 默认连接内网 GPU server
./vpbuddy-client

# 指定 GPU server 地址
VPBUDDY_GPU_URL=http://192.168.10.63:8765 ./vpbuddy-client

# GUI: 直接双击图标即可，Tauri 原生窗口
```

**注意**：客户端**不调 LLM**，所有语音识别、文档生成和 LLM 流量全走 GPU server 端。客户端只需连得通 server 就能跑。

### 架构

```
┌───────────────────────────────────────────────────────────┐
│  VP 桌面客户端 (Ubuntu 24.04 / macOS 14+ / Win11)        │
├───────────────────────────────────────────────────────────┤
│  Tauri 2.6+ (cpal 音频采集)                               │
│     ↓ WAV chunks (HTTP POST) / SSE 事件流                 │
│     ┌───────────────────┐   ┌────────────────────────┐   │
│     │ GPU 服务器          │   │  MiniMax-M3 LLM        │   │
│     │ 47.100.182.3:28765│   │  (OpenAI 兼容 API)      │   │
│     │                    │   │                         │   │
│     │  master session    │   │  batch_docs agent      │   │
│     │  meeting:{mid}:    │───│  (fork 自 chat)        │   │
│     │  vp-chat ←→ Chat   │   │  5 文档一次 LLM 调用    │   │
│     │       ↓ fork       │   └────────────────────────┘   │
│     │  ┌────────────────┐│   ┌────────────────────────┐   │
│     │  │ demo agent     ││   │  funasr ASR (GPU)      │   │
│     │  │ (fork 自 chat)  ││   │  pyannote 说话人分离    │   │
│     │  │ HTML 原型生成   ││   │  Chroma RAG (嵌入式)   │   │
│     │  └────────────────┘│   └────────────────────────┘   │
│     └───────────────────┘                                 │
└───────────────────────────────────────────────────────────┘
```
**架构变化历史**:
- **v1.40 及之前** (ADR-0029): 3 个完全独立的 session (chat / batch_docs / demo)
- **v1.41** (ADR-0041, 2026-07-04): doc agent fork 自主 chat session, 继承上下文

### 文档

| 主题 | 链接 |
|------|------|
| **📡 API 参考 (外部客户端) 🔥** | [docs/api-reference.md](docs/api-reference.md) |
| **架构** | [docs/design/总体架构.md](docs/design/总体架构.md) |
| **产品需求** | [docs/product-spec/](docs/product-spec/) |
| **决策记录** | [docs/decisions/](docs/decisions/) |
| **安装指南** | [docs/部署/INSTALL.md](docs/部署/INSTALL.md) |
| **Phase B Tauri 客户端** | [INSTALL.md §Phase B](./docs/部署/INSTALL.md#phase-b-tauri-桌面客户端-2026-06-24-adr-0016-落地) / [ADR-0016](./docs/decisions/0016-桌面客户端技术选型.md) |
| **流式 E2E** | [ADR-0013](./docs/decisions/0013-流式E2E-端到端工作流.md) |
| **VPBuddy Skill** | [ADR-0014](./docs/decisions/0014-VPBuddy装成Hermes-Skill.md) |
| **sqlite-vec RAG** | [ADR-0015](./docs/decisions/0015-RAG-sqlite-vec本地知识库.md) |
| **模型 swap** | [docs/部署/MODEL_SWAP.md](docs/部署/MODEL_SWAP.md) |
| **踩坑记录** | [docs/部署/踩坑记录.md](docs/部署/踩坑记录.md) |
| **用户手册** | [docs/用户手册.md](docs/用户手册.md) |
| **🔒 安全 (ADR-0010)** | [INSTALL.md §🔒](./docs/部署/INSTALL.md#-安全信息隔离-adr-0010) / [ADR-0010](./docs/decisions/0010-信息隔离-deployment-clean-install.md) |
| **🇨🇳 HF 离线 (ADR-0011)** | [INSTALL.md §🇨🇳](./docs/部署/INSTALL.md#hf-模型离线铁律-adr-0011) / [ADR-0011](./docs/decisions/0011-HF模型离线铁律.md) |

### 🔒 安全:信息隔离 (ADR-0010)

> 张胜东在 2026-06-22 发现 GPU 服务器的 `~/.hermes/.env` 含本机真实 API key,立即清理 + 修补 install 脚本。

**铁律先复习**(ADR-0009 + ADR-0001):
- **VPBuddy 必须运行在 Hermes Agent 之上**(不自研 LLM 框架)
- 一次会议 = 一个 Hermes session,6 doc_kind = 6 子 session
- 真并发 = `ThreadPoolExecutor(3)` + in-process `from run_agent import AIAgent`
- LLM API key 由 `~/.hermes/.env` 通过 env var 注入,VPBuddy 不自己调 LLM HTTP

**信息隔离三条铁律**(ADR-0010):
1. `config.yaml` / `.env` 都用占位符,真实 key 由用户手动 `vim` 填
2. install 脚本从不包含真实 API key(可以安全推到 GitHub)
3. install 脚本不覆盖用户已存在的 `~/.hermes/config.yaml` 或 `.env`

```bash
# 1. install 只创建占位符(GPU 服务器 install-gpu-server.sh 一步装 hermes-agent + vpbuddy)
bash scripts/install-gpu-server.sh
# 输出:MINIMAX_CN_API_KEY=*** 2. 用户手动填
vim ~/.hermes/.env
chmod 600 ~/.hermes/.env

# 3. 验证:hermes-agent + vpbuddy 真共享 session
conda activate vpbuddy-gpu
python3 -c "from run_agent import AIAgent; print('✅ VPBuddy ↔ Hermes 真连接')"
```

### 命令速查

| 命令 | 说明 |
|------|------|
| `vpbuddy ui` | 启动 Web UI(开会时主入口) |
| `vpbuddy controller --start` | 启动后台 7×24 controller |
| `vpbuddy transcribe audio.wav` | 单次音频转写 |
| `vpbuddy trigger <meeting_id> <doc_kind>` | 手动触发某种文档 |
| `vpbuddy kb-search <query>` | 命令行 KB 检索 |
| `vpbuddy kb-status` | 查看 KB 状态(失败重试等) |
| `vpbuddy setup-gpu` | 装 GPU 模型(开发用) |

### 技术栈

- **Python 3.11+**,FastAPI / Pydantic v2
- **前端**: Vanilla JS (零依赖,符合 YAGNI)
- **ASR**: funasr paraformer-zh + fsmn-vad + cam++ (本地)
- **Embedding**: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384 维)
- **向量存储**: sqlite-vec (单文件,零依赖)
- **LLM**: OpenAI 兼容 API(默认 MiniMax-M3,可换 GPT-4o / Claude / Qwen 等)
- **音频**: PipeWire / PulseAudio / WASAPI / BlackHole (跨平台)

### 状态(2026-07-05)

- ✅ 全链路 work: 上传音频 → ASR → batch_docs 5 文档 + demo → SSE 实时推流
- ✅ fork 架构: doc agent 继承 chat 上下文 (parent_session_id)
- ✅ **v0.8.6 CI 全线通过** — cargo test + check / Linux / macOS / Windows 四平台全部 success
- ✅ **三平台桌面客户端自动发布** — 直接从 [Releases](https://github.com/zhangsheng377/vpbuddy/releases) 下载安装包 (.deb / .dmg / .exe)
- ✅ API 参考文档对外公开, 外部开发者可自行实现网页客户端
- ✅ 文档齐全: 41 个 ADR + INSTALL.md + CI 工作流 + API 参考

### License

MIT

---

## English

### What is VPBuddy?

VPBuddy is a **local-first meeting AI system** that captures system audio directly (no SDK needed for Tencent Meeting / DingTalk / WeCom). During meetings, it automatically:

- 🎙️ **Real-time transcription** — funasr paraformer-zh (>90% Chinese accuracy)
- 📋 **Structured accumulation** — 5 fact categories: REQ / GOAL / FEAT / RISK / QUE
- 📄 **6 document types auto-generated** — requirements / architecture / tasks / API / risks / demo
- 🧠 **Cross-meeting KB** — sqlite-vec + sentence-transformers with cosine similarity
- 🌐 **Web UI** — port 8765, real-time accumulation + history search

**Key feature**: Runs entirely on VP's own machine. Data never leaves the device. Single-tenant, single-instance, single-binary.

### 5-minute Quick Start

```bash
git clone https://github.com/zhangsheng377/vpbuddy.git
cd vpbuddy
pip install -e .
vpbuddy ui    # → http://localhost:8765
```

### Documentation

See `docs/` for:
- Architecture (`docs/design/总体架构.md`)
- Decisions (`docs/decisions/0004-*.md` through `0016-*.md`)
- Installation (`docs/部署/INSTALL.md`)
- Model swap guide (`docs/部署/MODEL_SWAP.md`)
- Phase B Tauri desktop client (ADR-0016, since 2026-06-24)
- Streaming E2E architecture (ADR-0013, since 2026-06-23)

### Status (2026-07-01)
- ✅ v0.6 设计稿完成,等待实现: 8 项需求 + 6 个新 ADR (0019 ~ 0025)
- ✅ v0.5 现状: 5 cargo tests + 1 GPU E2E integration test
- ✅ 完整文档: 25 ADRs + INSTALL.md + CI workflow

### License
MIT

---

## CHANGELOG

### v0.9.0 (2026-07-05) — 后台任务队列 + 经验蒸馏 Phase 1 + BFF API + FastAPI 迁移 (#5, #1, #9, #6)

**核心**: 从 v0.8.x 的 IDE/文档管理阶段进入**服务端架构升级阶段**。后台任务队列解决了长会议线程堆积和重复触发，经验蒸馏提供了"越用越聪明"的长期能力闭环，FastAPI 迁移为产品级 API 层铺路。

**改动**:
- 🗂️ **#5 后台任务队列**: 新增 `task_manager.py` — per-meeting 单任务队列 (debounce), generation_id 递增防旧覆盖, 全局 bounded ThreadPoolExecutor (4 workers), 可观测任务状态
- 🧠 **#1 经验蒸馏 Phase 1**: 新增 `experience.py` + `experience_store.py` — ExperienceItem 6 kind 数据模型, 会议结束自动从 MeetingState 提取候选, 聚合索引, batch_docs 生成前检索注入
- 🏗️ **#6 FastAPI 迁移**: 新增 `server/fastapi_app.py` (1367 行, 28 路由端点) — FastAPI app + StreamingResponse SSE + 配置化 CORS + 双兼容 (旧 http.server 仍可用)
- 🔌 **#9 BFF API P0**: `GET /api/meetings/{id}/aggregate` 会议聚合 DTO + `GET /api/client/device-status` 设备状态
- 🔐 **#4 JSON 并发保护 (完善)**: stream_meta/chat_history 加 per-meeting 文件锁, _append_chat_message 原子 read-modify-write
- 📝 **版本号**: pyproject/Cargo.toml/tauri.conf.json/package.json/__init__.py → 0.9.0

**核心**: v0.8.5 (fork 架构 + LLM env 透传) 发布后 CI 因 stash 冲突残留 + commands.rs 导入缺失而持续失败。本次**彻底修复 CI 流水线**,实现真正 One-Click Release。

**改动**:
- 🛠️ **修复 4 个文件的 stash 冲突标记**: Cargo.toml / kb_api.py / gpu_transcribe.py / conftest.py 的 `<<<<<<<` / `=======` / `>>>>>>>` 标记全部清理（根因: `git stash pop` 未完成冲突解决）
- 🦀 **`commands.rs` 合并回 `main.rs` 回归单文件模式**: 跨模块编译问题（pub fn 可见性、缺导入、proc-macro 冲突）导致持续 cargo check exit 101。合并后通过本地 + 开发服务器双验证
- 📥 **28 个编译错误零残留**: 缺 `get_log_path`/`save_gpu_url_to_yaml`/`ClientConfig`/`AudioConfig`/`SseConfig` 导入 + `#[tauri::command]` 函数 `pub` 关键字冲突
- 🚀 **CI 全线通过**: cargo test + check ✅ / Linux .deb ✅ / macOS (.dmg + .app) ✅ / Windows .exe ✅ — 全绿
- 📦 **Release 自动发布**: `v0.8.6` tag 触发 GitHub Release, 三平台安装包自动上传（之前只能从 Actions Artifacts 下载）

**影响**: 100% 客户端。服务端无改动。GPU 服务器不受影响。

详见 [v0.8.6 Release](https://github.com/zhangsheng377/vpbuddy/releases/tag/v0.8.6)。

### v0.8.0 (2026-07-02) — Phase 7 跨平台 loopback 真实现 (Linux PulseAudio mon / macOS BlackHole / Windows v0.9.x)

**核心**: v0.7.1 stub (`loopback`/`both` fallback mic) 落地 → v0.8.0 **真接** 跨平台内录: Linux PulseAudio/PipeWire monitor source + macOS BlackHole 设备 + `both` 模式 = 双 cpal Stream 并行 + 等权混合. Windows 仍 fallback mic + UI 强提示 (cpal 0.15 不暴露 cross-platform loopback, v0.9.x unsafe 重构).

**改动**:
- 🎛️ **`AudioCapture::new_with_source` 真接 loopback/both 路径**: mic path 100% 不变 (向后兼容 v0.7.x); loopback 调 `detect_default_loopback()` 找平台默认内录设备 → 找不到 fallback mic + warn; both path 双 cpal Stream + `mix_two_streams` 等权混合
- 🌐 **`is_loopback_device_name` + `detect_default_loopback` 平台分支**:
  - **Linux**: `.monitor` 后缀 (PulseAudio/PipeWire 约定)
  - **macOS**: 名字含 `BlackHole` / `Loopback` / `Soundflower` (case-insensitive)
  - **Windows**: 恒 `false` (cpal 0.15.3 不暴露 WASAPI loopback, 需 unsafe `IAudioRenderClient` — v0.9.x)
- 🎚️ **`AudioDeviceInfo` 加 `is_loopback: bool` 字段**: `list_input_devices` 每设备标 is_loopback; UI 切 `audio-source-kind` 时按 kind filter device dropdown (mic 只列 mic / loopback 只列 monitor / both 列全部)
- 🍎 **macOS banner**: 检测 BlackHole 缺失 → 显示 "🍎 装 BlackHole 2ch" 链接; 🪟 Windows banner: "Windows 真内录 v0.9.x 实现 — 当前 fallback 录麦克风, 系统声不会进"
- 🔁 **`mix_two_streams(mic, loopback)` pure helper**: 等权混合 `(m+l)/2` clamp i16 + 短端补零 (调用方负责)
- 🛡️ **`StreamGuard::Single | Merged` 枚举**: 保 1/2 个 cpal Stream 不掉线; both path 用 `Merged` variant + `Box::leak` 延寿 (process 生命周期内有效)
- 🧹 **`stop_capture` 清理 `audio_source` 字段** (v0.7.1 留值不重置, v0.8.0 收尾)
- 🧪 **11 个新 inline unit tests** (v0.7.1 6 + v0.8.0 11 = **17 总**): `is_loopback_device_name_linux/macos/windows` × 3 + `mix_two_streams_equal/overflow/mic_longer/lp_longer/negative/empty` × 6 + `downmix_to_mono_stereo/passthrough` × 2
- 📝 design v1.32 → v1.33 + 新 ADR-0032 + ADR-0031 标 Superseded + ADR index 加 0032 + ADR-0021 顶部加修订注 (cpal 0.15.3 不暴露 cross-platform WASAPI loopback)
- 🔢 pyproject 0.7.3 → 0.8.0

**验证**:
- ✅ `cargo check` 0 errors, 5 dead_code warnings (v0.7.x 留值 + `new_with_device` pub 兼容 + `mix_stereo_into` 留作 v0.9 重构用, expected)
- ✅ `cargo test --lib` **17/17 pass** in 0.00s
- ✅ mic path 完全等价 v0.7.x (向后兼容)
- ✅ Linux 真内录实测: 本机 `is_loopback_device_name("alsa_output.pci-0000_00_1f.3.analog-stereo.monitor")` = true

**v0.9.x 计划**:
- Windows WASAPI loopback 真实现 (`IAudioRenderClient` unsafe 包装, 最小 `#[cfg(target_os = "windows")]` 子模块)
- `both` 模式时间戳精确对齐 (mic + loopback 同 cpal 启时间, 取 timestamp ns diff ≤ 50ms 视为同帧)
- 引入 `soxr` 做 mic + loopback 不同采样率时的高质量重采样
- `AudioCaptureConfig` struct 替代 `new_with_source` 顺序参 (API ergonomics)

详见 [docs/decisions/0032-Phase7-跨平台loopback真实现.md](docs/decisions/0032-Phase7-跨平台loopback真实现.md) + [总体架构 v1.33](docs/design/总体架构.md)。

### v0.7.1 (2026-07-02) — Phase 7 客户端双轨采集 stub (microphone / loopback / both)

**核心**: 让客户端 Rust 端**真正读** `audio_source` 字段(`microphone|loopback|both`)——之前 UI 选 loopback/both 只发到 server,客户端 cpal 采集完全忽略;v0.7.1 把字段串通到 AudioCapture。**loopback/both 跨平台实现留 v0.8.x** (本次仅 stub + fallbak mic, **不破 v0.7.0 录音流程**)。

**改动**:
- 🎛️ **新增 `AudioCapture::new_with_source()` 公开 API**: `match audio_source { "microphone" => 现行 mic 路径; "loopback" => warn + fallback mic; "both" => warn + fallback mic; 未知 => warn + fallback mic }`。`new_with_device` 保留向后兼容 (内部拆 `self_new_with_device_inner`)
- 🔄 **AppState 加 `audio_source: Arc<Mutex<Option<String>>>` 共享字段**: `start_capture` 写入 `Some(audio_source_norm)` → `run_capture_loop` outer scope clone → `tokio::spawn_blocking` 内传给 `AudioCapture::new_with_source(device, &audio_source_bg)`
- 🧪 **6 个 inline unit tests 在 `audio.rs` 末尾 `#[cfg(test)] mod tests`**: `mix_stereo_into_full_and_zero / overflow_clamp / odd_length_panics / appends_not_clears` × 4 + `resample_linear_same_rate_identity / downsample_48k_to_16k` × 2. `cargo test --lib` 6/6 passed in 0.00s
- 🛠️ **`mix_stereo_into(dst, src)` pure helper 落库**: 双声道 L/R → 单声道等权平均, debug_assert 偶数长度, `(l+r)/2` clamp `i16::MIN..i16::MAX` 防削顶. v0.8.x both 路径可直接复用
- 📝 design v1.31 → v1.32 + 新 ADR-0031 + ADR index 加 0031
- 🔢 pyproject 0.7.0 → 0.7.1

**验证**:
- ✅ `cargo check` 0 errors, 4 dead_code warnings (`AudioCapture::new`, `mix_stereo_into`, `create_meeting`, `last_recv` — pub API + 复用预留, expected)
- ✅ `cargo test --lib` 6/6 pass
- ✅ mic path 完全等价 v0.7.0 (兼容)

**未实现 / v0.8 计划**:
- 真正 loopback 跨平台 cpal: Linux PulseAudio mon / macOS BlackHole / Windows WASAPI loopback
- 真正 both path: mic + loopback 双 stream 并行 + `mix_stereo_into` 复用
- stop_capture 重置 `audio_source` 字段 (当前留 Some 不清, 行为 OK 但不优雅)
- 跟随: UI `音频源` label 加 `(待 v0.8 实现)` 副标

详见 [docs/decisions/0031-Phase7-客户端双轨采集-stub落地.md](docs/decisions/0031-Phase7-客户端双轨采集-stub落地.md) + [总体架构 v1.32](docs/design/总体架构.md)。

### v0.7.0 (2026-07-01) — 协作提问层 (collab.md) + 6→2 kinds 合并 + UI 实时折叠面板

**核心**: 让 VP 跟 agent **双向对话式协作**—— agent 提问、VP 回答、增量改方向，文档跟方向一致。

**改动**:
- 🤝 **协作提问层 (ADR-0028)**: 新 `collab.py` 模块 (5 API: read_collab/parse_questions/list_pending/ask_question/answer_question), `docs/{mid}/collab.md` 三方共享文件 (chat agent / batch_docs / demo agent), 线程安全 (per-file Lock + fcntl.flock + Windows fallback), 节流 (同 mid+section+相似问题 1 次会议只 1 次), 3 个 HTTP 端点 (`GET /api/meetings/{id}/collab` · `POST /ask_question` · `POST /answer_question`), SSE `collab-update` 推流 (13 + 25 测试)
- 🔀 **6 sub-session → 2 batch agent (ADR-0029)**: req/arch/tasks/api/risk 5 老 prompt 合并为 1 个 `batch_docs.md` (Markdown 分隔符, 软约束, 1 次 LLM 输出 5 文档), demo agent 独立 (HTML 格式差异大), `SCHEDULED_KINDS = ("batch_docs", "demo")`, 老 kinds 兼容 stub 返 deprecated 警告, **LLM 调用 6→2 (-66%), 时间 3-5min → 1-2min, 一致性显著提升** (19 测试)
- 💬 **客户端协作疑问折叠面板 (ADR-0030)**: Chat 面板顶部 `<details>` 默认折叠 + N 徽标 + pending 列表 + 主动提问栏 (6 section 下拉 + 输入) + 已答折叠子区, SSE 实时刷新, 回答内嵌 textarea (无 modal), Enter 提交, 字段兼容 (asked_by/asker)
- 🔢 版本号全栈升 0.7.0: pyproject
- 📝 design v1.29 → v1.31 + 3 个新 ADR (0028/0029/0030)

详见 [docs/decisions/README.md](docs/decisions/README.md) + [总体架构 v1.31](docs/design/总体架构.md)。

### ⚠️ v0.6.0 (2026-07-01) — **设计稿发布,非实现完成**

> **本版本仅含设计稿 (6 个新 ADR + 文档同步 + 依赖声明),代码实现未完成。**
> ADR-0019 (RAG 选型) / 0020 (KB 方案废弃) / 0021 (客户端双轨) / 0022 (首页流程) / 0023 (chat 上传+主动) / 0024 (demo 多版本) / 0025 (agent 工具) 待实现。安装 `pip install -e .` 仍是 v0.5 行为,无新功能可用。

**改动**:
- 📝 6 个新 ADR (0019 ~ 0025) + AGENTS.md (项目协作铁律) + TODO_v0.6.md
- 📝 design / spec / pyproject / README 同步到 0.6 状态 (含 chromadb + pypdf + duckduckgo-search 依赖声明,但还没 import)
- 🔢 版本号全栈升 0.6.0: pyproject / Tauri Cargo.toml / tauri.conf.json / package.json / `__init__.py`

详见 [docs/decisions/README.md](docs/decisions/README.md) + [总体架构 v1.21](docs/design/总体架构.md)。
