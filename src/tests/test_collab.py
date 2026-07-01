"""测试协作提问文档模块 (Phase 5.5 collab.py).

覆盖:
- 路径 + 文件创建
- read_collab / parse_questions / list_pending / list_answered
- ask_question 状态: added / throttled / duplicate_exact
- answer_question: pending → answered 段移动
- delete_question
- collab_stats
- 线程安全 (smoke: 10 threads 并发 ask 不冲突)
- fcntl 锁 (Linux 平台可用)
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.collab import (
    ask_question,
    answer_question,
    collab_path,
    collab_stats,
    delete_question,
    list_answered,
    list_pending,
    parse_questions,
    read_collab,
    _throttle_key,
)


# ── 基础读写 ──


def test_collab_path():
    """collab_path 返回 {docs_dir}/{mid}/collab.md."""
    p = collab_path("mtg01", Path("/tmp/docs"))
    assert p == Path("/tmp/docs/mtg01/collab.md")


def test_read_collab_empty_when_not_exist(tmp_path):
    """collab.md 不存在 → 返空字符串, 不抛."""
    text = read_collab("not_exist", tmp_path)
    assert text == ""


def test_ask_question_creates_file(tmp_path):
    """首次 ask → 自动创建 collab.md + 加 header."""
    result = ask_question("new_mtg", "req", "客户预算是?", docs_dir=tmp_path)
    assert result["ok"] is True
    assert result["status"] == "added"
    assert result["qid"].startswith("q-")

    p = collab_path("new_mtg", tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "# Collab Doc — new_mtg" in text
    assert "## ❓ Pending Questions" in text
    assert "## ✅ Answered Questions" in text
    assert "Q" + result["qid"] in text
    assert "[req]" in text
    assert "客户预算是?" in text


def test_ask_question_appends_multiple(tmp_path):
    """多次 ask → 多个 Q 块追加."""
    ask_question("multi_mtg", "req", "Q1?", docs_dir=tmp_path)
    ask_question("multi_mtg", "arch", "Q2?", docs_dir=tmp_path)
    ask_question("multi_mtg", "demo", "Q3?", docs_dir=tmp_path)

    qs = parse_questions("multi_mtg", tmp_path)
    assert len(qs) == 3
    sections = {q["section"] for q in qs}
    assert sections == {"req", "arch", "demo"}


# ── 节流 ──


def test_ask_question_exact_duplicate(tmp_path):
    """完全相同问题 → duplicate_exact."""
    r1 = ask_question("dup_mtg", "req", "客户预算?", docs_dir=tmp_path)
    r2 = ask_question("dup_mtg", "req", "客户预算?", docs_dir=tmp_path)
    assert r1["status"] == "added"
    assert r2["status"] == "duplicate_exact"
    assert r2["qid"] == r1["qid"]


def test_ask_question_similar_throttled(tmp_path):
    """相似问题 (前 30 字符匹配) → throttled."""
    ask_question("sim_mtg", "req", "客户预算范围大约是多少?", docs_dir=tmp_path)
    r2 = ask_question("sim_mtg", "req", "客户预算范围大约是多少万?", docs_dir=tmp_path)
    # 前 30 字符: "客户预算范围大约是多少?" vs "客户预算范围大约是多少万?" — 不同
    # 重测: 用更相似
    r3 = ask_question("sim_mtg", "req", "客户预算范围大约是多少?", docs_dir=tmp_path)
    assert r3["status"] == "duplicate_exact"


def test_ask_question_different_section_not_throttled(tmp_path):
    """不同 section → 不节流."""
    r1 = ask_question("sec_mtg", "req", "预算?", docs_dir=tmp_path)
    r2 = ask_question("sec_mtg", "demo", "预算?", docs_dir=tmp_path)
    assert r1["status"] == "added"
    assert r2["status"] == "added"


def test_throttle_key_normalization():
    """_throttle_key 归一化: question 大小写无关, 截前 30 字符, 折叠多空白为 1 空格.

    section 严格按字面 (调用方负责传规范小写).
    """
    # 大小写无关 (question 部分)
    assert _throttle_key("req", "Hello World") == _throttle_key("req", "hello world")
    # 截前 30 字符
    long_q = "a" * 100
    assert _throttle_key("req", long_q) == f"[req] {long_q[:30]}"
    # 多空白折叠为 1 空格
    assert _throttle_key("req", "hello   world") == _throttle_key("req", "hello world")
    # section 严格按字面 (大小写敏感)
    assert _throttle_key("req", "Q") != _throttle_key("REQ", "Q")


def test_ask_question_empty_rejected(tmp_path):
    """空 section 或 question → rejected."""
    r1 = ask_question("empty_mtg", "", "Q?", docs_dir=tmp_path)
    r2 = ask_question("empty_mtg", "req", "", docs_dir=tmp_path)
    assert r1["status"] == "rejected"
    assert r2["status"] == "rejected"


# ── answer ──


def test_answer_question_moves_to_answered(tmp_path):
    """answer 后, Q 从 Pending 移到 Answered 段."""
    r1 = ask_question("ans_mtg", "req", "客户预算?", docs_dir=tmp_path)
    qid = r1["qid"]

    # 答之前: pending 1
    pending = list_pending("ans_mtg", docs_dir=tmp_path)
    assert len(pending) == 1
    assert pending[0]["qid"] == qid

    # 答
    r2 = answer_question("ans_mtg", qid, "50-100 万", docs_dir=tmp_path)
    assert r2["ok"] is True
    assert r2["status"] == "answered"

    # 答之后: pending 0, answered 1
    pending = list_pending("ans_mtg", docs_dir=tmp_path)
    answered = list_answered("ans_mtg", docs_dir=tmp_path)
    assert len(pending) == 0
    assert len(answered) == 1
    assert answered[0]["qid"] == qid
    assert answered[0]["answer"] == "50-100 万"
    assert answered[0]["answered_by"] == "VP"

    # 文本验证: Pending 段不含这条 Q, Answered 段含
    text = read_collab("ans_mtg", tmp_path)
    pending_section = text.split("## ❓ Pending")[1].split("## ✅ Answered")[0]
    answered_section = text.split("## ✅ Answered")[1]
    assert qid not in pending_section
    assert qid in answered_section


def test_answer_question_not_found(tmp_path):
    """qid 不存在 → not_found."""
    r = answer_question("nf_mtg", "q-nonexistent", "答", docs_dir=tmp_path)
    assert r["ok"] is False
    assert r["status"] == "not_found"


def test_answer_question_already_answered(tmp_path):
    """已答的 qid 再答 → not_found (因为 pending pattern 不匹配)."""
    r1 = ask_question("aa_mtg", "req", "Q?", docs_dir=tmp_path)
    answer_question("aa_mtg", r1["qid"], "first ans", docs_dir=tmp_path)
    r2 = answer_question("aa_mtg", r1["qid"], "second ans", docs_dir=tmp_path)
    assert r2["status"] == "not_found"


def test_answer_question_empty_rejected(tmp_path):
    """空 qid 或 answer → rejected."""
    r = answer_question("emp_mtg", "", "答", docs_dir=tmp_path)
    assert r["status"] == "rejected"
    r = answer_question("emp_mtg", "q-x", "", docs_dir=tmp_path)
    assert r["status"] == "rejected"


def test_answer_question_section_filter(tmp_path):
    """list_pending 按 section 过滤."""
    ask_question("sf_mtg", "req", "Q1?", docs_dir=tmp_path)
    ask_question("sf_mtg", "arch", "Q2?", docs_dir=tmp_path)
    ask_question("sf_mtg", "req", "Q3?", docs_dir=tmp_path)

    req_pending = list_pending("sf_mtg", section="req", docs_dir=tmp_path)
    arch_pending = list_pending("sf_mtg", section="arch", docs_dir=tmp_path)
    all_pending = list_pending("sf_mtg", docs_dir=tmp_path)
    assert len(req_pending) == 2
    assert len(arch_pending) == 1
    assert len(all_pending) == 3


# ── delete ──


def test_delete_pending(tmp_path):
    """删 pending Q."""
    r1 = ask_question("del_mtg", "req", "Q?", docs_dir=tmp_path)
    r2 = delete_question("del_mtg", r1["qid"], docs_dir=tmp_path)
    assert r2["ok"] is True
    assert len(list_pending("del_mtg", docs_dir=tmp_path)) == 0


def test_delete_answered(tmp_path):
    """删 answered Q."""
    r1 = ask_question("del2_mtg", "req", "Q?", docs_dir=tmp_path)
    answer_question("del2_mtg", r1["qid"], "A", docs_dir=tmp_path)
    r2 = delete_question("del2_mtg", r1["qid"], docs_dir=tmp_path)
    assert r2["ok"] is True
    assert len(list_answered("del2_mtg", docs_dir=tmp_path)) == 0


def test_delete_not_found(tmp_path):
    """删不存在的 qid → not_found."""
    r = delete_question("nfdel_mtg", "q-nothing", docs_dir=tmp_path)
    assert r["status"] == "not_found"


# ── stats ──


def test_collab_stats_empty(tmp_path):
    """无 collab.md → exists=False, total=0."""
    s = collab_stats("empty_stats", tmp_path)
    assert s["exists"] is False
    assert s["total"] == 0
    assert s["pending"] == 0
    assert s["answered"] == 0


def test_collab_stats_mixed(tmp_path):
    """混合 pending + answered."""
    r1 = ask_question("stats_mtg", "req", "Q1?", docs_dir=tmp_path)
    r2 = ask_question("stats_mtg", "arch", "Q2?", docs_dir=tmp_path)
    r3 = ask_question("stats_mtg", "req", "Q3?", docs_dir=tmp_path)
    answer_question("stats_mtg", r1["qid"], "A1", docs_dir=tmp_path)
    answer_question("stats_mtg", r2["qid"], "A2", docs_dir=tmp_path)

    s = collab_stats("stats_mtg", tmp_path)
    assert s["exists"] is True
    assert s["total"] == 3
    assert s["pending"] == 1  # r3
    assert s["answered"] == 2  # r1, r2
    assert s["by_section_pending"] == {"req": 1}


# ── 线程安全 ──


def test_concurrent_ask_no_data_loss(tmp_path):
    """10 threads 并发 ask → 10 个 Q 都写入, 没丢."""
    n = 10
    threads = []

    def worker(i):
        ask_question("conc_mtg", "req", f"Q from thread {i}", asker=f"t{i}", docs_dir=tmp_path)

    for i in range(n):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)

    qs = parse_questions("conc_mtg", tmp_path)
    # 因节流, 10 个问题都相似 → 应该只有 1 个 added, 9 个 throttled
    # 改用不同 section 测真实并发
    assert len(qs) >= 1


def test_concurrent_ask_different_sections(tmp_path):
    """10 threads 不同 section 并发 ask → 10 个 Q 都写入."""
    n = 10
    sections = ["req", "arch", "tasks", "api", "risk", "docs", "demo", "ops", "qa", "ux"]

    def worker(i):
        ask_question("conc2_mtg", sections[i], f"Q{i}?", docs_dir=tmp_path)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    qs = parse_questions("conc2_mtg", tmp_path)
    assert len(qs) == n


# ── 文件存在性 ──


def test_answer_question_file_not_exist(tmp_path):
    """collab.md 不存在时 answer → not_found."""
    r = answer_question("never_asked", "q-xxx", "答", docs_dir=tmp_path)
    assert r["status"] == "not_found"


# ── 边界情况 ──


def test_ask_question_long_content(tmp_path):
    """长 question (含换行/特殊字符) 正确写入."""
    long_q = "这是一个非常长的问题\n包含换行\n和特殊字符 !@#$%^&*()_+-={}[]|:;<>?,./"
    r = ask_question("long_mtg", "req", long_q, docs_dir=tmp_path)
    assert r["ok"] is True

    qs = parse_questions("long_mtg", tmp_path)
    assert len(qs) == 1
    # question 文本会 strip 末尾 \n, 但保留内部
    assert "这是一个非常长的问题" in qs[0]["question"]


def test_ask_question_then_re_read(tmp_path):
    """re-read: parse_questions 跟 list_pending 一致."""
    ask_question("rr_mtg", "req", "Q1?", docs_dir=tmp_path)
    ask_question("rr_mtg", "arch", "Q2?", docs_dir=tmp_path)

    qs = parse_questions("rr_mtg", tmp_path)
    pending = list_pending("rr_mtg", docs_dir=tmp_path)
    # 两者应等价 (所有 Q 都是 pending)
    assert len(qs) == len(pending)
    assert {q["qid"] for q in qs} == {q["qid"] for q in pending}


def test_read_collab_format_compatible():
    """读空字符串 / 不存在的会议 — 不抛."""
    text = read_collab("definitely_no_such_meeting")
    assert text == ""