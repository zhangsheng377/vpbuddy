# ADR-0050: 会议所有权隔离

**日期**: 2026-07-07
**状态**: 已实现
**标签**: `security` `multi-tenant`

## 决策

`GET /api/meetings/{id}/state`、`GET /api/meetings/{id}/chat/history` 两个端点增加 owner 校验——只允许会议创建者访问。非 owner 的认证用户返回 `403 Forbidden`。

## 问题

ADR-0047 引入 `owner_id` 字段后，`GET /api/meetings` 已按 owner 过滤，但 `GET /api/meetings/{id}/state` 等单会议端点未校验——任何认证用户都能通过遍历 meeting_id 读取不属于自己的会议数据。

## 方案

新增 `_require_meeting_owner(meeting_id, user)` 函数：

1. 从磁盘加载 `MeetingState.owner_id`
2. 与 JWT `user["user_id"]` 比对
3. 不一致返回 `403 access denied`
4. 会议不存在返回 `404`

应用到：`get_meeting_state`、`get_meeting_chat_history`。后续可按需扩展到其他单会议端点。

## LLM 模型

不再硬编码模型名。所有 `AIAgent` 创建从 `.env` 读取 `MODEL=minimax-m3`，Hermes 配什么模型就用什么。删除 `VPBUDDY_LLM_MODEL` 环境变量。

## Demo 占位

无会议内容时不渲染系统流程模板（旧行为会暴露"VPBuddy 演示"、"工作流"、"部署 curl" 等内部实现细节）。改为显示"暂无会议内容, 等待音频采集"占位文案。
