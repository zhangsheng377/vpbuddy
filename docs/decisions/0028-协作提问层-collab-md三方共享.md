# 0028. 协作提问层 — collab.md 三方共享协议

- **状态**: 已接受
- **日期**: 2026-07-01
- **作者**: 张胜东 (起草: Hermes)
- **依赖**: [ADR-0006](../decisions/0006-MVP-Step3-子session架构.md) (子 session 架构) · [ADR-0025](../decisions/0025-agent-网络搜索-KB检索.md) (agent 通过 terminal 调 Python 工具)
- **后续**: ADR-0029 (6 sub-session 合并为 2 batch agent — 依赖本协议)

## 背景

2026-07-01 张胜东提议: 现有架构里 6 个 sub-session agent 各自"闷头干",跟用户对话脱节;想加一个**协作提问层**,让主对话 + sub-session agents 三方共享"提问文档",agent 不确定的关键事实主动问用户,用户回答后 agent 增量 patch 自己负责的文档。

**当前状态**:
- 6 doc agent (req/arch/tasks/api/risk/demo) 各自独立 prompt,各自 LLM 调用
- chat 主对话 agent 用 AIAgent,跟 6 doc agent 不通气
- 用户在 chat 问 "API 用 REST 还是 GraphQL" → 只有 chat agent 答,req/arch/tasks/api agent **不知道**
- 6 doc agent 写文档时遇到不确定事实 (e.g. 客户预算) → 默认编造,后续用户纠正成本高

## 决策

### 1. 新文件 `docs/{meeting_id}/collab.md` — 协作提问文档

**格式**: Markdown + append-only + 简单状态机 (pending / answered)

```markdown
# Collab Doc — {meeting_id}
Generated: {ts}

## ❓ Pending Questions (未答)

### Q{qid} [{section}] {question}
- Asked by: {agent_role} at {ts}

## ✅ Answered Questions (已答)

### Q{qid} [{section}] {question}
- Asked by: {agent_role} at {ts}
- Answered by: VP at {ts}: {answer}
```

**section 命名**: `req` / `arch` / `tasks` / `api` / `risk` / `docs` / `demo` — 谁问的归谁 section,后续回答按 section 过滤。

### 2. 新模块 `src/vpbuddy/collab.py` — 5 个公开 API

```python
read_collab(mid) -> str                          # 读全文
parse_questions(mid) -> list[dict]              # 解析所有 Q
list_pending(mid, section=None) -> list[dict]    # 未答 (可选 section 过滤)
ask_question(mid, section, question, asker) -> dict   # 追加 pending (节流)
answer_question(mid, qid, answer, answerer) -> dict   # pending → answered 移动
```

**节流**: 同 (mid, section, 相似问题) 一次会议只 1 次。throttle key = `[section] + question前30字符小写`。

**线程安全**: 进程内 `threading.Lock` + 跨进程 `fcntl.flock` (POSIX),避免 batch_docs / demo / chat 三个 agent 并发写冲突。

### 3. 三个 agent 的协作协议 (写在各自 prompt 里)

**主对话 agent (chat)**:
- 用户问时,先 `read_collab` 看有没有相关已答问题可引用
- 主动 `ask_question` 把自己不确定的细节推到 collab (section 写 `docs`)

**batch_docs agent (5 文档)**:
- 跑任务前:`list_pending(section="docs")` 看有没有新回答
- 写文档时发现不确定 → `ask_question(section="<对应>", ...)`
- 已答问题下次跑时看到,增量 patch 自己文档

**demo agent**:
- 同上,section 用 `"demo"`
- 配色/交互/布局疑问写到 collab

### 4. 用户回答路径

**选项 A**: 用户在 chat 面板正常打字 → chat agent 通过 proactive trigger 看到 → 转 answer_question
**选项 B**: UI 加 [回答疑问] 按钮,直接 POST `/api/meetings/{mid}/answer_question?qid=X&answer=Y`
**选项 C**: 用户答在普通 chat 历史, agent 自动判断是否是回答 (正则匹配 `qid`)

**实施**: 选项 B (UI 端点), 选项 A 兼容 (chat 历史带 `[qid:xxx]` 标记可被识别)。

### 5. 服务端 API

- `GET /api/meetings/{mid}/collab` → `{collab, pending, answered}`
- `POST /api/meetings/{mid}/answer_question?qid=X&answer=Y` → `{ok, qid, status}`
- `POST /api/meetings/{mid}/ask_question?section=X&question=Y` (可选,给前端直接用)

### 6. SSE 事件

- 新事件类型 `collab-update`,推 `{qid, status, section}` 让前端实时刷新 collab 面板
- 复用现有 `realtime_server.push_event` 通道

## 不做的

- ❌ collab agent (单独的 agent 协调) — 3 个 agent 各自管自己 section,无中间层
- ❌ 复杂权限系统 — 任何 agent 都能读写全部 section
- ❌ 跨会议共享 collab — collab 绑死 meeting_id
- ❌ 全文搜索 / 模糊匹配 — 用 section + 前 30 字符节流,够用

## 实施步骤

1. `src/vpbuddy/collab.py` 新模块 + 单元测试 (`test_collab.py`) ✅ Commit 1
2. `ui_server` 加 3 个端点 (GET collab / POST answer_question / POST ask_question) + 测试 ← Commit 2
3. `prompts/batch_docs.md` 新 prompt + `prompts/demo.md` 改 (加 collab 协议段) ← Commit 3 (合并 6 sub-session)
4. `sub_sessions/batch_docs.py` 新模块 + `sub_sessions/demo.py` 迁出 ← Commit 3
5. `sub_session_controller` 调度改 (6 kinds → 2 kinds) ← Commit 3
6. 删老 5 prompt (`req.md` / `arch.md` / `tasks.md` / `api.md` / `risk.md`) ← Commit 3
7. UI chat 面板加 [回答疑问] 按钮 + collab 折叠面板 ← Commit 4
8. design v1.29 同步 ← Commit 5
9. pyproject version 0.6.x → 0.7.0 ← Commit 5 (架构级, minor bump)
10. README v0.7 节 ← Commit 5

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| agent 写 collab 太频繁刷屏 | 节流 (前 30 字符 + section) + soft 限制 (prompt 引导"非关键事实不写") |
| 用户被疑问淹没 | 默认 Pending 折叠, 只显示数量; 用户主动展开 |
| 跨 agent 写冲突 | `threading.Lock` + `fcntl.flock` 双锁 |
| 文本解析脆弱 (Markdown → dict) | 协议简单 (3 行 / 块), 单测覆盖 25+ case |

## 关联

- ADR-0006 — 子 session 架构
- ADR-0025 — agent 调 Python 工具 (collab.py 通过 terminal 调)
- ADR-0029 — 6 sub-session 合并为 2 batch agent (下一步, 依赖本协议)
- `src/vpbuddy/collab.py` (新模块)
- `src/tests/test_collab.py` (新测试)
- `src/vpbuddy/ui_server.py` (改)
- `src/vpbuddy/sub_session_controller.py` (改)
- `src/vpbuddy/prompts/batch_docs.md` (新 prompt)
- `src/vpbuddy/prompts/demo.md` (改 prompt)