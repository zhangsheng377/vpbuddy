## Phase 0 审计进度回复

### 已完成

**审计报告**: [ADR-0056 `docs/decisions/0056-data-isolation-audit.md`](https://github.com/zhangsheng377/vpbuddy/blob/main/docs/decisions/0056-data-isolation-audit.md) — 含完整代码调用链 + 12 项数据对象隔离矩阵。

### 审计结论

| 编号 | 问题 | 结论 | 行动 |
|------|------|---|---|
| A | KB 检索不按 meeting_id 过滤 | **by design** — KB 按 user_id 隔离，同用户可跨会议检索 | 无 |
| B | `handle_chat_upload` 缺 scope 字段 | **confirmed** | ✅ 已修复，补 `scope=meeting_material` |
| C | Experience 无 PII 检测/脱敏/抽象 | **confirmed** | 暂缓 — 先不做，避免隐含问题 |
| D | `search_experiences()` 无任何过滤参数 | **confirmed** | ✅ 已加 `exclude_meeting_id`，batch_docs 传参 |
| E | `DELETE /api/meetings/{id}` 不清理 uploads/KB/agent cache/experience | **confirmed** | ✅ 已补 4 项清理 |
| F | Agent 文件工具无目录 sandbox | **confirmed** | 暂缓 |
| G | `stream_start` reuse 重置 transcript | **confirmed** | ✅ 已修复，reuse 时保留 |
| H | `parent_session_id` fork 不生效 | **known** | 已有手动注入补偿 (ADR-0055) |
| I | 反复出现的人名来源 | **suspected** | 服务器抽样: `_all.json` 仅 2 条全未 approved; chat.json 含正常会议客户名 |

### 关于现象 1（新会议 Demo 出现旧会议需求）

最可疑路径是 **Experience 原文注入 batch_docs agent** — `extract_from_meeting_state()` 将 `requirements` 原文无过滤地写为 `ExperienceItem.text`，`search_experiences()` 返回所有已确认经验注入 agent prompt。当前生产 `_all.json` 仅有 2 条且全部未 approved，理论上不会注入。但如果之前有 approved 条目，可能会泄漏。本次修复已加 `exclude_meeting_id` 防止当前会议经验自我循环。

### 关于现象 2（反复出现的陌生人名）

生产数据抽样: Experience 不包含姓名；chat.json 中 22 处命中包含正常会议内容（如客户公司名）。更可能来自 agent 的 KB 检索 — agent `kb_search` 只按 `user_id` 过滤，可能命中同用户旧会议的 KB 片段。**这是期望行为**（KB 产品定义即按 user 隔离），但用户期望是"其他会议不可注入"——需产品侧确认是否需要调整 KB 注入策略。

### 待完成 (Phase 0 剩余)

- [ ] 搭建用户 A/B 隔离矩阵可复现测试
- [ ] 按 Issue §10 增加 generation 上下文来源 trace

### 修复部署

以上 B/D/E/G 四项修复已合并 `main` 并部署到生产服务器，进程 `PID=88596`，健康检查 `{"ok":true}`。
