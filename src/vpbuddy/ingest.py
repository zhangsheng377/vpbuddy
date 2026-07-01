"""
VPBuddy transcript → MeetingState 启发式 ingest 公共函数
(2026-06-23 从 e2e_ingest.py 抽出, 让 UI/CLI/Controller 都能调)

设计:
- 不引入 LLM 分类 (YAGNI, 真用户场景 state 由会议内嵌解析)
- 启发式 REQ/RISK/QUE 规则, 89 段 → ~15 项入库
- speaker_id → speaker_name 映射从 segment 文本启发推断
"""
from __future__ import annotations

import re

from .state import MeetingState, Platform, Priority

# 启发式分类规则
REQ_PATTERNS = [
    r"需要.*?",
    r"得.*?(?:加|做|改|实现|落地)",
    r"应该.*?",
    r"必须.*?",
    r"加上.*?",
    r"实现.*?",
    r"(?:P0|P1|P2).*?",
    r"那我们继续.*?",
    r"先.*?(?:做|改|实现|落地).*?",
    r"建议.*?",
    r"可以.*?",
    r"(?:核心|关键|主要).*?(?:是|就是).*?",
    r"用.*?(?:做|实现).*?",
    r"通过.*?(?:做|实现).*?",
    r"(?:要|要加|要做).*?",
]
RISK_PATTERNS = [
    r"(?:担心|风险|怕).*?",
    r"挂了.*?",
    r"(?:没|没有).*?(?:fallback|降级|兜底|watchdog)",
    r"挡.*?",
    r"延迟感",
    r"会不会.*?",
    r"经常.*?(?:阻|断|挂)",
]
QUESTION_PATTERNS = [
    r".*?\?$",
    r".*?(?:对吧|怎么办|怎么|是不是|会不会).*?",
    r".*?还是不.*?",
    r".*?(?:怎么|如何).*?办.*?",
    r".*?谁.*?",
    r".*?(?:几|多少).*?",
    r".*?(?:什么|哪个).*?(?:方案|办法|框架).*?",
]
DECISION_PATTERNS = [
    r"先.*?(?:这样|不动|做|跑起来|这样).*?",
    r"就是.*?",
    r"按.*?(?:ADR|\d+).*?",
    r"散会",
    r"我.*?(?:来|会).*?(?:总结|做|改|写|加|标)",
    r"今天.*?(?:先|的).*?(?:到这|先这样|完)",
    r"我们.*?(?:今天|就).*?",
    r"嗯.*?",
    r"对.*?",
    r"好.*?(?:的|了|，|。|就|那)",
    r"明白.*?",
    r"那就.*?",
    r"继续.*?",
    r"下一个.*?",
    r"辛苦.*?",
    r"那就到时候再说.*?",
]

HIGH_PRIO_KEYWORDS = ["P0", "必须", "挂", "挡", "fallback", "风险", "担心"]


def _classify(text: str) -> tuple[str, Priority]:
    """返回 (item_type, priority), 默认 ('skip', LOW)"""
    text_clean = text.strip().rstrip("，。,. ")
    if not text_clean or len(text_clean) < 6:
        return ("skip", Priority.LOW)
    for p in RISK_PATTERNS:
        if re.search(p, text_clean):
            prio = Priority.HIGH if any(k in text_clean for k in HIGH_PRIO_KEYWORDS) else Priority.MEDIUM
            return ("risk", prio)
    for p in DECISION_PATTERNS:
        if re.search(p, text_clean):
            return ("decision", Priority.MEDIUM)
    for p in QUESTION_PATTERNS:
        if re.search(p, text_clean):
            return ("question", Priority.MEDIUM)
    for p in REQ_PATTERNS:
        if re.search(p, text_clean):
            prio = Priority.HIGH if any(k in text_clean for k in HIGH_PRIO_KEYWORDS) else Priority.MEDIUM
            return ("requirement", prio)
    return ("skip", Priority.LOW)


def infer_speaker_map(segments: list[dict]) -> dict[str, str]:
    """从 segments 推断 speaker 映射 (按时长排序 → S00=最多, S01=次, S02=第三)

    Returns: {speaker_id: speaker_name} 如 {"SPEAKER_00": "VP", "SPEAKER_01": "PM", "SPEAKER_02": "Designer"}
    """
    from collections import defaultdict
    durs = defaultdict(float)
    for s in segments:
        durs[s["speaker_id"]] += s["end_sec"] - s["start_sec"]
    sorted_spks = sorted(durs.keys(), key=lambda k: -durs[k])
    # 默认 3 角色命名 (按典型 E2E 场景)
    default_names = ["VP", "PM", "Designer", "Guest1", "Guest2"]
    return {spk: default_names[i] if i < len(default_names) else f"Speaker{i+1}"
            for i, spk in enumerate(sorted_spks)}


def ingest_transcript(
    meeting_id: str,
    transcript: dict,
    project_name: str | None = None,
    platform: Platform = Platform.LOCAL,
    speaker_map: dict[str, str] | None = None,
    storage=None,
) -> MeetingState:
    """从 funasr 转写结果 → MeetingState, 启发式分类入 req/risk/question

    Args:
        meeting_id: 会议 ID
        transcript: gpu_transcribe.process() 输出 (含 segments/num_speakers)
        project_name: 会议项目名 (Optional)
        platform: Platform 枚举
        speaker_map: 自定义 speaker 映射, None=自动推断
        storage: MeetingStorage 实例, None=默认

    Returns:
        MeetingState 实例 (已 save 到 storage)
    """
    from .storage import MeetingStorage  # 避免循环

    storage = storage or MeetingStorage()

    if storage.exists(meeting_id):
        storage.delete(meeting_id)

    state = MeetingState(
        meeting_id=meeting_id,
        platform=platform,
        project_name=project_name or f"会议 {meeting_id}",
    )

    spk_map = speaker_map or infer_speaker_map(transcript["segments"])
    for spk_id, spk_name in spk_map.items():
        state.register_speaker(spk_id, spk_name)

    counts = {"requirement": 0, "risk": 0, "question": 0, "decision": 0, "skip": 0}
    for s in transcript["segments"]:
        text = s["text"]
        spk_name = spk_map.get(s["speaker_id"], "UNKNOWN")
        kind, prio = _classify(text)
        counts[kind] += 1
        if kind == "skip":
            continue
        if kind == "requirement":
            state.add_requirement(text, priority=prio, speaker_id=spk_name)
        elif kind == "risk":
            state.add_risk(text, priority=prio, speaker_id=spk_name)
        elif kind == "question":
            state.add_question(text, is_urgent=(prio == Priority.HIGH), speaker_id=spk_name)

    storage.save(state)
    state._ingest_counts = counts  # type: ignore  # 附加元信息
    return state
