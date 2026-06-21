# ADR-0006: MVP Step 3 — 子 session 常驻循环架构

- **状态**: Accepted
- **日期**: 2026-06-21
- **作者**: 张胜东(起草: Hermes,基于 2026-06-21 晚架构讨论)
- **关联**: [ADR-0001 MVP 选型](./0001-MVP-选型.md) · [ADR-0004 Step 2 ASR](./0004-MVP-Step2-ASR设计.md) · [架构 v1.17](../design/总体架构.md)

---

## 背景

VPBuddy 架构 v1.16(2026-06-20)设计了"5 大交付物后台并行生成"——但**没具体设计怎么实现**。2026-06-21 晚讨论发现:

1. **V 视角**:5 个文档(需求/架构/任务/API/风险)+ 1 类可演示 demo
2. **架构师视角**:6 个东西 = 6 个独立子 session,各自持续维护
3. **避免重造轮子**:VPBuddy 跑在 Hermes Agent 上,**直接复用** Hermes 的 session/tool/memory,**不自己**重做一套

**关键设计洞察(2026-06-21 晚张胜东指出)**:
- 累积暴露给 LLM ≠ 主动调 tool。**UI 展示什么,prompt 就有什么**。
- "AI 主动拉"是伪命题。**V 看得到,AI 看得到**。
- Session 持久化 = Hermes `session_search` + 同 `session_id`。
- 子 session 直接写文件,不需要 JSON 中转。

---

## 决策

### 1. 6 个常驻子 session(各维护一种文档)

```
session_id 命名:meeting:{mid}:{doc_kind}

doc_kind ∈ {
    "req",   # 需求文档
    "arch",  # 架构文档
    "tasks", # 任务列表
    "api",   # API 文档
    "risk",  # 风险评估
    "demo",  # 演示 demo(可运行代码/HTML/mermaid)
}
```

每个子 session:
- 独立 `session_id` → Hermes 自动保留历史
- 独立 prompt 模板 → 各自职责清晰
- 独立 doc 文件 → `/home/zsd/vpbuddy/docs/{mid}/{doc_kind}.md`
- 后台循环:每 N 秒检查累积 → 判断 → 决定写不写

### 2. 共享累积 = 一个 JSON 文件(已存在的 MeetingState)

```
/home/zsd/vpbuddy/data/meetings/{mid}.json
```

**写方**:
- ASR 后台(独立进程):从转写+分离中提取 REQ/RISK/QUE → 调 `storage.save()`

**读方**:
- 6 个子 session:循环里 `storage.load(mid)` 读最新累积
- V 主 session:每次对话通过 UI / 工具展示
- UI:Web 端从 NFS 读(实时)

**不需额外数据库**——Step 1 的 `storage.py` 已实现 JSON 持久化。

### 3. 复用 Hermes 能力(不重造)

| 能力 | VPBuddy 怎么用 Hermes |
|---|---|
| 工具调用 | 子 session 直接用 `read_file` / `write_file` / `patch` / `search_files` 等内置 tool |
| 历史上下文 | `session_search(session_id=...)` 读同 session 历史 |
| 后台调度 | `hermes cron` + `delegate_task` 触发子 session |
| 跨 session 共享 | 共用 `MeetingState JSON` 文件(文件系统即共享) |
| 知识库搜索 | 统一 `sqlite-vec` 存所有内容 embedding,跨会议 RAG |

**关键不做的**:
- ❌ 自己设计 VPBuddy 专用 tool(用 Hermes 通用 tool 即可)
- ❌ 自己实现 session 持久化(用 Hermes session_search)
- ❌ 自己实现 cron 调度(用 hermes-scheduler)
- ❌ 自己设计知识库"双模式"(统一搜索)

### 4. 子 session 怎么"常驻"循环

**实现方式**:`sub_session_controller.py` 脚本 + `hermes cron`

```python
# src/vpbuddy/sub_session_controller.py
# 每 30 秒一轮,6 个子 session 串行触发
import time
import subprocess
from pathlib import Path
from vpbuddy.storage import MeetingStorage

def main_loop():
    """主循环:每 30s 检查活跃会议,触发 6 个子 session"""
    while True:
        for meeting_id in list_active_meetings():
            state = MeetingStorage().load(meeting_id)
            for doc_kind in ["req", "arch", "tasks", "api", "risk", "demo"]:
                trigger_sub_session(meeting_id, doc_kind, state)
        time.sleep(30)

def trigger_sub_session(meeting_id, doc_kind, state):
    """触发 1 个子 session(用 hermes chat)"""
    session_id = f"meeting:{meeting_id}:{doc_kind}"
    prompt = render_prompt(doc_kind, state, meeting_id)
    subprocess.run([
        "hermes", "chat",
        "-q", prompt,
        "-Q",  # quiet:只输出最终回复
        # 不传 --resume —— LLM 每次拿全 context 即可,不用维护 session_id 映射
    ])
```

**为什么不传 `--resume`**(2026-06-21 晚验证后修正):
- Hermes 的 session_id 是自动生成的 hash,无法按"自定义名字"创建
- 我们的需求是:**全 context 都在 prompt 里**(meeting_state JSON + 上次 doc 输出)
- LLM 不需要 chat history,只需要"最新状态 + 你之前的输出"
- 每次 fresh chat → 不维护 session 映射 → YAGNI 友好
- 代价:每次传同样数据(token 略浪费) — 可接受

**为什么用 cron 触发,不直接常驻 Python 进程**:
- 复用 hermes-scheduler 的可靠性(失败重试/日志/通知)
- 不需自己写守护进程
- 一次一调,失败影响范围小

### 5. 子 session 的 prompt 设计模式(2026-06-21 实战验证)

**通用模板**(每个 doc_kind 实例化):

```
你是 VPBuddy 的【{doc_kind} 子 session】。

【职责】
你负责持续维护本次会议的 {doc_kind} 文档。
基于【最新累积 + 你上次的输出】,判断是否需要更新。

【当前累积】
{state_summary}

【你之前的输出】
{last_doc}

【判断】
1. 累积有 REQ/RISK/QUE 变化?→ 更新
2. V 显式说"更新 {doc_kind}"?→ 立即更新
3. 否则?→ 输出"无变化",退出

【如何写】
(Hermes 会告诉你可用工具,自己选合适的)

【YAGNI 原则】
- 不主动加"可能需要"的章节
- 不写 VP 没要求的内容
- 跑起来再说,有问题再调
```

**关键不做的**(张胜东 2026-06-21 晚纠正):
- ❌ 在 prompt 里**指定具体工具名**(`read_file` / `write_file` / `patch`)
  - 原因:Hermes 已经在 context 里告诉 LLM 可用工具,画蛇添足
  - 替代:说"Hermes 会告诉你可用工具,自己选"
- ❌ 输出 JSON 让别的进程写
  - 原因:LLM 自己能写文件,不需要中介
- ❌ 维护 session_id 映射(用 --resume)
  - 原因:Hermes session_id 不可预测,全 context 传过去就够了

**6 个 doc_kind 的特化**(完整版见 `src/vpbuddy/prompts/{req,arch,tasks,api,risk,demo}.md`):
- **req**:Markdown 需求清单(编号 + 优先级 + 状态 + 来源原话)
- **arch**:mermaid graph TD + 模块/接口/数据流 + 关键决策
- **tasks**:任务卡片(负责人/工期/依赖/验收)
- **api**:OpenAPI 3.0 YAML
- **risk**:风险矩阵(严重度/概率/影响/缓解/Owner)
- **demo**:**可运行的 HTML/代码/mermaid** ← 唯一非纯文本文档

### 6. 演示 demo(第 6 类)特殊说明

**demo session 的输出**不是 markdown 文档,是**可运行/可展示的产物**:
- HTML 互动原型
- Python 代码片段
- mermaid 流程图
- shell 命令演示

**输出位置**:`/home/zsd/vpbuddy/docs/{mid}/demo/`
- `demo.html`(首选:浏览器打开)
- `demo.py`(可选:命令行)
- `demo.mmd`(可选:流程图)

**判定"可演示"**:
- 能在浏览器/终端跑起来
- 反映会议讨论的关键场景
- V 能直接拿给同事/客户展示

### 7. 知识库 = 统一 sqlite-vec(不分"双模式")

```sql
CREATE TABLE knowledge (
    id TEXT PRIMARY KEY,        -- "meeting:abc:REQ-001" 或 "external:doc123"
    type TEXT,                  -- 'req' / 'risk' / 'meeting_summary' / 'doc' / 'external'
    content TEXT,               -- 原文
    embedding BLOB,             -- 384 维向量(用 bge-small-zh)
    meeting_id TEXT,            -- 来源会议(外部知识为 NULL)
    created_at TEXT
);
CREATE INDEX idx_meeting ON knowledge(meeting_id);
CREATE INDEX idx_type ON knowledge(type);
```

**RAG 检索**:
```python
def search_knowledge(query: str, k: int = 5, type_filter: str = None) -> list:
    """统一搜索:不区分模式,按 query 找最相关的 k 条"""
    q_emb = embed(query)
    # sqlite-vec 的 vector_top_k
    return db.execute(
        "SELECT * FROM knowledge ORDER BY vector_distance(embedding, ?) LIMIT ?",
        [q_emb, k]
    )
```

**MVP 不做的**:
- 多模态(图/音/视频)
- 联邦检索(企业知识库+个人+会议三路)
- 自动摘要/聚类

---

## 后果

### 正面
- ✅ **零造轮子**:工具/历史/调度全用 Hermes
- ✅ **6 个并行维护**:每个文档持续演化,V 随时看到最新版
- ✅ **失败隔离**:1 个子 session 崩了不影响其他
- ✅ **可调试**:每个子 session 的 session_id 在 `~/.hermes/cron/output/` 有日志
- ✅ **重启友好**:session_id 不变 = 历史保留 = 不丢上下文
- ✅ **MVP 友好**:先做 1 个 demo session 跑通,再扩 5 个

### 负面 / 取舍
- ⚠️ 6 个 cron 任务,资源占用略高
- ⚠️ 每个子 session 调 LLM,API 成本 ×6(需要控制触发频率)
- ⚠️ 子 session prompt 设计错了 → 6 个全错(模板化降低风险)
- ⚠️ sqlite-vec 部署需要新依赖
- ⚠️ V 离线时,子 session 不知道(可能空转,需加活跃会议检测)

### 风险 & 缓解
- **风险**:cron 30s 太频繁 → LLM 成本高
  - **缓解**:自适应频率(累积变化小时拉长到 5min,变化大时缩短到 10s)
- **风险**:子 session prompt 不够具体 → 输出垃圾
  - **缓解**:每个 doc_kind 有 1-2 个具体示例(用户给的"理想输出"作为 few-shot)
- **风险**:文件并发写冲突(6 个 session 写不同文件,理论无冲突,但有 .lock 文件兜底)
  - **缓解**:`/home/zsd/vpbuddy/docs/{mid}/.lock` 文件锁
- **风险**:sqlite-vec 装不上
  - **缓解**:fallback 到 `numpy` 暴力 cosine 相似度(MVP 候选 < 1000 时够用)

---

## 实施计划

### Phase 1:骨架(2026-06-21 当晚)
- [x] 写 `sub_session_controller.py` 骨架(主循环)
- [x] 写 6 个 doc_kind 的 prompt 模板(各自独立)
- [x] 写 `test_sub_session.py`(19 个测试)全过
- [x] 跑通 1 个 demo session 端到端:18.5KB / 398 行 HTML demo 生成,正确处理开放问题"SSO 走哪个 IdP?"
- [x] 修正架构:不传 `--resume`,全 context 传过去(LLM 每次 fresh chat)

### Phase 2:扩展(后续)
- [ ] 复制 demo 模式 → 验证 req/arch/tasks/api/risk 5 个
- [ ] 知识库 sqlite-vec 集成
- [ ] hermes cron 注册(1 个 cron job:每 30s 跑 controller)
- [ ] 真实会议端到端:ASR 持续累积 → 6 个 doc 持续更新

### Phase 3:产品化
- [ ] UI 集成(展示 6 个文档实时状态)
- [ ] V 主 session ↔ 6 个子 session 通信(标记 V 显式指示)
- [ ] 跨会议 RAG

### Phase 1 实战记录(2026-06-21 04:04 完成)

**端到端 demo(meeting DEMO01)**:
- 输入:3 REQ + 1 RISK + 1 QUE(SSO 走哪个 IdP)
- 输出:`/tmp/vpbuddy_docs/DEMO01/demo/demo.html`,18.5KB / 398 行
- LLM 实际做到:
  - ✅ 列出 3 个场景(SSO/微信扫码/Excel 导出),每个都标 REQ ID
  - ✅ 5 个 IdP 选项(Okta/Azure AD/Auth0/钉钉/企微)**可点选**(飞书 ADR-0008 后从 IdP 候选删除)
  - ✅ 5 步 SSO 流程可视化
  - ✅ 开放问题"SSO 走哪个 IdP?"在 demo 顶部显眼位置显示
  - ✅ 风险"OAuth 限流"在 SSO 步骤旁标注
  - ✅ 单文件无依赖(直接浏览器打开)

**测试结果**:`pytest tests/test_sub_session.py -v` → **19 passed**

---

## 变更

- 2026-06-21: 起草,基于 2026-06-21 晚架构讨论(张胜东纠正过度设计)
