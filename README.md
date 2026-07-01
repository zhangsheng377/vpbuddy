# VPBuddy

> **本地优先的会议操作系统级 AI 助手** —— 为 VP / 售前 / 项目负责人设计,运行在 VP 自己桌面客户端,数据完全本地化。

**v0.7.0** (2026-07-01) — 协作提问层 + 6→2 kinds 合并 + UI 实时折叠面板 (见 CHANGELOG)。**v0.6.0** (2026-07-01) — 8 项产品需求合入:RAG 切 Chroma 嵌入式 / KB 改用户主动上传+会议隔离 / 客户端麦克风+内录双轨 / 首页强制会议选择 / chat 上传+agent 主动 / demo 多版本 / agent 网络搜索+KB 工具。详见 [CHANGELOG](#v060-2026-07-01-8-项需求合入) + [ADR-0019 ~ 0025](docs/decisions/README.md)。

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

VPBuddy 桌面客户端通过 GitHub Actions 自动编译（三平台），产物保存 30 天：

1. 打开 [Actions → VPBuddy Tauri Multi-Platform Build](https://github.com/zhangsheng377/vpbuddy/actions/workflows/tauri-multi-build.yml)
2. 点击最新成功的 run（绿色 ✅）
3. 滚动到底部 "Artifacts" 区域
4. 下载对应平台：

| 平台 | 下载 | 安装方法 |
|------|------|----------|
| **Linux** | `vpbuddy-client-linux` (zip, 内含 .deb) | `sudo dpkg -i vpbuddy-client_*.deb` <br> 或双击安装 |
| **macOS** | `vpbuddy-client-macos-app` (.app) + `vpbuddy-client-macos-dmg` (.dmg) | 下载后拖到 `应用程序` 文件夹 (Apple Silicon 需 Rosetta, 详见 ADR-0026) |
| **Windows** | `vpbuddy-client-windows` (zip, 内含 .exe) | 直接运行 vpbuddy-client.exe |

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
┌─────────────────────────────────────────────────────┐
│  VP 桌面客户端 (Ubuntu 24.04 / macOS 14+ / Win11)  │
├─────────────────────────────────────────────────────┤
│  Audio loopback (PipeWire / WASAPI / BlackHole)     │
│        ↓                                            │
│  ASR (funasr paraformer-zh)                        │
│        ↓                                            │
│  MeetingState (5 类事实累积)                       │
│        ↓                                            │
│  6 × sub_session (in-process AIAgent)              │
│  ┌────┬────┬────┬────┬────┬────┐                  │
│  │req │arch│tasks│api │risk│demo│                  │
│  └────┴────┴────┴────┴────┴────┘                  │
│        ↓                                            │
│  Knowledge Base (sqlite-vec + sentence-transformers)│
│        ↓                                            │
│  Web UI (FastAPI + Vanilla JS, port 8765)          │
└─────────────────────────────────────────────────────┘

可选: GPU 服务器 (cuda) 跑 ASR/embedding,加速
```

### 文档

| 主题 | 链接 |
|------|------|
| **架构** | [docs/design/总体架构.md](docs/design/总体架构.md) |
| **产品需求** | [docs/product-spec/](docs/product-spec/) |
| **决策记录** | [docs/decisions/](docs/decisions/) (ADR-0004 / 0005 / 0009) |
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

### 状态(2026-06-24)

- ✅ MVP 全链路 work: 音频 → ASR → 6 文档 → KB → UI 检索
- ✅ Tauri 桌面客户端编译过 (`cargo build --release`) + 6 子 session E2E 联调通过
- ✅ 5 个 cargo test + 1 个 GPU E2E 联调测试
- ✅ 文档齐全: 16 个 ADR + INSTALL.md + CI 工作流

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
