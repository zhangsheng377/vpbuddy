"""飞书妙记校准 — Step 5

功能: 拉飞书妙记的转写 vs 我们的 ASR 转写,输出 diff 报告
- 我们的 ASR: faster-whisper + pyannote (Step 2)
- 飞书妙记: 飞书云端转写服务, 通过分钟级 API 拉取

YAGNI: 默认 mock 模式(不需要真 token)
      真模式需要 FEISHU_APP_ID + FEISHU_APP_SECRET + minute_token

典型用法:
    # Mock 模式(对比本地 ASR vs 模拟妙记输出)
    python -m vpbuddy.miaoji_calibration --meeting D5ABF3427649 --mock

    # 真模式(需要飞书 app credentials)
    FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \\
    python -m vpbuddy.miaoji_calibration --meeting D5ABF3427649 \\
        --minute-token obcnq3b9jl72l83w4f14xxxx
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Tuple

# 我们的转写文件
TRANSCRIPT_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", "/home/zsd/vpbuddy/docs"))


@dataclass
class TranscriptSegment:
    """转写段(双方统一格式)"""
    start: float
    end: float
    speaker: str
    text: str


@dataclass
class DiffReport:
    """校准报告"""
    meeting_id: str
    our_segments: int
    miaoji_segments: int
    text_similarity: float
    time_alignment_pct: float
    missing_in_miaoji: List[str]  # 我们有但妙记没有的段
    missing_in_ours: List[str]    # 妙记有但我们没有的段
    speaker_confusion: List[Tuple[str, str]]  # (我们说的说话人, 妙记说的说话人)
    recommendations: List[str]


def load_our_transcript(meeting_id: str) -> List[TranscriptSegment]:
    """读我们自己的转写(Step 2 输出)"""
    # YAGNI: 现在没有 transcript.json, 从 meeting_state 简单推算
    # 真实场景下, transcript.py 会写 /home/zsd/vpbuddy/docs/{mid}/transcript.json
    path = TRANSCRIPT_DIR / meeting_id / "transcript.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        TranscriptSegment(
            start=seg.get("start", 0),
            end=seg.get("end", 0),
            speaker=seg.get("speaker", "unknown"),
            text=seg.get("text", ""),
        )
        for seg in data.get("segments", [])
    ]


def fetch_miaoji_transcript(minute_token: str, app_id: Optional[str] = None,
                            app_secret: Optional[str] = None) -> List[TranscriptSegment]:
    """从飞书妙记 API 拉转写

    需要 FEISHU_APP_ID + FEISHU_APP_SECRET + minute_token
    YAGNI: 不实现 OAuth 流程,只做最小 HTTP 调用
    """
    if not app_id or not app_secret:
        raise ValueError("需要 FEISHU_APP_ID + FEISHU_APP_SECRET + minute_token")

    # 1. 拿 tenant_access_token
    import urllib.request
    import urllib.parse

    token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    token_data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(token_url, data=token_data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        token_result = json.loads(resp.read().decode("utf-8"))
    access_token = token_result.get("tenant_access_token")
    if not access_token:
        raise RuntimeError(f"Failed to get access_token: {token_result}")

    # 2. 拿妙记信息(只 metadata, 真 transcript 还需要子 API)
    info_url = f"https://open.feishu.cn/open-apis/minutes/v1/minutes/{minute_token}"
    req = urllib.request.Request(info_url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        info = json.loads(resp.read().decode("utf-8"))

    # YAGNI: 妙记完整 transcript 还需要调用 minutes/v1/minutes/{token}/transcript 子 API
    # 文档: https://open.feishu.cn/document/server-docs/minutes-v1/minute-transcript
    # 现在只返回 metadata
    return [TranscriptSegment(
        start=0, end=0,
        speaker="unknown",
        text=f"[妙记 metadata] {json.dumps(info.get('data', {}).get('minute', {}), ensure_ascii=False)[:200]}"
    )]


def mock_miaoji_transcript(our_segments: List[TranscriptSegment]) -> List[TranscriptSegment]:
    """Mock 妙记(模拟妙记但有差异,用于测试校准逻辑)

    模拟:
    - 80% 段完全相同
    - 10% 段妙记缺
    - 10% 段我们缺
    - 说话人 5% 错位
    - 时间戳 ±5% 偏移
    """
    import random
    random.seed(42)  # 确定性

    mock = []
    for i, seg in enumerate(our_segments):
        r = random.random()
        if r < 0.10:
            continue  # 妙记缺
        # 时间戳 ±5% 偏移
        start = seg.start + random.uniform(-0.5, 0.5) if seg.start else 0
        end = seg.end + random.uniform(-0.5, 0.5) if seg.end else 0
        # 说话人 5% 错位
        speaker = seg.speaker
        if r < 0.15:
            speakers = ["SPK_00", "SPK_01", "SPK_02"]
            speaker = random.choice([s for s in speakers if s != seg.speaker])
        mock.append(TranscriptSegment(
            start=start,
            end=end,
            speaker=speaker,
            text=seg.text,
        ))

    # 加 1-2 段"妙记有,我们没有"
    for _ in range(random.randint(1, 2)):
        mock.append(TranscriptSegment(
            start=len(mock) * 5.0,
            end=len(mock) * 5.0 + 3.0,
            speaker="SPK_99",
            text="[妙记独有] 这一段在我们的 ASR 中没识别到"
        ))
    return mock


def align_by_time(ours: List[TranscriptSegment], theirs: List[TranscriptSegment],
                  tolerance: float = 2.0) -> Tuple[List, List, List]:
    """按时间戳对齐(±2s 内算同一段)"""
    matched = []
    used_theirs = set()
    for o in ours:
        for i, t in enumerate(theirs):
            if i in used_theirs:
                continue
            if abs(o.start - t.start) <= tolerance:
                matched.append((o, t))
                used_theirs.add(i)
                break
    missing_in_theirs = [o for o in ours if o not in [m[0] for m in matched]]
    missing_in_ours = [t for i, t in enumerate(theirs) if i not in used_theirs]
    return matched, missing_in_theirs, missing_in_ours


def compute_similarity(text1: str, text2: str) -> float:
    """两段文本的相似度 0-1"""
    return SequenceMatcher(None, text1, text2).ratio()


def calibrate(meeting_id: str, our_segments: List[TranscriptSegment],
              miaoji_segments: List[TranscriptSegment]) -> DiffReport:
    """核心: 校准两个 transcript"""
    # 1. 时间对齐
    matched, missing_in_miaoji, missing_in_ours = align_by_time(our_segments, miaoji_segments)

    # 2. 文本相似度(平均值)
    if matched:
        sims = [compute_similarity(o.text, t.text) for o, t in matched]
        text_similarity = sum(sims) / len(sims)
    else:
        text_similarity = 0.0

    # 3. 时间对齐率
    alignment_pct = len(matched) / max(len(our_segments), 1) * 100

    # 4. 说话人错位
    speaker_confusion = [(o.speaker, t.speaker) for o, t in matched
                         if o.speaker != t.speaker]

    # 5. 建议
    recs = []
    if text_similarity < 0.85:
        recs.append(f"文本相似度 {text_similarity:.1%} < 85%, 检查 ASR 模型或音频质量")
    if alignment_pct < 80:
        recs.append(f"时间对齐率 {alignment_pct:.0f}% < 80%, 检查 VAD 切分或说话人映射")
    if speaker_confusion:
        recs.append(f"{len(speaker_confusion)} 段说话人错位, 考虑加大 pyannote 模型或调阈值")
    if missing_in_miaoji:
        recs.append(f"{len(missing_in_miaoji)} 段我们有但妙记没有, 可能是会议开头/结尾静音段")
    if missing_in_ours:
        recs.append(f"{len(missing_in_ours)} 段妙记有但我们没有, 检查 ASR 漏识")
    if not recs:
        recs.append("✅ 校准通过, ASR 与妙记一致")

    return DiffReport(
        meeting_id=meeting_id,
        our_segments=len(our_segments),
        miaoji_segments=len(miaoji_segments),
        text_similarity=text_similarity,
        time_alignment_pct=alignment_pct,
        missing_in_miaoji=[s.text for s in missing_in_miaoji[:5]],
        missing_in_ours=[s.text for s in missing_in_ours[:5]],
        speaker_confusion=speaker_confusion[:5],
        recommendations=recs,
    )


def print_report(report: DiffReport) -> None:
    """打印人类可读报告"""
    print(f"\n{'='*60}")
    print(f"📊 飞书妙记校准报告 — {report.meeting_id}")
    print(f"{'='*60}\n")
    print(f"我们的段数:    {report.our_segments}")
    print(f"妙记的段数:    {report.miaoji_segments}")
    print(f"文本相似度:    {report.text_similarity:.1%}")
    print(f"时间对齐率:    {report.time_alignment_pct:.0f}%")
    print(f"说话人错位段:  {len(report.speaker_confusion)}")
    print()

    if report.missing_in_miaoji:
        print(f"⚠️ 我们有但妙记没有 ({len(report.missing_in_miaoji)} 段):")
        for t in report.missing_in_miaoji:
            print(f"  - {t[:80]}")
        print()

    if report.missing_in_ours:
        print(f"⚠️ 妙记有但我们没有 ({len(report.missing_in_ours)} 段):")
        for t in report.missing_in_ours:
            print(f"  - {t[:80]}")
        print()

    if report.speaker_confusion:
        print(f"⚠️ 说话人错位 ({len(report.speaker_confusion)} 段):")
        for o, t in report.speaker_confusion:
            print(f"  - 我们: {o}  妙记: {t}")
        print()

    print("💡 建议:")
    for r in report.recommendations:
        print(f"  - {r}")
    print()


def main():
    parser = argparse.ArgumentParser(description="VPBuddy 飞书妙记校准 (Step 5)")
    parser.add_argument("--meeting", required=True, help="会议 ID")
    parser.add_argument("--minute-token", help="飞书妙记 minute_token (URL 最后一串)")
    parser.add_argument("--mock", action="store_true", help="Mock 模式(无需飞书 token)")
    parser.add_argument("--output", help="输出 JSON 报告路径")
    args = parser.parse_args()

    # 1. 读我们的转写
    our_segments = load_our_transcript(args.meeting)
    if not our_segments:
        print(f"⚠️ 没找到 {args.meeting} 的 transcript.json")
        print(f"   (路径: {TRANSCRIPT_DIR / args.meeting / 'transcript.json'})")
        print(f"   用 mock 模式继续(假设有 5 段示例)...")
        our_segments = [
            TranscriptSegment(0, 5, "SPK_00", "大家好今天我们讨论一下碳排放管理系统的需求"),
            TranscriptSegment(5, 10, "SPK_01", "好的首先需要支持碳排放数据的统一录入和核算"),
            TranscriptSegment(10, 15, "SPK_00", "组织架构方面要支持多层级"),
            TranscriptSegment(15, 20, "SPK_01", "还有排放因子的来源需要明确,IPCC 还是国家发改委"),
            TranscriptSegment(20, 25, "SPK_00", "Scope 3 的支持先不做 MVP 阶段"),
        ]

    # 2. 拉妙记的转写
    if args.mock:
        print("🎭 Mock 模式: 模拟妙记转写(确定性 seed=42)")
        miaoji_segments = mock_miaoji_transcript(our_segments)
    else:
        if not args.minute_token:
            print("❌ 非 mock 模式需要 --minute-token")
            sys.exit(1)
        app_id = os.environ.get("FEISHU_APP_ID")
        app_secret = os.environ.get("FEISHU_APP_SECRET")
        if not app_id or not app_secret:
            print("❌ 需要环境变量 FEISHU_APP_ID + FEISHU_APP_SECRET")
            sys.exit(1)
        miaoji_segments = fetch_miaoji_transcript(args.minute_token, app_id, app_secret)

    # 3. 校准
    report = calibrate(args.meeting, our_segments, miaoji_segments)
    print_report(report)

    # 4. 可选: 输出 JSON
    if args.output:
        Path(args.output).write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"📁 报告已保存: {args.output}")


if __name__ == "__main__":
    main()
