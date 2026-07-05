# ADR-0043: 经验蒸馏 Phase 1

- **状态**: 已采纳
- **日期**: 2026-07-05
- **作者**: AI Agent (Hermes)
- **替代**: 无
- **依赖**: ADR-0019 (RAG 选型 Chroma 嵌入式), ADR-0020 (KB 隔离 by meeting_id)

## Context

v0.8 用户反馈: "用了这么多次 VPBuddy, 每次新会议它都像第一次跟我开会一样, 完全不记得之前的讨论。"

需求: **"越用越聪明"** — 每次会议沉淀的知识和经验应该能被后续会议参考。

### 约束

1. **不要 LLM 摘要**: 用户不相信 LLM 能准确总结会议(已知问题: 幻觉、简化过度)。使用结构化 MeetingState 的数据直接提取。
2. **不要自动注入**: Phase 1 只做"提取+存储+检索", 不自动写入 agent prompt。自动注入是 Phase 2 的事。
3. **用户确认**: 只有用户手动 approve 的经验才进入聚合索引, 避免噪声。
4. **轻量**: 不引入新数据库, 使用 JSON 文件持久化。

## Decision

引入经验蒸馏 (Experience Distillation) Phase 1: 会议结束后从 MeetingState 自动提取经验候选, 存储为 JSON 文件, 支持检索和手动确认。

### 数据模型

`ExperienceItem` 包含 6 种 kind:

| Kind | 描述 | 提取来源 |
|------|------|----------|
| `domain_fact` | 领域事实 | state.requirements |
| `product_pattern` | 产品模式 | (Phase 2, 需要更多数据) |
| `decision_rule` | 决策规则 | state.goals |
| `terminology` | 术语 | (Phase 2, 从 KB 提取) |
| `failure_lesson` | 失败教训 | state.risks |
| `user_preference` | 用户偏好 | (Phase 2, 从 chat 历史) |

### 存储

```
data/experiences/
  {meeting_id}.json   # 每会一文件, 含候选列表
  _all.json           # 聚合索引, 仅含 approved=True 的条目
```

### 提取时机

`POST /api/meetings/{id}/close` 中自动调用:
```python
from ..experience_store import extract_from_meeting_state, save_experiences

state = storage.load(meeting_id)
items = extract_from_meeting_state(meeting_id, state, meeting_title=meeting_id)
if items:
    save_experiences(meeting_id, items)
```

### 检索

- `get_approved_experiences()`: 从 `_all.json` 读取已确认条目
- `search_experiences(domain, product_type)`: 按领域/产品类型过滤
- `format_experiences_for_prompt()`: 格式化为 prompt 注入文本 (Phase 2 用)

### 与 Chroma RAG 的关系

- Chroma RAG (ADR-0019) 处理**文档级检索**: 上传的文档、会议纪要全文
- Experience Store 处理**经验级检索**: 结构化的、已提炼的可复用知识
- 两者互补, Chroma 不影响 Experience Store 的设计

## Consequences

### 正面

- **越用越聪明**: 长期使用后, 类似的会议经验可以被检索到。
- **低侵入**: JSON 文件, 不引入新依赖, 不改变现有数据流。
- **用户可控**: 只有 approved 条目才进聚合索引。
- **结构化提取**: 基于 MeetingState (非 LLM), 可预测、可测试。

### 负面

- **Phase 1 不接 Hermes skill 更新**: 经验不会自动倒灌到 agent prompt 中, 需要用户通过 VP Chat 手动查询。"越用越聪明"的感知有限。
- **启发式提取有限**: 当前提取规则不依赖 LLM, 质量受限于 MeetingState 的结构化程度。
- **领域猜测粗糙**: 基于关键词匹配的 `guess_domain_from_meeting`, 准确率 60-70%。

### Phase 2 规划

- Hermes skill 自动更新: 用户确认的经验 → 写入 skills 目录
- KB 上传文件的经验提取: 从上传文档中提取 terminology / product_pattern
- Chat 历史挖掘: 从用户和 agent 的对话中提取偏好
- LLM 辅助提取: 启发式规则无法覆盖的场景, 用 LLM 二次提取

### 存储变更

- 新增 `data/experiences/` 目录
- 新增 `ExperienceItem` 数据模型 (`src/vpbuddy/experience.py`)
- 新增 `experience_store.py` 模块
- 无数据库迁移
