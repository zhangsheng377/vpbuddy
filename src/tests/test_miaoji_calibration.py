"""miaoji_calibration 单元测试

覆盖:
- mock 模式: 模拟妙记, 跑校准
- 我们的转写 vs mock 妙记
- 报告结构完整性
- 对齐逻辑
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vpbuddy.miaoji_calibration import (
    TranscriptSegment,
    align_by_time,
    calibrate,
    compute_similarity,
    mock_miaoji_transcript,
)


def test_compute_similarity_identical():
    """完全相同文本相似度应为 1.0"""
    assert compute_similarity("hello", "hello") == 1.0


def test_compute_similarity_empty():
    """空字符串: 都空 = 1.0(都"相同"), 一空一非空 = 0.0"""
    assert compute_similarity("", "") == 1.0  # SequenceMatcher 默认行为
    assert compute_similarity("hello", "") == 0.0
    assert compute_similarity("", "hello") == 0.0


def test_compute_similarity_partial():
    """部分相同应该有中间值"""
    sim = compute_similarity("hello world", "hello there")
    assert 0.3 < sim < 0.8


def test_mock_returns_segments():
    """Mock 应返回有效段(确定性 seed)"""
    our = [
        TranscriptSegment(0, 5, "SPK_00", "段1"),
        TranscriptSegment(5, 10, "SPK_01", "段2"),
        TranscriptSegment(10, 15, "SPK_00", "段3"),
    ]
    mock = mock_miaoji_transcript(our)
    assert isinstance(mock, list)
    # 应该至少 1 段(seed=42 不太可能全 drop)
    assert len(mock) >= 1


def test_align_by_time_exact_match():
    """完全时间匹配应该 100% 对齐"""
    ours = [TranscriptSegment(0, 5, "A", "x"), TranscriptSegment(10, 15, "B", "y")]
    theirs = [TranscriptSegment(0, 5, "A", "x"), TranscriptSegment(10, 15, "B", "y")]
    matched, missing_t, missing_o = align_by_time(ours, theirs)
    assert len(matched) == 2
    assert missing_t == []
    assert missing_o == []


def test_align_by_time_within_tolerance():
    """±2s 内应该匹配"""
    ours = [TranscriptSegment(10, 15, "A", "x")]
    theirs = [TranscriptSegment(11, 16, "A", "x")]  # 1s 偏移
    matched, _, _ = align_by_time(ours, theirs)
    assert len(matched) == 1


def test_align_by_time_outside_tolerance():
    """超过 2s 不应该匹配"""
    ours = [TranscriptSegment(10, 15, "A", "x")]
    theirs = [TranscriptSegment(20, 25, "A", "x")]  # 10s 偏移
    matched, missing_t, missing_o = align_by_time(ours, theirs)
    assert len(matched) == 0
    assert len(missing_o) == 1  # ours 有 theirs 没


def test_calibrate_perfect_match():
    """完全相同的转写应该 100% 校准通过"""
    our = [TranscriptSegment(0, 5, "A", "hello"), TranscriptSegment(5, 10, "B", "world")]
    mock = [TranscriptSegment(0, 5, "A", "hello"), TranscriptSegment(5, 10, "B", "world")]
    report = calibrate("MTG01", our, mock)
    assert report.text_similarity == 1.0
    assert report.time_alignment_pct == 100.0
    assert len(report.speaker_confusion) == 0
    assert any("通过" in r for r in report.recommendations)


def test_calibrate_with_discrepancies():
    """有差异应该触发建议"""
    our = [
        TranscriptSegment(0, 5, "A", "hello world"),  # mock 改一下文本
        TranscriptSegment(5, 10, "B", "this is a test"),
        TranscriptSegment(10, 15, "A", "another segment"),
    ]
    mock = [
        TranscriptSegment(0, 5, "A", "hello WORLD"),  # match 但文本不同
        # 缺 2 段
        TranscriptSegment(20, 25, "C", "totally different text"),  # time + speaker 都不匹配
    ]
    report = calibrate("MTG01", our, mock)
    assert report.our_segments == 3
    assert report.miaoji_segments == 2
    assert 0 < report.text_similarity < 1.0  # "hello world" vs "hello WORLD" 不完全相同
    assert len(report.missing_in_miaoji) >= 1
    assert len(report.recommendations) >= 1


def test_calibrate_empty_inputs():
    """空输入不应该崩"""
    report = calibrate("MTG01", [], [])
    assert report.text_similarity == 0.0
    assert report.time_alignment_pct == 0.0
    assert len(report.recommendations) >= 1


def test_report_serialization():
    """报告应该可以 JSON 序列化"""
    import json
    from dataclasses import asdict
    our = [TranscriptSegment(0, 5, "A", "test")]
    mock = [TranscriptSegment(0, 5, "A", "test")]
    report = calibrate("MTG01", our, mock)
    data = asdict(report)
    json_str = json.dumps(data, ensure_ascii=False)
    assert json_str  # 不为空
    # 反序列化也应该 OK
    parsed = json.loads(json_str)
    assert parsed["meeting_id"] == "MTG01"
    assert parsed["our_segments"] == 1
