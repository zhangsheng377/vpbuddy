# ADR-0055: Hermes `parent_session_id` fork 不生效 — 手动注入 chat history

**状态**: ✅ 已落地 (2026-07-13)

---

## 发现

ADR-0041 声称子 agent (batch_docs/demo) 通过 `parent_session_id` 继承主 chat session 的对话历史：

```python
AIAgent(
    session_id="meeting:{mid}:batch_docs",
    parent_session_id="meeting:{mid}:vp-chat",  # ← ADR-0041 认为这会让子 agent 看到 chat 历史
)
```

**实测不成立。** 阅读 Hermes 源码确认：

| 代码位置 | 实际行为 |
|----------|----------|
| `agent_init.py:L300` | `ephemeral_system_prompt` 注释明确："NOT saved to trajectories" |
| `agent_init.py:L1121` | `_parent_session_id` 仅赋给实例属性 |
| `run_agent.py:L531-539` | `_ensure_db_session()` 写入 SQLite `sessions.parent_session_id` 列 |
| `turn_context.py:L259` | 对话循环 `messages = list(conversation_history) if conversation_history else []` — 传入参数为 `None` |
| `conversation_loop.py` | **全文无** `parent_session_id` 引用 |
| `turn_context.py` | **全文无** `parent_session_id` 引用 |

`parent_session_id` 的唯一作用是 DB 血缘标记（`sessions` 表里记一笔 "这个 session 的父是哪个"），对​话循环全程不读这个字段。子 agent 启动时消息列表从 `[]` 开始。

---

## 修复

在 VPBuddy 层面手动注入 chat history 到 `format_state_summary()`：

```python
# sub_session_controller.py format_state_summary()
_chat_json = DATA_DIR / f"{state.meeting_id}.chat.json"
if _chat_json.exists():
    _history = json.loads(_chat_json.read_text())
    _recent = _history[-20:]  # 最近 20 条
    parts.append("## VP Chat 对话历史 (最近 20 条)")
    for _m in _recent:
        # 格式: 👤 VP: xxx / 🤖 Agent: xxx
        # 超长内容 (>2000 字) 截断
        # 显示上传文件附件名
```

这样 chat 中讨论的需求变更、风险确认、方向调整，以及上传的文件路径，都能被 batch_docs 和 demo 子 agent 看到。

---

## 影响范围

| 文件 | 改动 |
|------|------|
| `src/vpbuddy/sub_session_controller.py` | `format_state_summary()` 末尾加载 `{mid}.chat.json` 并格式化 |

客户端无需更新。
