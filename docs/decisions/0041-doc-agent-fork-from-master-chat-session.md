# ADR-0041 — 子 agent fork 自主 chat session (继承上下文)

**状态**: **已接受** (2026-07-04)
**日期**: 2026-07-04
**作者**: Hermes (起草) / 张胜东 (决策)
**替代**: 无 (前向补充 ADR-0029, 不取代)
**依赖**: ADR-0006 (子 session 架构) / ADR-0029 (6 sub-session 合并为 2 batch) / ADR-0040 (LLM env 透传)

## Context

截至 ADR-0040, vpbuddy 的 session 架构是:

```
chat 页签 → session="meeting:{mid}:vp-chat"        ← 独立的 AIAgent 实例
batch_docs → session="meeting:{mid}:batch_docs"     ← 独立的 AIAgent 实例
demo       → session="meeting:{mid}:demo"           ← 独立的 AIAgent 实例
```

3 个 session **彼此完全隔离**, chat 里讨论的所有内容 (需求变更、风险确认、方向调整) batch_docs 和 demo 毫不知情。

张胜东指出:
> "6 个文档应该对应一个子 agent, 它和 chat 页签对应的主会话是同一个 session_id, 这样共享历史。"
>
> "chat 里聊的也是与会议有关的内容, 所以上下文污染不是问题。"

建议架构:

```
master session: meeting:{mid}:vp-chat     ← 客户端 chat 页签
   ├── fork → meeting:{mid}:batch         ← 继承 master 历史, 生成 5 文档
   └── fork → meeting:{mid}:demo          ← 继承 master 历史, 生成 demo
```

## Decision

### 1. Hermes `parent_session_id` 机制

Hermes AIAgent 支持 `parent_session_id` 参数 (0.18.0+):

```python
# 构造子 agent 时指定父 session
agent = AIAgent(
    session_id="meeting:{mid}:batch",
    parent_session_id="meeting:{mid}:vp-chat",  # ← fork 关键
    ...
)
```

行为:
1. 子 agent 启动时, 从 parent session 读取**整个对话历史**作为初始化上下文
2. 子 agent 自己的 chat 写入 `session_id` 路径, **不修改 parent 的历史**
3. parent 继续 chat 时, 感知不到子 agent 的存在
4. 效果 = **fork**: 子 agent 看到 parent 说过的所有话, 但双方后续互不影响

### 2. session_id 号段统一

| 角色 | session_id | 创建方 |
|------|-----------|--------|
| 主 chat | `meeting:{mid}:vp-chat` | `ui_server._get_chat_agent()` |
| batch_docs | `meeting:{mid}:batch` | `sub_session_controller._get_or_create_agent()` |
| demo | `meeting:{mid}:demo` | `sub_session_controller._get_or_create_agent()` |

`_master_session_id()` 和 `_agent_session_id()` 集中管理号段, 确保 `parent_session_id` 引用一致。

### 3. LLM provider 统一

Chat agent 原用 `VPBUDDY_LLM_API_BASE` (默认 ollama), doc agent 原用 `OPENAI_BASE_URL` (MiniMax)。fork 要求 parent 和 child 走同一个 provider, 否则上下文可能因 tokenizer/model 不一致出问题。

统一: chat agent 也走 `OPENAI_BASE_URL` + `OPENAI_API_KEY` (MiniMax), 保持跟 doc agent 一致。

### 4. prompt 差异化保留

虽然共享上下文, 但 system prompt 仍不同:
- **Chat agent**: 对话风格, 建议/追问交付物
- **batch_docs**: 结构化写作, write_file 硬性要求
- **demo**: HTML 演示脚本生成

这没问题 — `parent_session_id` 只继承**对话历史** (user / assistant messages), 不覆盖 child 的 system prompt。

## Consequences

### 正面

- **上下文共享**: chat 里 VP 说 "重点考虑安全性", batch_docs 生成时自动把安全性纳入需求/架构/风险文档
- **减少 VP 重复输入**: 不需要在每次触发 doc 生成前都重申一遍会议重点
- **架构简单清晰**: 自然的 parent-child fork 模型, 符合 Hermes 设计意图
- **向后兼容**: 老的 standalone session 号段仍可用, 新会议自动走 fork

### 负面

- **上下文膨胀风险**: chat 聊了很多轮后 fork, 子 agent 看到全部历史 → 可能超 token 限制
  - 缓解: Hermes 内部有 context window management, 超长会自动 truncate
  - 后续: 可加 `max_parent_history_tokens` 限制继承长度 (如最后 20 轮)
- **Chat agent 改 LLM provider**: 如果用户本地配了 ollama, 现在 chat 也走 MiniMax, 可能需要用户确认
  - 缓解: 优先级 `OPENAI_BASE_URL > VPBUDDY_LLM_API_BASE > localhost:11434`, 保留回退

### 后续

- 考虑 `max_fork_history_tokens` / `max_fork_turns` 控制继承上限
- 如果 chat 和 doc 需要不同模型, 可考虑 chat 用 qwen/glm, doc 用 MiniMax — 但 fork 上下文可能不兼容
- `cleanup_inactive_agents` 应同时清理 parent session 的缓存的 chat agent

## 验证

```python
# 在 GPU 上验证 fork
python -c "
import os
from run_agent import AIAgent

mastersid = 'meeting:TEST_FORK_MID:vp-chat'
childsid  = 'meeting:TEST_FORK_MID:batch'

# 1. 先在 master 里 chat
master = AIAgent(session_id=mastersid, ...)
master.chat('这个会议重点关注安全性')

# 2. fork 子 agent (应看到 master 的历史)
child = AIAgent(session_id=childsid, parent_session_id=mastersid, ...)
r = child.chat('根据刚才的讨论, 生成需求文档')
# 应自动把 "重点关注安全性" 纳入需求
"
```

## 参考

- Hermes AIAgent `parent_session_id` 参数: `run_agent.py::AIAgent.__init__` 签名包含 `parent_session_id: str = None`
- ADR-0029 (6 sub-session 合并为 2 batch): `docs/decisions/0029-6sub-session合并为2batch-agent.md`
- ADR-0040 (LLM env 透传): `docs/decisions/0040-sub-session-透传-LLM-env-避免-openrouter-401.md`
- 张胜东 2026-07-04 纠正: "6 个文档应该对应一个子 agent, 它和 chat 页签对应的主会话是同一个 session_id"