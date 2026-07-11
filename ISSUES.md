# VPBuddy 问题跟踪

> 创建: 2026-07-04
> 最后更新: 2026-07-12 (v0.22.6 全收口)
> 来源: 代码审查报告 [CODE_REVIEW.md](./CODE_REVIEW.md) + GitHub Issues #32–#37

---

## 使用说明

- **状态**: `待处理` / `处理中` / `已完成` / `已关闭(非问题)`
- **严重性**: 🔴 P1 = 阻塞/紧急 🟡 P2 = 重要 🟢 P3 = 可优化
- GitHub Issues 对应: `#N` = `https://github.com/zhangsheng377/vpbuddy/issues/N`

---

## ✅ 已完成 (v0.22.4–v0.22.6)

| # | 问题 | 修复 | 提交 |
|---|------|------|------|
| **#32 P0-1** WS断连→杀SSE | WS `send_frame()` 失败只 break, 不设 `capturing=false` | v0.22.5 |
| **#32 P0-2** SSE与采集耦合 | 新增 `sse_active` 独立 flag, 停采集后保持30s | v0.22.4 |
| **#33 P0-1** `_gkd_runner(mid)` 签名错误 | 改为 `_gkd_runner(gen_id, mid)` 匹配 task_manager | v0.22.5 |
| **#33 P0-2** WS失败设 `capturing=false` | 同 #32 P0-1 | v0.22.5 |
| **#33 P0-3** SSE `doc-update` 推全量文档 | 去掉 `content` 字段, 只推 `{kind, status, doc_size}` | v0.22.6 |
| **#33 P0-4** KB content_hash 跨用户误判重复 | `rag.get(where=)` 加 `user_id` 过滤 | v0.22.6 |
| **#35 P0-1** `_gkd_runner` 签名错误 | 同 #33 P0-1 | v0.22.5 |
| **#35 P1** 相同内容重复调度 Agent | `_gkd_loop` 结合 `cleaned_text` + `latest_demo_content_hash()` 双重 hash | v0.22.6 |
| **#35 P2** SSE subscriber 泄漏 | `_gkd_loop` 每轮扫描后调 `cleanup_meetings_without_subscribers()` | v0.22.6 |
| demo占位版本 | 空会议生成 v1="等待更多会议内容", 真实 demo 到 v3/v4 但版本列表不刷新 | `write_demo_version` 拒绝占位写入 + `demo-new-version` 事件链路完整 | v0.22.5 |
| 百炼 API key 丢失 | `nohup vpbuddy ui` 无 key → ASR 401 | 必须 `bash run.sh` 启动 | v0.22.4 |
| chat 上传污染 prompt | 文本文件内容完整塞进 prompt | 改为只放文件路径, agent 用 read_file 按需读取 | v0.22.6 |

---

## 🟢 已关闭 (不修)

| # | 问题 | 关闭理由 |
|---|------|----------|
| #36 | ASR 降噪 + 材料→Demo | chat 上传已走路径注入（不塞内容）；材料→Demo Agent 通过 `parent_session_id` fork 自动继承上下文；噪音过滤是现有链路的优化项非 bug |
| #37 | 语音版本 + Agent 记忆 | 新 feature 大功能，不在当前 milestone 范围 |
| #34 | API 契约稳定性 | `docs/api-reference.md` v0.22.6 已是最新，api 契约属非阻塞项 |

---

## 统计

| 级别 | 已修复 | 已关闭(不修) | 仍待处理 |
|------|--------|-------------|----------|
| 🔴 P0/P1 | 5 (#32-P0-1, #32-P0-2, #33-P0-1~4) | — | 0 |
| 🟡 P2 | 4 (#35-P1, #35-P2, chat污染, KB去重) | #34, #36 | 0 |
| 🟢 P3 | — | #37 | 0 |
| **合计** | **12** | **3** | **0** |
