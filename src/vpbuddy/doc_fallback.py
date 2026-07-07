"""代码生成 docs(不依赖 LLM 工具调用)

背景(2026-06-22):MiniMax-M3 (8B) 模型工具调用能力弱,经常 thinking-only 不调 write_file。
虽然 system prompt 已强化,仍可能失败。这个 fallback 用 state facts 直接拼 markdown,
保证 doc 一定会被写盘。

用法:
    from vpbuddy.doc_fallback import generate_doc
    content = generate_doc(meeting_id, doc_kind, state_dict)
    write_file(doc_path, content)
"""
from datetime import datetime
from pathlib import Path
from typing import Any

# === 模板函数:每种 doc_kind 一份 ===
# YAGNI:只做 MVP 必需的 6 种,arch/api/demo 简化但保留结构

def _gen_req(state: dict[str, Any]) -> str:
    facts = state.get("facts", {})
    reqs = facts.get("REQ", [])
    goals = facts.get("GOAL", [])
    md = f"""# 需求清单

**会议**:{state.get("title", state.get("meeting_id", "?"))}
**时间**:{state.get("created_at", "?")}

## REQ 需求列表

"""
    for i, r in enumerate(reqs, 1):
        md += f"### REQ-{i:03d}\n- {r}\n\n"
    if goals:
        md += "## GOAL 目标\n\n"
        for g in goals:
            md += f"- {g}\n"
    return md


def _gen_arch(state: dict[str, Any]) -> str:
    facts = state.get("facts", {})
    risks = facts.get("RISK", [])
    risks_block = "\n".join("- " + r for r in risks)
    return f"""# 架构文档

**会议**:{state.get("title", state.get("meeting_id", "?"))}
**时间**:{state.get("created_at", "?")}

## 总体架构

```
[音频采集] → [ASR 转写] → [事实累积] → [6 文档生成] → [KB 索引]
   loopback    funasr      MeetingState    模板/LLM      sqlite-vec
```

## 数据流

1. VP 桌面客户端启动音频 loopback 采集，音频流上传服务端
2. 服务端实时 ASR 转写(中文:funasr paraformer-zh)
3. 转写结果实时写入 meeting state JSON
4. 6 个子 session 并行生成文档(本会议,持续更新)
5. 文档自动入知识库,后续检索用 sqlite-vec 余弦相似度

## 关键决策

- 客户端-服务端架构:客户端采集展示,服务端计算存储
- 单租户:数据隔离,支持私有化部署
- 存储:服务端 SQLite + sqlite-vec(零依赖向量索引)
- 嵌入模型:paraphrase-multilingual-MiniLM-L12-v2 (384 维)
- 冷启动:首次安装预下载 256MB 模型

## 模块划分

| 模块 | 职责 | 存储 |
|------|------|------|
| transcribe | 音频 → 文本 | transcript.json |
| state | 累积事实 | meeting_state.json |
| trigger | 6 doc_kind 子 session | docs/<meeting_id>/ |
| kb | 向量化 + 检索 | knowledge.db |

## 已知风险

{risks_block}
"""


def _gen_tasks(state: dict[str, Any]) -> str:
    facts = state.get("facts", {})
    reqs = facts.get("REQ", [])
    md = f"""# 任务列表

**会议**:{state.get("title", state.get("meeting_id", "?"))}
**时间**:{state.get("created_at", "?")}

## T-001 ... T-{len(reqs):03d}

"""
    for i, r in enumerate(reqs, 1):
        md += f"### T-{i:03d} 实现:{r}\n- **负责人**: 待分配\n- **工期**: 待估\n- **依赖**: -\n- **状态**: pending\n- **验收标准**: {r} 上线可用\n\n"
    return md


def _gen_api(state: dict[str, Any]) -> str:
    state.get("facts", {})
    return f"""# API 设计

**会议**:{state.get("title", state.get("meeting_id", "?"))}
**时间**:{state.get("created_at", "?")}

## POST /v1/meetings

创建一个新会议(VP 客户端上传音频转写结果)

### Request
```yaml
meeting_id: string  # 必填,VP 客户端生成 UUID
title: string       # 会议标题
platform: string    # 腾讯会议 / 钉钉 / 企微 / local
facts:
  REQ: [string]     # 需求列表
  GOAL: [string]    # 目标列表
  FEAT: [string]    # 功能列表
  RISK: [string]    # 风险列表
  QUE: [string]     # 问题列表
transcript_path: string  # 可选,转写文件路径
```

### Response 200
```yaml
status: "active"
doc_kinds_triggered: [string]  # 已触发的 6 种文档
kb_queued: integer             # 入库 KB 的文档数
```

### 错误码
- 401: 未授权
- 422: 缺少必填字段
- 500: 服务器内部错误

## GET /v1/kb/search

跨会议知识库检索

### Request
```yaml
query: string  # 搜索关键词
top_k: integer # 默认 5
```

### Response 200
```yaml
results:
  - meeting_id: string
    doc_kind: string
    snippet: string       # 相关片段
    distance: float       # 余弦距离,越小越相关
```
"""


def _gen_risk(state: dict[str, Any]) -> str:
    facts = state.get("facts", {})
    risks = facts.get("RISK", [])
    md = f"""# 风险评估

**会议**:{state.get("title", state.get("meeting_id", "?"))}
**时间**:{state.get("created_at", "?")}

## R-001 ... R-{len(risks):03d}

"""
    for i, r in enumerate(risks, 1):
        md += f"### R-{i:03d} {r}\n- **严重度**: medium\n- **概率**: 3\n- **影响**: 3\n- **风险值**: 9\n- **缓解方案**: 见会议讨论\n- **Owner**: 待分配\n- **状态**: open\n\n"
    return md


def _gen_demo(state: dict[str, Any]) -> str:
    """demo 是 HTML 主文件,目录结构见 trigger_sub_session

    2026-07-07 ADR-0048: 无会议内容时不强行生成 demo 模板。
    旧行为: 空会议也会渲染"VPBuddy 演示"泄露系统内部流程。
    新行为: cleaned_text 为空且无 reqs → 返回占位 HTML, 不含系统信息。
    """
    facts = state.get("facts", {})
    reqs = facts.get("REQ", [])
    cleaned = state.get("cleaned_text", "")

    # ADR-0048: 没有任何会议内容时不生成系统流程模板
    if not reqs and not cleaned.strip():
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>会议 {state.get("title", "?")}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 700px; margin: 40px auto; padding: 20px; color: #334155; }}
    h1 {{ color: #2563eb; }}
    .empty {{ text-align: center; color: #94a3b8; margin-top: 80px; font-size: 18px; }}
    .meta {{ color: #94a3b8; font-size: 13px; margin-top: 60px; }}
  </style>
</head>
<body>
  <h1>会议记录演示</h1>
  <p class="meta">会议:{state.get("title", "?")} · {state.get("created_at", "?")}</p>
  <div class="empty">暂无会议内容, 等待音频采集与转写...</div>
  <p class="meta">页面会在会议内容就绪后自动更新</p>
</body>
</html>
"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>演示 - {state.get("title", "?")}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
    h1 {{ color: #2563eb; }}
    .req {{ background: #f1f5f9; border-left: 4px solid #2563eb; padding: 12px; margin: 8px 0; }}
    .meta {{ color: #64748b; font-size: 14px; }}
  </style>
</head>
<body>
  <h1>会议演示</h1>
  <p class="meta">会议:{state.get("title", "?")} · 时间:{state.get("created_at", "?")}</p>

  <h2>核心需求</h2>
  {''.join(f'<div class="req">{r}</div>' for r in reqs) if reqs else '<p class="meta">等待需求讨论...</p>'}

  <h2>会议摘要</h2>
  <pre style="white-space:pre-wrap;background:#f8fafc;padding:16px;border-radius:8px;">{cleaned[:3000]}</pre>

  <p class="meta">本页面根据会议内容实时生成 · 最后更新:{datetime.now().isoformat()}</p>
</body>
</html>
"""


_GENERATORS = {
    "req": _gen_req,
    "arch": _gen_arch,
    "tasks": _gen_tasks,
    "api": _gen_api,
    "risk": _gen_risk,
    "demo": _gen_demo,
}


def generate_doc(meeting_id: str, doc_kind: str, state: dict[str, Any]) -> str:
    """根据 state + doc_kind 生成 markdown/HTML 内容

    Args:
        meeting_id: 会议 ID
        doc_kind: req/arch/tasks/api/risk/demo
        state: meeting state dict(含 facts.REQ 等)

    Returns:
        完整文档内容字符串

    Raises:
        ValueError: 未知的 doc_kind
    """
    if doc_kind not in _GENERATORS:
        raise ValueError(f"Unknown doc_kind: {doc_kind}, valid: {list(_GENERATORS)}")
    return _GENERATORS[doc_kind](state)


def meeting_state_to_dict(state) -> dict[str, Any]:
    """把 MeetingState(BaseModel) 转成 doc_fallback 用的 dict

    MeetingState 字段:requirements/goals/features/risks/open_questions (BaseModel 对象列表)
    doc_fallback 期望:facts.REQ/GOAL/FEAT/RISK/QUE (string 列表)
    """
    # 兼容 MeetingState(BaseModel) 和 dict 两种输入
    if isinstance(state, dict):
        return state
    return {
        "meeting_id": getattr(state, "meeting_id", "?"),
        "title": getattr(state, "project_name", None) or getattr(state, "meeting_id", "?"),
        "created_at": getattr(state, "started_at", "?"),
        "platform": getattr(getattr(state, "platform", None), "value", "?"),
        "cleaned_text": getattr(state, "cleaned_text", ""),  # 2026-07-07 ADR-0048: demo 用
        "facts": {
            "REQ": [r.text for r in getattr(state, "requirements", [])],
            "GOAL": [g.text for g in getattr(state, "goals", [])],
            "FEAT": [f.text for f in getattr(state, "features", [])],
            "RISK": [r.text for r in getattr(state, "risks", [])],
            "QUE": [q.text for q in getattr(state, "open_questions", [])],
        },
    }


def generate_and_write(meeting_id: str, doc_kind: str, state, doc_path: Path) -> Path:
    """生成文档并写入 doc_path(自动建父目录)

    state 接受 MeetingState(BaseModel) 或 dict
    """
    state_dict = meeting_state_to_dict(state)
    doc_path = Path(doc_path)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_doc(meeting_id, doc_kind, state_dict)
    doc_path.write_text(content, encoding="utf-8")
    return doc_path
