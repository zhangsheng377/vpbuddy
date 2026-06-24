# 0011. VPBuddy 装成 Hermes Skill (5 doc + 1 demo agent 架构)

- **状态**: 已接受
- **日期**: 2026-06-23
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (新增)
- **依赖**: [ADR-0009](../0009-部署架构-Hermes-runtime.md) (Hermes runtime), [ADR-0013](../0013-流式E2E-端到端工作流.md) (in-process AIAgent)

## 背景

ADR-0009 写"VPBuddy = Hermes skill 集合", 但 2026-06-23 之前:

- `src/vpbuddy/__init__.py` 注释 "运行在 Hermes Agent 之上" 但 `import hermes_agent` 0 命中
- `~/.hermes/skills/vpbuddy/` 不存在
- controller.py 直接 `from run_agent import AIAgent`, **没通过 hermes skill system 注册**
- 用户在 hermes 问 "VPBuddy 怎么用" / "开会时" 触发不到 VPBuddy skill (没装)

张胜东 2026-06-23 在 GPU 服务器**完整安装好 hermes 0.16.0**, 现在需要把 VPBuddy 装成正式 skill.

## 决策

**把 VPBuddy 装成 `~/.hermes/skills/vpbuddy/` 下的 hermes skill**。

### 落地点

```
/home/zsd/.hermes/skills/vpbuddy/
├── SKILL.md                        # YAML frontmatter + 使用文档
└── references/
    ├── architecture-overview.md    # 数据流图 + 6 agent 生命周期
    └── sandboxed-demo-rules.md     # demo agent 输出约束
```

**SKILL.md frontmatter**:
```yaml
---
name: vpbuddy
description: "VPBuddy meeting intelligence — loopback audio capture, funasr streaming ASR, 5 doc sub-sessions (req/arch/tasks/api/risk) + 1 separate demo sub-session, KB RAG..."
version: 0.4.0
metadata:
  hermes:
    tags: [meeting, asr, doc-generation, rag, sqlite-vec, funasr]
    related_skills: [hermes-agent, autonomous-ai-agents, data-science]
---
```

**触发关键词** (在 description 里):
- "开会时" / "会议开始" / "新会议" / "loopback 采集" / "实时转写" / "5 个文档" / "做 demo" / "演示下"

**5 doc + 1 demo agent 架构** (张胜东 2026-06-23 纠正):
- 5 doc agent = req / arch / tasks / api / risk (写 .md)
- 1 demo agent = 单独拎出来 (写 demo/demo.html)
- 总共 6 个 long-lived AIAgent, 跨 chunk session_id 复用
- 同一会议内, 6 个 agent 都常驻 (跨 chunk 累计 LLM 上下文)
- 不同会议 = 6 个新 agent (上下文隔离)

### Hermes 调用方式

```python
# src/vpbuddy/sub_session_controller.py (ADR-0009 落地版)
from run_agent import AIAgent  # ⚠️ 不是 hermes_agent — 见 ADR-0009 §具体落地

_AGENT_CACHE: Dict[str, AIAgent] = {}

def _get_or_create_agent(meeting_id: str, doc_kind: str) -> AIAgent:
    sid = f"meeting:{meeting_id}:{doc_kind}"
    if sid not in _AGENT_CACHE:
        _AGENT_CACHE[sid] = AIAgent(
            session_id=sid,
            enabled_toolsets=["terminal", "file"],  # 6 agent 都一样
            platform="subagent",
            quiet_mode=True,
            max_iterations=30,
        )
    return _AGENT_CACHE[sid]
```

### 安装

新部署一台 GPU 服务器:
```bash
# 1. 装 hermes (PyPI)
pip install hermes-agent>=0.16.0,<1.0
# 2. 装 vpbuddy (PyPI 或本地 git)
pip install vpbuddy[audio,gpu]
# 3. 装 vpbuddy skill 到 hermes skills 目录 (自动 / 手动)
# 自动: pyproject.toml [project.scripts] 注册 post-install hook
# 手动: cp -r src/vpbuddy/skills/vpbuddy ~/.hermes/skills/
# 4. 验证
vpbuddy version
hermes skills list | grep vpbuddy
```

## 拒绝的方案

### 拒绝: VPBuddy 装成 hermes 插件 (plugin) 而非 skill
- 插件粒度更粗, 适合全局 hook (登录/定时), 不适合"VP 主动触发"
- skill 粒度细, 用户说"开会时"精确触发

### 拒绝: VPBuddy 继续裸 import `run_agent`, 不通过 hermes skill
- 跟 ADR-0009 "VPBuddy = Hermes skill 集合"矛盾
- 用户问 hermes "VPBuddy 怎么用" 触发不到
- 没法跟其他 hermes skill (autonomous-ai-agents, data-science) 组合

## 关联

- ADR-0009: 部署架构 — Hermes runtime
- ADR-0010: 流式 E2E 工作流 (in-process AIAgent)
- `~/.hermes/skills/vpbuddy/SKILL.md` — 实际 skill 定义
- `src/vpbuddy/sub_session_controller.py` — 6 agent 实现
- `src/vpbuddy/prompts/demo.md` — demo agent 强 prompt 约束
