# 0029. 6 sub-session 合并为 2 batch agent (一致性 + 速度优化)

- **状态**: 已接受
- **日期**: 2026-07-01
- **作者**: 张胜东 (起草: Hermes)
- **替代**: ADR-0009 (sub-session 架构 6 并行) 部分 superseded — 6 → 2 kinds
- **依赖**: [ADR-0028](../decisions/0028-协作提问层-collab-md三方共享.md) (collab 协议层) · [ADR-0006](../decisions/0006-MVP-Step3-子session架构.md) (子 session 架构基础)

## 背景

2026-07-01 张胜东提议: 现有架构里 6 个 sub-session agent (req/arch/tasks/api/risk/demo) 各自独立跑 LLM,有几个问题:

1. **冗余调用**: 5 个 doc agent 各自读 state + 各自写文档, 重复劳动
2. **一致性差**: 6 个 agent 各自"理解" state,经常出现 req 提的预算 arch 不认 / tasks 列的工期 API agent 反对
3. **耗时**: 6 个 LLM 调用 + 6 个文件 IO, 实测 3-5 min / 轮
4. **调试难**: 6 个独立 prompt 模板 + 6 个独立 session_id, 出问题不知道哪个 agent 错了

## 决策

### 1. 5 文档 agent 合并为 1 个 batch_docs agent

**1 次 LLM 调用, 输出 5 个 Markdown 文档**:

| 老 (6 sub-session) | 新 (2 sub-session) |
|---|---|
| req agent (LLM call #1) | batch_docs agent (LLM call #1) |
| arch agent (LLM call #2) | ↑ 1 次调用输出 5 文件 |
| tasks agent (LLM call #3) | |
| api agent (LLM call #4) | |
| risk agent (LLM call #5) | |
| demo agent (LLM call #6) | demo agent (LLM call #2, 独立, HTML 格式) |

**5 文档 = 1 个共享 session**: `meeting:{mid}:batch_docs`, 跨轮询 LLM 记得 5 文档历史 → 增量 patch 时不需要每次读全 5 文件

**输出格式**: Markdown 分隔符 (不用 JSON — LLM 容错好), agent 通过 file toolset 调 5 次 write_file

**软约束**: LLM 自觉判断哪些文档要 patch, 没改的也 write_file (保证 mtime 更新触发 SSE)

**失败隔离**: 1 个文件写失败, 其他 4 个仍要 write_file. 部分成功也算 `triggered=True`

### 2. demo 保持独立 agent

**理由**: demo 输出 HTML (跟 markdown 文档格式差异大), prompt 分离更清晰; demo 经常晚于 5 文档, 独立调度更灵活; LLM 输出 HTML 跟 markdown 混合容易出错.

### 3. 调度改 6 kinds → 2 kinds

**老** `run_one_round`: 每会议 × 6 kinds = 6 tasks
**新** `run_one_round`: 每会议 × 2 kinds (batch_docs + demo) = 2 tasks

```python
# 老
tasks = [(mid, k) for mid in meetings for k in DOC_KINDS]  # 6 kinds

# 新
SCHEDULED_KINDS = [BATCH_DOCS_KIND, DEMO_KIND]  # 2 kinds
tasks = [(mid, k) for mid in meetings for k in SCHEDULED_KINDS]
```

### 4. 老 kinds 兼容 stub

`req` / `arch` / `tasks` / `api` / `risk` 5 个 doc_kind 仍保留在 `DOC_KINDS` 常量 (向后兼容外部 import),但 `_dispatch_kind` 路由返 `deprecated: True` 警告 + 引导用 `batch_docs`.

```python
def _dispatch_kind(mid, kind, dry_run=False):
    if kind == "batch_docs":
        return trigger_batch_docs(mid, dry_run=dry_run)
    if kind == "demo":
        return trigger_sub_session(mid, "demo", dry_run=dry_run)
    return {"deprecated": True, "error": f"kind '{kind}' deprecated, use batch_docs"}
```

### 5. Prompt 模板精简

**老**: `prompts/req.md` (42 行) + `arch.md` (40) + `tasks.md` (42) + `api.md` (39) + `risk.md` (44) = 207 行
**新**: `prompts/batch_docs.md` (180 行, 含 ADR-0028 collab 协议)

每文档精简到 300-600 字 (YAGNI), bullet points 优先, 不写大段背景说明.

### 6. 协作提问协议 (ADR-0028)

新 batch_docs.md + 改 demo.md 加 collab 协议段:
- 跑前 `list_pending(mid)` 看未答问题
- 跑中 `ask_question(mid, section, q)` 推不确定
- 跑后 写入 collab.md 的 `Answered Questions` 段

`section` 命名: `docs` (通用) / `req` / `arch` / `tasks` / `api` / `risk` / `demo` (特定)

## 不做的

- ❌ 把 demo 也合进 batch_docs (HTML 跟 markdown 格式差异大)
- ❌ 一次性输出 6 文档 (含 demo) — demo 单独调度更灵活
- ❌ 完全删老 kind 字符串 (DOC_KINDS 保留兼容老 import)
- ❌ 改 AIAgent session_id 格式 (保持 `meeting:{mid}:{kind}` 兼容老 cache)
- ❌ 一期接 LLM function calling 协议 (沿用 ADR-0025 terminal 调风格, KISS)
- ❌ 多模态 vision 喂图 (chat 上传图片暂不喂 LLM, 后续)

## 性能预期

| 维度 | 老 | 新 | 提升 |
|---|---|---|---|
| LLM 调用 / 轮 | 6 | 2 | 3x ↓ |
| 总耗时 / 轮 | 3-5 min | 1-2 min | 2-3x ↓ |
| 一致性 (req↔arch↔tasks) | 差 | **好** | 显著 |
| 调试难度 | 中 | 低 | 1 个 prompt |
| 单点失败影响 | 1 文档 | 1-5 文档 (best-effort) | 隔离更好 |

## 实施步骤 (Commit 3)

1. 新 `prompts/batch_docs.md` (180 行, 含 ADR-0028 collab 协议)
2. 改 `prompts/demo.md` 加 ADR-0028 协议段
3. 删 `prompts/{req,arch,tasks,api,risk}.md` (5 老 prompt)
4. 新 `src/vpbuddy/sub_sessions/batch_docs.py` (~250 行)
   - `trigger_batch_docs(mid, dry_run, timeout)`
   - `render_batch_prompt(mid, state_summary, last_docs)`
   - `get_batch_doc_paths(mid)`
   - 失败隔离 + SSE 推
5. 改 `sub_session_controller` 调度:
   - 新常量 `BATCH_DOCS_KIND` / `DEMO_KIND` / `SCHEDULED_KINDS`
   - 新 `_dispatch_kind(mid, kind)` 路由函数
   - `run_one_round` 改用 `SCHEDULED_KINDS`
6. 测试 `src/tests/test_batch_docs.py` (19 个)
   - prompt 渲染 (路径 + 上次内容 + state)
   - dry_run / DIRECT mode / 无 AIAgent 路径
   - 路由 (batch_docs / demo / deprecated)
   - run_one_round 调度 (2 kinds)
7. design v1.30 同步 (顶部状态号 + ADR 索引 + 关键变更段)
8. pyproject version 0.6.x → **0.7.0** (架构级调整, minor bump)
9. README v0.7 节

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 1 次 LLM 输出 5 文档 token 多 (~3-4k output) 慢 | 实测 ~90s (现代模型 OK), 比 6×30s=180s 还快 |
| 第 5 个文档被 LLM "草草收尾" | prompt 强约束 "5 个文档同等重要, 都要写完整" |
| 增量更新误改无关文档 | soft 约束 + 软提示 "无变化也 write_file 旧内容" |
| 1 个崩 5 文档全没 (单点) | best-effort: 部分成功也算 triggered, 客户端 partial 显示 |
| 老 kinds (req/arch) 引用方未迁移 | 保留 DOC_KINDS 常量, _dispatch_kind 返 deprecated 警告 |

## 关联

- ADR-0006 — 子 session 架构基础
- ADR-0009 — 老 6 sub-session (部分 superseded)
- ADR-0025 — agent 调 Python 工具 (terminal 风格, 沿用)
- ADR-0028 — collab.md 协作协议 (本 commit 依赖)
- `src/vpbuddy/sub_sessions/batch_docs.py` (新模块)
- `src/vpbuddy/prompts/batch_docs.md` (新 prompt)
- `src/vpbuddy/prompts/demo.md` (改)
- `src/vpbuddy/sub_session_controller.py` (改调度)
- `src/tests/test_batch_docs.py` (新测试)