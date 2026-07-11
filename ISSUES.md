# VPBuddy 问题跟踪

> 创建: 2026-07-04
> 最后更新: 2026-07-12
> 来源: 代码审查报告 [CODE_REVIEW.md](./CODE_REVIEW.md) + GitHub Issues #32–#37

---

## 使用说明

- **状态**: `待处理` / `处理中` / `已完成` / `已关闭(非问题)`
- **严重性**: 🔴 P1 = 阻塞/紧急 🟡 P2 = 重要 🟢 P3 = 可优化
- GitHub Issues 对应: `#N` = `https://github.com/zhangsheng377/vpbuddy/issues/N`

---

## ✅ 已完成 (v0.22.4–v0.22.5)

| # | 问题 | 状态 | 效果 |
|---|------|------|------|
| #33 P0-2 | WS 失败设 `capturing=false` 连带杀 SSE → demo 消失 | ✅ v0.22.5 | WS 失败只 break，不碰 flag |
| #35 P0-1 | `_gkd_runner(mid: str)` 签名不匹配 task_manager 的 `runner(gen_id, mid)` | ✅ 707a50e | gkd 文档调度正常触发 |
| SSE-lifetime | SSE 与音频采集绑死 (停采集=断 SSE)，demo-new-version 在 SSE 断开后才生成 | ✅ v0.22.4 | sse_active 独立 flag，close 后保持 30s |
| demo-placeholder | 空会议生成 v1="等待更多会议内容"占位 demo，真实 demo 到 v3/v4 但版本列表不刷新 | ✅ v0.22.5 | 占位拒绝写入 + demo-new-version 事件链路完整 |
| bailian-key | 服务端 `nohup vpbuddy ui` 启动无百炼 key → ASR 报 401 | ✅ v0.22.4 | 必须 `bash run.sh` 启动，文档已更新 |

---

## 🟡 P2 — 仍存在 (GitHub Issues #32/#33/#35 剩余项)

### #33 P0-3: SSE `doc-update` 推送全量文档内容

| 字段 | 值 |
|------|-----|
| **文件** | `sub_session_controller.py` L698-709 |
| **症状** | 每个 doc-update 的 `content` 字段包含完整 markdown/HTML（单条 18KB+），频繁生成时日志爆炸 + 前端全量解析 |
| **建议** | SSE 只推 `{kind, status, doc_size}` 元信息，客户端按需 `GET /api/meetings/{id}/docs/{kind}` |

### #35 P1: 相同内容重复调度 Agent

| 字段 | 值 |
|------|-----|
| **文件** | `fastapi_app.py` _gkd_loop |
| **症状** | 即使文档内容无实质变化（"仅 last_updated 时间戳变化"），gkd 仍在每 6s 扫描时重复触发 agent 生成 |
| **建议** | 对比 `demo_version` 已有版本的 content hash，只有实质变化才重新触发 |

### #35 P2: SSE 反复断开重连 + subscriber 数量增长

| 字段 | 值 |
|------|-----|
| **文件** | `realtime_server.py` + `main.rs` `run_sse_loop` |
| **症状** | `SSE 断开: error decoding response body` → 重连 → subscribers=1→2→3 |
| **建议** | 旧 SSE 连接关闭时同步清理订阅记录，客户端重连前等 1s debounce |

---

## 🟢 P3 — 可优化 (GitHub Issues #34/#36/#37)

| # | 问题 | 来源 |
|---|------|------|
| #36 | ASR 降噪后转写未闭环 + 图片/PDF 材料无法驱动 Demo Agent | [issue](https://github.com/zhangsheng377/vpbuddy/issues/36) |
| #34 | 请求发布版本化 API、OpenAPI 契约、WS/SSE 协议与兼容性策略 | [issue](https://github.com/zhangsheng377/vpbuddy/issues/34) |
| #37 | 语音指定历史版本修改 + Agent 自主检索决策 + 个性化经验闭环 | [issue](https://github.com/zhangsheng377/vpbuddy/issues/37) |

---

## 统计

| 级别 | 已完成 | 待处理 |
|------|--------|--------|
| 🔴 P0/P1 | 4 (#33-P0-2, #33-P0-1, SSE-lifetime, demo-placeholder) | 2 (#33 P0-3 SSE全量, #35 P1 重复调度) |
| 🟡 P2 | 1 (bailian-key) | 1 (#35 P2 SSE subscriber 泄漏) |
| 🟢 P3 | — | 3 (#34 API契约, #36 ASR/材料, #37 语音版本) |
| **合计** | **5** | **6** |
