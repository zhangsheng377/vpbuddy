"""VPBuddy E2E 集成测试(2026-06-22)

跑完整链路:
  音频输入 → ASR → MeetingState → 6 docs → KB → UI 检索

触发方式:
    RUN_E2E=1 pytest src/tests/test_e2e_integration.py -v -s

注:不写 RUN_E2E 时整个文件被 skip,不污染日常 pytest。
"""
import os
import json
import time
import shutil
import subprocess
import urllib.request
from urllib.parse import quote as url_quote
from pathlib import Path
from typing import Optional, List

import pytest


# === Gate:必须有 RUN_E2E=1 才跑 ===
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="E2E 集成测试(慢,需真实音频/ASR/KB),用 RUN_E2E=1 显式触发",
)


# === 路径配置(与 VPBuddy 默认一致) ===
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "meetings"
DOCS_DIR = PROJECT_ROOT / "docs"
KB_PATH = PROJECT_ROOT / "data" / "knowledge.db"

E2E_MEETING_ID = "E2E_INTEGRATION_TEST"


def _wait_for_kb(expected: int, timeout_sec: int = 30) -> int:
    """等 KB 文档数 ≥ expected(因为 KB 入库是 bg thread 异步)

    Returns:
        最终 KB 文档数
    """
    import sqlite3
    deadline = time.time() + timeout_sec
    last_n = 0
    while time.time() < deadline:
        if KB_PATH.exists():
            conn = sqlite3.connect(str(KB_PATH))
            cur = conn.execute("SELECT COUNT(*) FROM documents")
            n = cur.fetchone()[0]
            conn.close()
            if n >= expected:
                return n
            last_n = n
        time.sleep(1)
    return last_n


def _wait_for_docs(meeting_id: str, expected_kinds: List[str], timeout_sec: int = 600) -> List[str]:
    """等 6 种 doc 全部写盘

    Returns:
        写盘的 doc_kind 列表(可能少于 expected_kinds,如果超时)
    """
    deadline = time.time() + timeout_sec
    written: List[str] = []
    meeting_dir = DOCS_DIR / meeting_id
    while time.time() < deadline:
        if meeting_dir.exists():
            written = sorted([
                f.stem for f in meeting_dir.glob("*")
                if f.is_file() and f.suffix in (".md", ".html")
            ])
            if len(written) >= len(expected_kinds):
                return written
        time.sleep(2)
    return written


class TestE2EFullChain:
    """完整链路:loopback → ASR → 6 docs → KB → UI 检索"""

    def test_01_audio_capture(self, tmp_path):
        """Step 1: 音频 loopback 捕获(可降级到 silence)"""
        from vpbuddy.loopback import capture_loopback, list_monitor_sources

        sources = list_monitor_sources()
        print(f"\n[01] monitor sources: {[s['name'] for s in sources]}")

        out = capture_loopback(
            duration_sec=3.0,
            output_path=tmp_path / "loopback.wav",
            source_name=sources[0]["name"] if sources else None,
            silence_fallback=True,
        )
        assert out.exists(), f"wav 没写盘: {out}"
        assert out.stat().st_size > 1000, f"wav 太小({out.stat().st_size}B),可能写盘失败"
        print(f"[01] ✅ wav: {out} ({out.stat().st_size} bytes)")

    def test_02_asr_transcribe(self, tmp_path):
        """Step 2: ASR 转写 — 已迁移至百炼 WS 实时模式, 待重写为 WS 版"""
        pytest.skip("gpu_transcribe 已移除, 待适配百炼 WS 实时转写")

    def test_03_meeting_state_setup(self, tmp_path):
        """Step 3: 创建 meeting state(从 ASR 结果或 mock)"""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "meeting_id": E2E_MEETING_ID,
            "title": "E2E 集成测试会议",
            "created_at": "2026-06-22T20:00:00",
            "platform": "local",
            "status": "active",
            "facts": {
                "REQ": [
                    "支持腾讯会议、钉钉、企微三平台音频 loopback 采集",
                    "本地 ASR 转写(funasr paraformer-zh)",
                    "sqlite-vec + sentence-transformers 多语言模型",
                    "向量维度 384,跨会议检索用余弦相似度",
                ],
                "GOAL": [
                    "完全本地运行,数据不上传云端",
                    "首屏 < 100ms 响应",
                ],
                "FEAT": [
                    "会议结束自动生成 6 种文档(需求/架构/任务/API/风险/演示)",
                    "Web UI 端口 8765,跨会议 RAG 检索",
                ],
                "RISK": [
                    "冷启动 256MB 模型加载慢",
                    "loopback 方案无法分离发言人",
                ],
                "QUE": [
                    "法务确认隐私政策是否需要更新",
                ],
            },
            "transcript_path": "/tmp/e2e_test_transcript.json",
        }
        state_path = DATA_DIR / f"{E2E_MEETING_ID}.json"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        assert state_path.exists()
        print(f"[03] ✅ state: {state_path}")

    def test_04_trigger_6_docs(self):
        """Step 4: 触发 6 种 doc_kind(等所有 doc 写盘)

        MiniMax-M3 8B 模型工具调用慢且不稳,允许部分成功(5/6)但必须 ≥ 4/6。
        详细:每个 doc_kind 都要看 fallback 标志,agent 真写 vs fallback 兜底。
        """
        from vpbuddy.sub_session_controller import trigger_sub_session

        DOC_KINDS = ["req", "arch", "tasks", "api", "risk", "demo"]
        results = {}
        t0 = time.time()
        for kind in DOC_KINDS:
            r = trigger_sub_session(E2E_MEETING_ID, kind)
            results[kind] = r
            print(f"  {kind:8s} triggered={r.get('triggered')} size={r.get('doc_size', 0)}B "
                  f"fallback={r.get('fallback_used', False)}")
        elapsed = time.time() - t0
        print(f"[04] trigger 耗时: {elapsed:.1f}s")

        # 验证:至少 4/6 成功(MiniMax-M3 不稳,允许部分)
        true_count = sum(1 for r in results.values() if r.get("triggered"))
        assert true_count >= 4, f"只有 {true_count}/6 triggered (期望 ≥ 4)"

        # 详细统计:agent 真写 vs fallback 兜底
        agent_written = sum(1 for r in results.values()
                          if r.get("triggered") and not r.get("fallback_used"))
        fallback_written = sum(1 for r in results.values()
                              if r.get("triggered") and r.get("fallback_used"))
        print(f"[04] 统计: agent={agent_written}, fallback={fallback_written}, "
              f"failed={6 - true_count}")

        # 验证:文件真写盘
        written = _wait_for_docs(E2E_MEETING_ID, DOC_KINDS, timeout_sec=60)
        assert len(written) >= 4, f"只有 {len(written)}/6 docs 写盘: {written}"
        print(f"[04] ✅ docs 写盘: {written}")

    def test_05_kb_storage(self):
        """Step 5: 验证 KB 入库(等 bg thread 完成)"""
        # 触发 6 docs 后,KB 会有 background 入库
        # 等 KB 文档数 >= 6
        n = _wait_for_kb(expected=6, timeout_sec=30)
        assert n >= 6, f"KB 只有 {n} 文档,期望 ≥ 6"
        print(f"[05] ✅ KB 文档数: {n}")

    def test_06_ui_search(self):
        """Step 6: 通过 UI /api/kb/search 检索(模拟浏览器调用)"""
        # 启动 UI server(在后台,如果未启动)
        # 用临时端口避免冲突
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        # 启动 server
        env = os.environ.copy()
        env["VPBUDDY_KB_DB"] = str(KB_PATH)
        env["VPBUDDY_DATA_DIR"] = str(DATA_DIR.parent / "meetings")
        env["VPBUDDY_DOCS_DIR"] = str(DOCS_DIR)
        proc = subprocess.Popen(
            ["python3", "-m", "vpbuddy.ui_server", "--port", str(port), "--host", "127.0.0.1"],
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        try:
            # 等 server ready
            for _ in range(30):
                try:
                    r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2)
                    if r.status == 200:
                        break
                except Exception:
                    time.sleep(1)
            else:
                pytest.fail(f"UI server 没起来,port {port}")

            # 检索测试
            query = "loopback 音频采集"
            url = f"http://127.0.0.1:{port}/api/kb/search?q={url_quote(query)}&top_k=5"
            r = urllib.request.urlopen(url, timeout=10)
            data = json.loads(r.read().decode())

            assert "results" in data, f"响应缺 results: {data}"
            assert len(data["results"]) > 0, f"query '{query}' 无结果"
            top = data["results"][0]
            print(f"[06] ✅ top result: {top['meeting_id']}/{top['doc_kind']} dist={top['distance']:.3f}")
            print(f"[06]    snippet: {top['snippet'][:120]}")

        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_99_cleanup(self):
        """Step 99: 清理 E2E 测试数据(可选)"""
        if os.environ.get("E2E_KEEP_DATA") == "1":
            print("[99] 保留 E2E 数据(E2E_KEEP_DATA=1)")
            return

        # 清理 docs
        meeting_docs = DOCS_DIR / E2E_MEETING_ID
        if meeting_docs.exists():
            shutil.rmtree(meeting_docs, ignore_errors=True)

        # 清理 meeting state
        state_file = DATA_DIR / f"{E2E_MEETING_ID}.json"
        if state_file.exists():
            state_file.unlink()

        # 清理 KB entries
        if KB_PATH.exists():
            import sqlite3
            conn = sqlite3.connect(str(KB_PATH))
            conn.execute("DELETE FROM documents WHERE meeting_id = ?", (E2E_MEETING_ID,))
            conn.execute("DELETE FROM vec_documents WHERE meeting_id = ?", (E2E_MEETING_ID,))
            conn.commit()
            conn.close()

        print(f"[99] ✅ 清理 {E2E_MEETING_ID} 数据")
