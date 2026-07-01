# 架构决策记录 (Architecture Decision Records)

本目录用于记录 VPBuddy 的关键架构决策,采用 ADR 模式(参考 [MADR](https://adr.github.io/madr/))。

## 命名规则

`NNNN-简短标题.md`,其中 `NNNN` 是 4 位递增序号。

例如:

- `0001-采用多agent协同架构.md`
- `0002-知识库分层设计.md`

## 推荐模板

```markdown
# NNNN. <标题>

- **状态**: 提议 / 已接受 / 已废弃 / 已取代(被 NNNN 取代)
- **日期**: YYYY-MM-DD
- **作者**: 

## 背景与问题

<什么决策需要被做出?在什么背景下?有哪些约束?>

## 考虑的选项

1. 选项 A —— <描述>
2. 选项 B —— <描述>
3. 选项 C —— <描述>

## 决策

<选择了哪个选项?为什么?>

## 后果

- 正面: <...>
- 负面: <...>
- 中性: <...>
```

## 当前 ADR 列表

| 编号 | 标题 | 状态 | 日期 |
|---|---|---|---|
| [0001](./0001-MVP-选型.md) | MVP 选型(第一平台/范围/租户/开源/数据归属) | Accepted | 2026-06-20 |
| [0002](./0002-UI-vs-架构冲突-review.md) | UI vs 架构冲突 Review(9 个冲突 + 6 决策) | Accepted | 2026-06-20 |
| [0003](./0003-MVP-Step1-YAGNI-review.md) | MVP Step 1 YAGNI Review | Accepted | 2026-06-20 |
| [0004](./0004-MVP-Step2-ASR设计.md) | MVP Step 2 — Whisper + pyannote 链路 | Accepted | 2026-06-20 |
| [0005](./0005-ModelScope-替代HF_TOKEN.md) | ModelScope 镜像替代 HF_TOKEN(国内无账号) | Accepted | 2026-06-21 |
| [0006](./0006-MVP-Step3-子session架构.md) | MVP Step 3 — 6 个常驻子 session 循环架构 | Accepted | 2026-06-21 |
| [0007](./0007-多平台适配器架构.md) | 多平台 MeetingAdapter 抽象(防锁定) | Accepted | 2026-06-21 |
| [0008](./0008-ADR-0001-决策1-Superseded.md) | ADR-0001 决策 1 (飞书第一平台) Superseded | Superseded | 2026-06-21 |
| [0009](./0009-部署架构-Hermes-runtime.md) | 部署架构 — 以 Hermes Agent 作为生产 runtime | Accepted | 2026-06-21 |
| [0010](./0010-信息隔离-deployment-clean-install.md) | 信息隔离 — deployment clean install 铁律 | Accepted | 2026-06-21 |
| [0011](./0011-HF模型离线铁律.md) | HF 模型离线铁律 (国内网络) | Accepted | 2026-06-22 |
| [0012](./0012-RAG-sqlite-vec本地知识库.md) | RAG 接入 — sqlite-vec + sentence-transformers 本地知识库 | **Superseded by [0019](./0019-RAG-选型-Chroma-嵌入式.md)** | 2026-06-23 |
| [0013](./0013-流式E2E-端到端工作流.md) | 流式 E2E 端到端工作流 (stream chunk + 5+1 agent in-process) | Accepted | 2026-06-23 |
| [0014](./0014-VPBuddy装成Hermes-Skill.md) | VPBuddy 装成 Hermes Skill (5 doc + 1 demo agent) | Accepted | 2026-06-23 |
| [0015](./0015-桌面客户端技术选型.md) | 桌面客户端技术选型 — Tauri 2.x + Rust (cpal 跨平台音频) | Accepted | 2026-06-24 |
| [0016](./0016-桌面客户端技术选型-原15.md) | (老编号, 文件名漂移) | (已合并入 0015) | — |
| [0017](./0017-SSE-stream-close语义与heartbeat修复.md) | SSE stream close 语义 + heartbeat 修复 | Accepted | 2026-06-28 |
| [0018](./0018-SSE-chunked-meeting-complete-stop语义.md) | SSE chunked meeting-complete / close 语义 | Accepted | 2026-06-28 |
| [0019](./0019-RAG-选型-Chroma-嵌入式.md) | **RAG 选型 — Chroma 嵌入式 + sentence-transformers** | Accepted | 2026-07-01 |
| [0020](./0020-知识库-废弃旧库-文件上传-会议隔离.md) | **知识库方案废弃 + 文件上传 + 会议隔离** | Accepted | 2026-07-01 |
| [0021](./0021-桌面客户端-麦克风+内录双轨.md) | **桌面客户端支持麦克风 + 内录双轨 (跨平台)** | Accepted | 2026-07-01 |
| [0022](./0022-首页录音-强制会议选择创建.md) | **首页录音流程 — 强制会议选择/创建 + 6 doc 完成不关会议** | Accepted | 2026-07-01 |
| [0023](./0023-chat-上传-主动提问.md) | **chat 页面支持文件/图片上传 + agent 主动提问** | Accepted | 2026-07-01 |
| [0024](./0024-demo-版本号-多版本切换.md) | **demo 版本号 + 多版本切换** | Accepted | 2026-07-01 |
| [0025](./0025-agent-网络搜索-KB检索.md) | **agent 网络搜索 + KB 检索工具** | Accepted | 2026-07-01 |
| [0026](./0026-macOS-CI-去-no-bundle产出app和dmg.md) | **macOS CI 去 no-bundle + 产出 app 和 dmg** | Accepted | 2026-07-01 |
| [0028](./0028-协作提问层-collab-md三方共享.md) | **协作提问层 — collab.md 三方共享 (Commit 1)** | Accepted | 2026-07-02 |
| [0029](./0029-6sub-session合并为2batch-agent.md) | **6 sub-session 合并为 2 batch agent (一致性 + 速度)** | Accepted | 2026-07-01 |
| [0030](./0030-协作提问层-UI面板-实时SSE推流.md) | **协作提问层 — UI 面板 + 实时 SSE 推流** | Accepted | 2026-07-02 |
| [0031](./0031-Phase7-客户端双轨采集-stub落地.md) | **Phase 7 客户端双轨采集 stub 落地 (microphone/loopback/both)** | Accepted (Stub) → Superseded by 0032 | 2026-07-02 |
| [0032](./0032-Phase7-跨平台loopback真实现.md) | **Phase 7 跨平台 loopback 真实现 (Linux PulseAudio mon / macOS BlackHole / Windows v0.9.x)** | Accepted | 2026-07-02 |

## 更新原则 (2026-06-23 张胜东立的铁律)

- **代码先于文档**: 改架构先改代码, 验证代码落地, 再补 ADR
- **ADR 驱动**: 每次架构变更 = 1 ADR + 同步 design / spec / 部署 / README / pyproject + 1 commit
- **读 ADR 按编号**: `ls docs/decisions/` 从小到大读, 看顶部"Superseded by"
- **假设错立即承认 + 修文档**: 不"为 ADR 辩护", "代码 = 真相"
