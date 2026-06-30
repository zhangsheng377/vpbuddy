# VPBuddy v0.6 — 待办与设计任务清单

> 创建: 2026-07-01
> 作者: 张胜东 (起草: Hermes)
> 关联: 总体架构 v1.20 (2026-06-24) → v1.21 (待写, 含本批 8 项)

## 背景

8 项产品需求, 需要先做项目设计 (同步现有 design / spec / ADR, ADR-0019 ~ 0025),
再进入实现。每项写一个独立 ADR + 同步到 design.md / spec / pyproject / README。

## 需求清单 (8 项)

### 1. 录音支持麦克风 + 内录 (loopback)
- **现状**: `src/vpbuddy/loopback.py` 已实现 Linux PipeWire/PulseAudio 端到端录音
  (`capture_loopback()`)。但 Tauri 客户端 `vpbuddy-client/src-tauri/src/audio.rs`
  只用 cpal 麦克风 (`host.input_devices()`), 没暴露 loopback 选项。
- **现状 (服务端)**: `src/vpbuddy/ui_server.py` + `scripts/gpu_transcribe.py`
  不接 loopback, 只能接客户端推上来的 wav。
- **目标**: 客户端 Tauri 同时支持麦克风 + 系统内录 (Mac BlackHole / Windows WASAPI),
  服务端保留 loopback 端到端备选。UI 提供"内录"开关 + 设备选择下拉。
- **设计依据**: ADR-0004 (ASR 选型, 不依赖会议平台 SDK) + ADR-0016 (桌面客户端技术选型)

### 2. chat 页面文件/图片上传按钮
- **现状**: Tauri 客户端 `<section id="panel-chat">` 只有文本输入框
  (`#chat-input` textarea + `#chat-send` button)。
  服务端 `/api/meetings/{id}/chat` (推断) 不接附件。
- **目标**: chat 区域加 📎 按钮 → 选文件/图片 → multipart 上传 →
  后端保存到 `data/uploads/{meeting_id}/` → 6 doc agent 可读 → chat 历史带附件引用。

### 3. 知识库方案废弃旧库 + 文件上传 + 会议隔离 + 切换轻量 RAG
- **现状**: `src/vpbuddy/knowledge_base.py` (ADR-0012) **自动** ingest 6 子 session 产物
  (`sub_session_controller.py:530 kb.add_document(meeting_id, doc_kind, content)`),
  **无会议隔离开关** (全会议入库, KB 列表混在一起)。
- **现状 (RAG 实现)**: 手写 `sqlite-vec` + `sentence-transformers` + WAL + 写锁, 470MB
  embedding 模型 CPU 推理 ~50ms/doc。
- **目标**:
  1. **废弃**自动 ingest 6 docs 入库逻辑 (`sub_session_controller.py:520-580` 整段
     改成只 update in-memory + 推 SSE, 不写 KB)
  2. **加隔离**: KB API 支持 `meeting_id` 过滤 (已有, 改默认行为: 默认仅当前会议)
  3. **加文件上传**: 知识库页加上传按钮 → 用户上传文件 → 入库 (用户主动)
  4. **切轻量 RAG**: 评估超轻 pip-install RAG (llama-index / chroma / qdrant-client
     server-less), 决策后写 ADR-0019
- **设计依据**: ADR-0012 superseded by ADR-0019 (新 RAG 选型)

### 4. 首页录音流程改造 (会议选择/创建)
- **现状**: Tauri `ui/main.js:107-110` `invoke("start_capture", ...)` = 直接开新会议,
  没"选旧会议/输入新会议"前置。
- **目标**: 首页录音按钮变禁用, 必须:
  1. 下拉条选旧会议 (从 `/api/meetings` 拉), 或
  2. 文本框输入新会议名 (校验非空, 不重名), 或
  3. 啥都不选不输入 → 按钮 disabled
  4. 停止录音 ≠ 结束会议 (用户分开控制)
- **设计依据**: 总体架构 v1.20 §"客户端-服务端架构"

### 5. chat 页面允许 agent 主动提问
- **现状**: chat 是纯用户发起, agent 只能被动答。6 doc agent 完成时只是
  `push_event(meeting_id, "doc-update", ...)` 推状态, 不主动在 chat 留话。
- **目标**: agent 完成工作时, 在 chat 历史里 append 一条 assistant 消息
  (例如"📄 需求文档已生成 v2, 主要变更: ..."), 用户能看到 agent 主动汇报。
  阈值/触发: doc 生成完成 / 风险命中 / demo 出新版本。

### 6. demo 要有版本号 + 多版本切换
- **现状**: `panel-docs` 6 doc 块, `panel-demo` 一个 iframe。Demo agent 生成
  HTML 后推到 `docs/{meeting_id}/demo.html` (推断), 没版本号, 切换要看 git。
- **目标**:
  1. demo 存 `docs/{meeting_id}/demo_v1.html`, `demo_v2.html`, ... 增量
  2. 客户端 demo 页面顶部加版本下拉条 (类似 IDE tab), 切换 iframe src
  3. SSE 推新 demo 事件带 `version` 字段, 客户端自动追加
  4. 默认显示最新版本

### 7. 各 agent 都能网络搜索 + 主动调用知识库
- **现状**: 6 doc agent 走 `sub_session_controller` → `engine` → LLM, 没 web 搜索
  工具, 没 KB 检索工具 (KB 检索只在 `ui_server.py:557` `/api/kb/search` 给 UI 用)。
- **目标**: 6 doc agent + demo agent + chat agent 都能:
  1. **网络搜索**: 通过 web_search / tavily / ddgs 公开 API 查外部信息
  2. **KB 检索**: 调 `KB.search(query, top_k=5, meeting_id=current)` 拉历史会议
- **设计依据**: 总体架构 v1.20 §"AI 主动行为边界" — 检索是只读, 符合"展示范围仅限
  VPBuddy 内部"。

### 8. RAG 框架选型 — 切现成 pip 包?
- **现状**: 手写 sqlite-vec + sentence-transformers (470MB 模型, 50ms/doc 推理)
- **目标**: 评估超轻 pip-install 方案:
  1. **llama-index** (核心包 + 默认 `SimpleVectorStore` in-memory) — 全套 RAG 抽象
  2. **chromadb** (0.5+) — 嵌入式模式, 持久化到本地文件
  3. **qdrant-client** (server-less 模式) — Rust 内核, 性能好
  4. **faiss-cpu** + 手写 — 最低层, 但不送 embedding
  5. **lancedb** — 嵌入式 lance 列存, 支持 embedding 内置
- **决策维度**: 体积 (<200MB 优先) / 易装 (pip install 一步) / 中文支持 / 是否需要 GPU /
  跟 VPBuddy "全本地单文件" 哲学是否冲突
- **输出**: ADR-0019 选型决策 + 迁移路径

## 设计阶段 (本批 8 项需求 6 个 ADR)

| 编号 | 标题 | 状态 |
|------|------|------|
| ADR-0019 | RAG 框架选型 (llama-index / chroma / lancedb / 维持自写) | 待写 |
| ADR-0020 | 知识库方案废弃 + 文件上传 + 会议隔离 | 待写 |
| ADR-0021 | 桌面客户端支持麦克风 + 内录双轨 (跨平台) | 待写 |
| ADR-0022 | 首页录音流程 — 强制会议选择/创建 | 待写 |
| ADR-0023 | chat 页面支持文件/图片上传 + agent 主动提问 | 待写 |
| ADR-0024 | demo 版本号 + 多版本切换 | 待写 |
| ADR-0025 | agent 网络搜索 + KB 检索工具 | 待写 |

## 同步更新 (设计阶段产物)

- `docs/design/总体架构.md` → v1.21 (新增 6 ADR 引用)
- `docs/product-spec/VPBuddy_产品说明书.md` → v2.0 (合并 8 项需求, 删旧 KB 默认 ingest 描述)
- `pyproject.toml` → 0.3.0 (新 RAG 依赖 / 客户端 Tauri 2.6+ 约束)
- `README.md` → 8 项需求要点 (中英)
- `AGENTS.md` (新建, 项目根) — 必读铁律 (事实陈述 / ADR 驱动 / 真命令验证)
- `src/vpbuddy/_version.py` → 0.6.0

## 实现阶段 (设计稿确认后开工)

按 1 → 8 优先级:
1. ADR-0019 选 RAG → 装新依赖 (设计稿先确认, 装包后续)
2. ADR-0021 + 0022 (客户端 + 首页, 用户最先感知的改动)
3. ADR-0020 (废弃旧 KB, 改上传, 隔离)
4. ADR-0023 (chat 上传 + agent 主动)
5. ADR-0024 (demo 版本)
6. ADR-0025 (agent 工具)

每项 commit 完 push + CI + release。
