"""experience_store — 经验持久化 + 检索 (v0.9.0 #1 Phase 1 → v0.23.0)

存储:
- data/experiences/{meeting_id}.json — 每个会议的经验候选
- data/experiences/_all.json — 全部已确认经验的聚合索引

检索:
- get_approved_experiences(): 返回所有 approved=True 的条目
- search_experiences(domain, product_type): 按领域/产品类型过滤

PII 防护:
- extract_from_meeting_state() 过滤含人名/邮箱/电话/具体需求的候选
- _might_contain_pii() 汉字人名形态 + 常见 PII pattern 检测
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
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


# v0.23.0: PII 检测 — 拒绝含个人信息的经验候选
_PII_PATTERNS = [
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),  # email
    re.compile(r'1[3-9]\d{9}'),  # 中国手机号
    re.compile(r'\b\d{3}[-.]?\d{4}[-.]?\d{4}\b'),  # 电话号
    re.compile(r'\b\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b'),  # 18位身份证
]

# 常见中国人名特征: 2-3 汉字, 跟在动词/介绍的后面
_NAME_CONTEXT_PATTERNS = [
    re.compile(r'(叫|是|负责|由|为|姓|名)\s*([\u4e00-\u9fa5]{2,3})\s*(。|，|的|在|做|来|去|要|已经|了|这个|那个|一个|我们)'),
    re.compile(r'^([\u4e00-\u9fa5]{2,3})(要求|说|提出|认为|表示|指出|需要|希望|建议|决定|同意|反对)'),
]

# 常见姓 + 名 组合 (常见 100 姓)
_COMMON_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张"
    "孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎"
    "鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤"
    "滕殷罗毕郝邬安常乐于时傅皮下齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞"
    "熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭"
    "梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫"
    "经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄麴家封芮羿储靳汲邴糜松"
    "井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫"
    "宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄"
    "印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双"
    "闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍卻璩桑桂濮牛寿通"
    "边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容"
    "向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东"
    "欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空"
    "曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
    "万俟司马上官欧阳夏侯诸葛闻人东方赫连皇甫尉迟公羊"
    "澹台公冶宗政濮阳淳于单于太叔申屠公孙仲孙轩辕令狐"
    "钟离宇文长孙慕容鲜于闾丘司徒司空丌官司寇仉督子车"
    "颛孙端木巫马公西漆雕乐正壤驷公良拓跋夹谷宰父谷梁"
    "晋楚闫法汝鄢涂钦段干百里东郭南门呼延归海羊舌微生"
    "岳帅缑亢况后有琴梁丘左丘东门西门商牟佘佴伯赏南宫"
    "墨哈谯笪年爱阳佟第五言福"
)


def _might_contain_pii(text: str) -> bool:
    """检测文本是否可能含 PII / 具体需求。

    返回 True = 应拒绝此经验候选。
    """
    if not text or len(text) < 4:
        return True

    # 1. regex 扫描邮箱/电话/身份证
    for pat in _PII_PATTERNS:
        if pat.search(text):
            return True

    # 2. 人名上下文检测
    for ctx_pat in _NAME_CONTEXT_PATTERNS:
        m = ctx_pat.search(text)
        if m:
            name_part = m.group(2) if ctx_pat is _NAME_CONTEXT_PATTERNS[0] else m.group(1)
            if len(name_part) >= 2 and name_part[0] in _COMMON_SURNAMES:
                return True

    # 3. 具体需求特征: 包含具体产品名/功能描述 (太长的 domain_fact 基本是原文复制)
    if len(text) > 80:
        return True

    return False


def save_experiences(meeting_id: str, items: list[ExperienceItem]) -> str:
    """保存一次会议的经验候选到独立文件."""
    ensure_dir()
    path = EXPERIENCES_DIR / f"{meeting_id}.json"
    data = {
        "meeting_id": meeting_id,
        "items": [it.to_dict() for it in items],
        "updated_at": datetime.now(timezone.utc).isoformat(),
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
    exclude_meeting_id: str | None = None,
) -> list[ExperienceItem]:
    """按领域/产品类型检索已确认经验.

    v0.22.7: 加 exclude_meeting_id 防止当前会议的经验自我引用.
    """
    all_items = get_approved_experiences()
    results = []
    for item in all_items:
        if domain and item.domain != domain:
            continue
        if product_type and item.product_type != product_type:
            continue
        if exclude_meeting_id and item.source_meeting_id == exclude_meeting_id:
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


def reject_experience(item_id: str, meeting_id: str) -> bool:
    """拒绝/删除一条经验候选 (从 meeting 文件 + 聚合索引中移除, v0.19.0)."""
    items = load_experiences(meeting_id)
    orig_count = len(items)
    items = [it for it in items if it.id != item_id]
    if len(items) == orig_count:
        return False  # not found

    # 重新保存 (不含被拒绝项)
    ensure_dir()
    path = EXPERIENCES_DIR / f"{meeting_id}.json"
    data = {
        "meeting_id": meeting_id,
        "items": [it.to_dict() for it in items],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 从聚合索引中删除
    with _aggregate_lock:
        if _aggregate_path.exists():
            try:
                agg = json.loads(_aggregate_path.read_text(encoding="utf-8"))
                agg["items"] = [it for it in agg.get("items", []) if it.get("id") != item_id]
                _aggregate_path.write_text(
                    json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            except Exception:
                pass
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
            json.dumps({"items": existing, "updated_at": datetime.now(timezone.utc).isoformat()},
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
    now = datetime.now(timezone.utc).isoformat()

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
        if len(text) > 15 and text not in seen_texts and not _might_contain_pii(text):
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
        if len(text) > 20 and text not in seen_texts and not _might_contain_pii(text):
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
