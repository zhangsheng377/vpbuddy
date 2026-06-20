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
