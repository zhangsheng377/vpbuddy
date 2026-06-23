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
| [0010](./0013-流式E2E-端到端工作流.md) | 流式 E2E 端到端工作流 (stream chunk + 5+1 agent in-process) | Accepted | 2026-06-23 |
| [0011](./0014-VPBuddy装成Hermes-Skill.md) | VPBuddy 装成 Hermes Skill (5 doc + 1 demo agent) | Accepted | 2026-06-23 |
| [0012](./0015-RAG-sqlite-vec本地知识库.md) | RAG 接入 — sqlite-vec + sentence-transformers 本地知识库 | Accepted | 2026-06-23 |
| [0013](./0016-桌面客户端技术选型.md) | 桌面客户端技术选型 — Tauri 2.x + Rust (cpal 跨平台音频) | Accepted | 2026-06-24 |

## 更新原则 (2026-06-23 张胜东立的铁律)

- **代码先于文档**: 改架构先改代码, 验证代码落地, 再补 ADR
- **ADR 驱动**: 每次架构变更 = 1 ADR + 同步 design / spec / 部署 / README / pyproject + 1 commit
- **读 ADR 按编号**: `ls docs/decisions/` 从小到大读, 看顶部"Superseded by"
- **假设错立即承认 + 修文档**: 不"为 ADR 辩护", "代码 = 真相"
