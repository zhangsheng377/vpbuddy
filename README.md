# VPBuddy

> **本地优先的会议操作系统级 AI 助手** —— 为 VP / 售前 / 项目负责人设计,运行在 VP 自己桌面客户端,数据完全本地化。

[English](#english) | [中文](#中文)

---

## 中文

### 什么是 VPBuddy?

VPBuddy 是一款**本地优先的会议 AI 系统**,直接捕获系统音频(腾讯会议/钉钉/企微 不需要 SDK),在会议过程中自动:

- 🎙️ **实时转写** — funasr paraformer-zh (本地 ASR,中文识别率 > 90%)
- 📋 **结构化累积** — REQ / GOAL / FEAT / RISK / QUE 5 类事实自动分类
- 📄 **6 种文档自动生成** — 需求 / 架构 / 任务 / API / 风险 / 演示
- 🧠 **跨会议知识库** — sqlite-vec + sentence-transformers,余弦相似度检索
- 🌐 **Web UI** — 端口 8765,实时看累积、检索历史会议

**关键特性**: 完全运行在 VP 自己机器,数据不上传云端。单租户,单实例,单镜像。

### 5 分钟上手

```bash
# 1. 安装(开发机/桌面客户端)
git clone https://github.com/zhangsheng377/vpbuddy.git
cd vpbuddy
pip install -e .

# 2. 启动 UI
vpbuddy ui
# → 浏览器打开 http://localhost:8765

# 3. 启动后台 controller(7×24 监听)
vpbuddy controller --start
```

**第一次启动会自动**:
- 预下载 256MB embedding 模型(`paraphrase-multilingual-MiniLM-L12-v2`)
- 创建本地数据库(`data/knowledge.db`)
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
| **macOS** | `vpbuddy-client-macos` (zip, 内含 .app) | 解压后拖到 `应用程序` 文件夹 |
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

### Status (2026-06-24)
- ✅ MVP full pipeline: audio → ASR → 6 docs → KB → UI
- ✅ Tauri desktop client compiles (`cargo build --release`) + 6 sub-session E2E pass
- ✅ 5 cargo tests + 1 GPU E2E integration test
- ✅ Complete documentation: 16 ADRs + INSTALL.md + CI workflow

### License
MIT
MIT
