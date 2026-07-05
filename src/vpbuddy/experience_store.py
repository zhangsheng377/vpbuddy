"""experience_store — 经验持久化 + 检索 (v0.9.0 #1 Phase 1)

存储:
- data/experiences/{meeting_id}.json — 每个会议的经验候选
- data/experiences/_all.json — 全部已确认经验的聚合索引

检索:
- get_approved_experiences(): 返回所有 approved=True 的条目
- search_experiences(domain, product_type): 按领域/产品类型过滤
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any
from .experience import ExperienceItem


# 2026-07-05 fix(#1): 经验存储根目录
EXPERIENCES_DIR = Path(
    os.environ.get("VPBUDDY_DATA_DIR", Path(__file__).resolve().parent.parent.parent / "data")
) / "experiences"

_aggregate_lock = threading.Lock()
_aggregate_path = EXPERIENCES_DIR / "_all.json"


def ensure_dir():
    EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)


# import 时自动创建目录
ensure_dir()


def save_experiences(meeting_id: str, items: list[ExperienceItem]) -> str:
    """保存一次会议的经验候选到独立文件."""
    ensure_dir()
    path = EXPERIENCES_DIR / f"{meeting_id}.json"
    data = {
        "meeting_id": meeting_id,
        "items": [it.to_dict() for it in items],
        "updated_at": __import__("datetime").datetime.now().isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同步更新聚合索引
    _update_aggregate(items)
    return str(path)


def load_experiences(meeting_id: str) -> list[ExperienceItem]:
    """加载某会议的经验候选."""
    path = EXPERIENCES_DIR / f"{meeting_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [ExperienceItem.from_dict(it) for it in data.get("items", [])]
    except (json.JSONDecodeError, KeyError):
        return []


def get_approved_experiences() -> list[ExperienceItem]:
    """返回所有已确认 (approved=True) 的经验条目.

    从聚合索引 _all.json 读取.
    """
    if not _aggregate_path.exists():
        return []
    try:
        data = json.loads(_aggregate_path.read_text(encoding="utf-8"))
        items = data.get("items", [])
        return [ExperienceItem.from_dict(it) for it in items if it.get("approved")]
    except (json.JSONDecodeError, KeyError):
        return []


def search_experiences(
    domain: str | None = None,
    product_type: str | None = None,
) -> list[ExperienceItem]:
    """按领域/产品类型检索已确认经验."""
    all_items = get_approved_experiences()
    results = []
    for item in all_items:
        if domain and item.domain != domain:
            continue
        if product_type and item.product_type != product_type:
            continue
        results.append(item)
    return results


def approve_experience(item_id: str, meeting_id: str) -> bool:
    """确认一条经验 (approved=True)."""
    items = load_experiences(meeting_id)
    found = False
    for it in items:
        if it.id == item_id:
            it.approved = True
            found = True
            break
    if not found:
        return False

    save_experiences(meeting_id, items)
    return True


def _update_aggregate(new_items: list[ExperienceItem]):
    """将新经验条目合并到聚合索引, 对已存在条目更新其字段."""
    with _aggregate_lock:
        existing = []
        if _aggregate_path.exists():
            try:
                data = json.loads(_aggregate_path.read_text(encoding="utf-8"))
                existing = data.get("items", [])
            except (json.JSONDecodeError, KeyError):
                pass

        existing_map = {it["id"]: it for it in existing}
        for item in new_items:
            d = item.to_dict()
            if d["id"] in existing_map:
                # 更新已存在条目的字段 (eg. approved 状态变更)
                existing_map[d["id"]].update(d)
            else:
                existing.append(d)
                existing_map[d["id"]] = d

        _aggregate_path.write_text(
            json.dumps({"items": existing, "updated_at": __import__("datetime").datetime.now().isoformat()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def extract_from_meeting_state(
    meeting_id: str,
    state: Any,
    meeting_title: str = "",
) -> list[ExperienceItem]:
    """从 MeetingState 自动提取经验候选.

    Args:
        meeting_id: 会议 ID
        state: MeetingState 实例 (有 requirements/goals/features/risks 等字段)
        meeting_title: 会议标题

    Returns:
        提取的经验候选列表 (均未确认)
    """
    items: list[ExperienceItem] = []
    now = __import__("datetime").datetime.now().isoformat()

    # 获取 facts
    facts = []
    try:
        for r in getattr(state, "requirements", []) or []:
            facts.append(f"REQ: {getattr(r, 'text', '')}")
        for g in getattr(state, "goals", []) or []:
            facts.append(f"GOAL: {getattr(g, 'text', '')}")
        for f in getattr(state, "features", []) or []:
            facts.append(f"FEAT: {getattr(f, 'text', '')}")
        for r in getattr(state, "risks", []) or []:
            facts.append(f"RISK: {getattr(r, 'text', '')}")
        for q in getattr(state, "open_questions", []) or []:
            facts.append(f"QUE: {getattr(q, 'text', '')}")
    except Exception:
        pass

    # 从 state 中提取高质量 evidence (完整的 requirement/risk 原文)
    high_confidence_evidence = [f for f in facts if len(f) > 20][:5]

    if not facts:
        return items

    domain = _guess_domain_from_meeting(meeting_title, facts)

    # --- 提取规则 (基于 state 结构化数据, 不需要 LLM) ---

    # 1. 从 requirements 中提取领域事实
    seen_texts: set[str] = set()
    for r_text in [f for f in facts if f.startswith("REQ:")]:
        text = r_text[5:].strip()
        if len(text) > 15 and text not in seen_texts:
            seen_texts.add(text)
            items.append(ExperienceItem(
                kind="domain_fact",
                text=text,
                source_meeting_id=meeting_id,
                domain=domain,
                evidence=[r_text],
                confidence=0.4,
                approved=False,
                created_at=now,
            ))

    # 2. 从 risks 中提取失败教训
    for r_text in [f for f in facts if f.startswith("RISK:")]:
        text = r_text[6:].strip()
        if len(text) > 20 and text not in seen_texts:
            seen_texts.add(text)
            items.append(ExperienceItem(
                kind="failure_lesson",
                text=f"注意事项: {text}",
                source_meeting_id=meeting_id,
                domain=domain,
                evidence=[r_text],
                confidence=0.5,
                approved=False,
                created_at=now,
            ))

    # 3. 从 goals 中提取决策规则
    for g_text in [f for f in facts if f.startswith("GOAL:")]:
        text = g_text[6:].strip()
        if len(text) > 20 and text not in seen_texts:
            seen_texts.add(text)
            items.append(ExperienceItem(
                kind="decision_rule",
                text=f"目标约束: {text}",
                source_meeting_id=meeting_id,
                domain=domain,
                evidence=[g_text],
                confidence=0.3,
                approved=False,
                created_at=now,
            ))

    return items[:20]  # 最多 20 条


def _guess_domain_from_meeting(title: str, facts: list[str]) -> str | None:
    """从会议标题和 state facts 猜测领域."""
    from .experience import guess_domain_from_meeting
    return guess_domain_from_meeting(title, facts)


def format_experiences_for_prompt(
    experiences: list[ExperienceItem],
    max_items: int = 5,
) -> str:
    """将已确认经验格式化为 prompt 可注入的文本块."""
    if not experiences:
        return ""

    lines = [
        "## 历史经验参考 (自动检索)",
        "",
        f"以下 {len(experiences)} 条经验来自相似领域的过去会议, 由用户确认:",
        "",
    ]
    for exp in experiences[:max_items]:
        kind_label = {
            "domain_fact": "📌 领域事实",
            "product_pattern": "🔁 产品模式",
            "decision_rule": "📐 决策规则",
            "terminology": "📖 术语",
            "failure_lesson": "⚠️ 教训",
            "user_preference": "💡 偏好",
        }.get(exp.kind.value, exp.kind.value)
        lines.append(f"- **[{kind_label}]** {exp.text}")
        if exp.domain:
            lines[-1] += f" (领域: {exp.domain})"
        lines.append("")

    return "\n".join(lines)
