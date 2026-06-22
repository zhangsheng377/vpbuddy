"""VPBuddy UI Server — 4 窗口 shell 的后端 API

提供:
- GET /                     → UI shell
- GET /docs/*               → 静态文档
- GET /api/meetings         → 会议列表
- GET /api/timeline         → 全部累积项按时间倒序
- GET /api/kb/search?q=     → 跨会议 RAG 检索
- GET /api/status           → Controller + 数据状态

用法: python -m vpbuddy.ui_server [--port 8765]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from typing import List, Optional
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 默认路径(可通过环境变量覆盖)
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", "/home/zsd/vpbuddy/docs"))
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", "/home/zsd/vpbuddy/data/meetings"))
UI_DIR = Path(os.environ.get("VPBUDDY_UI_DIR", "/home/zsd/vpbuddy/ui"))
KB_PATH = Path(os.environ.get("VPBUDDY_KB_DB", "/home/zsd/vpbuddy/data/knowledge.db"))
CONTROLLER_PID_FILE = Path("/tmp/vpbuddy_controller.pid")
CONTROLLER_LOG = Path("/tmp/vpbuddy_controller.log")


def list_meetings() -> list[dict]:
    """列出所有会议 + 统计"""
    if not DATA_DIR.exists():
        return []
    out = []
    for f in sorted(DATA_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            item_count = sum(len(data.get(k, [])) for k in
                             ["requirements", "goals", "features", "risks", "open_questions"])
            out.append({
                "meeting_id": data.get("meeting_id", f.stem),
                "platform": data.get("platform", "unknown"),
                "project_name": data.get("project_name"),
                "started_at": data.get("started_at"),
                "last_updated": data.get("last_updated"),
                "item_count": item_count,
            })
        except Exception:
            continue
    return out


def get_timeline() -> list[dict]:
    """全部累积项按 created_at 倒序(时间线)"""
    events = []
    if not DATA_DIR.exists():
        return []
    for f in DATA_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            mid = data.get("meeting_id", f.stem)
            for kind_key, kind_label in [
                ("requirements", "REQ"), ("goals", "GOAL"),
                ("features", "FEAT"), ("risks", "RISK"),
                ("open_questions", "QUE"),
            ]:
                for item in data.get(kind_key, []):
                    events.append({
                        "meeting_id": mid,
                        "kind": kind_label,
                        "id": item.get("id", "?"),
                        "text": item.get("text", ""),
                        "priority": item.get("priority", "?"),
                        "status": item.get("status", "?"),
                        "created_at": item.get("created_at"),
                        "speaker_name": item.get("speaker_name"),
                    })
        except Exception:
            continue
    events.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return events


def search_kb(query: str, top_k: int = 5) -> list[dict]:
    """跨会议 RAG 检索"""
    if not KB_PATH.exists() or not query.strip():
        return []
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from .knowledge_base import KnowledgeBase
        kb = KnowledgeBase(db_path=str(KB_PATH))
        results = kb.search(query, top_k=top_k)
        kb.close()
        return results
    except Exception as e:
        return [{"error": str(e)}]


def get_status() -> dict:
    """Controller + 数据状态"""
    # Controller 状态
    controller = {
        "running": False,
        "pid": None,
        "poll_interval": os.environ.get("VPBUDDY_POLL_INTERVAL", "30"),
        "last_log": None,
    }
    if CONTROLLER_PID_FILE.exists():
        pid = CONTROLLER_PID_FILE.read_text().strip()
        try:
            os.kill(int(pid), 0)
            controller["running"] = True
            controller["pid"] = pid
        except (OSError, ValueError):
            pass
    if CONTROLLER_LOG.exists():
        try:
            # 取最后一行
            with open(CONTROLLER_LOG) as f:
                lines = f.readlines()
                for line in reversed(lines):
                    if line.strip() and "Loading weights" not in line:
                        controller["last_log"] = line.strip()[:200]
                        break
        except Exception:
            pass

    # 数据统计
    meetings = list_meetings()
    total_docs = 0
    if DOCS_DIR.exists():
        for d in DOCS_DIR.iterdir():
            if d.is_dir() and d.name not in ("decisions", "research"):
                for f in d.rglob("*.md"):
                    total_docs += 1
                for f in d.rglob("*.html"):
                    total_docs += 1

    kb_docs = 0
    kb_failed = 0
    if KB_PATH.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(KB_PATH))
            cur = conn.execute("SELECT COUNT(*) FROM documents")
            kb_docs = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass
    # 2026-06-22 加 failed 计数 (sub_session_controller 的 _KB_STATUS)
    try:
        from .sub_session_controller import get_kb_status
        kb_failed = get_kb_status().get("summary", {}).get("failed", 0)
    except Exception:
        pass

    return {
        "controller": controller,
        "stats": {
            "active_meetings": len(meetings),
            "total_docs": total_docs,
            "kb_docs": kb_docs,
            "kb_failed": kb_failed,
        },
        "paths": {
            "data_dir": str(DATA_DIR),
            "docs_dir": str(DOCS_DIR),
            "kb_path": str(KB_PATH),
            "ui_dir": str(UI_DIR),
        },
        "meetings": meetings[:5],  # 最近 5 个
    }


# === HTTP Handler ===
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """安静点(不打印每次请求)"""
        pass

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        params = parse_qs(url.query)

        # 根 → UI shell
        if path == "/" or path == "/index.html":
            return self._serve_file(UI_DIR / "index.html", "text/html")

        # 静态文档
        if path.startswith("/docs/"):
            rel = path[6:]  # 去掉 /docs/
            f = DOCS_DIR / rel
            if f.is_file():
                mime = "text/html" if f.suffix == ".html" else "text/markdown"
                return self._serve_file(f, mime)
            return self._404(f"docs/{rel}")

        # API: meetings
        if path == "/api/meetings":
            meetings = list_meetings()
            return self._json({"meetings": meetings, "count": len(meetings)})

        # API: timeline
        if path == "/api/timeline":
            events = get_timeline()
            return self._json({"events": events, "count": len(events)})

        # API: kb search
        if path == "/api/kb/search":
            q = params.get("q", [""])[0]
            top_k = int(params.get("top_k", ["5"])[0])
            if not q.strip():
                return self._json({"query": "", "results": []})
            results = search_kb(q, top_k=top_k)
            return self._json({"query": q, "results": results, "count": len(results)})

        # API: kb status (2026-06-22 — 跨会议 KB 写入状态)
        if path == "/api/kb/status":
            from .sub_session_controller import get_kb_status
            meeting_id = params.get("meeting_id", [None])[0]
            return self._json(get_kb_status(meeting_id=meeting_id))

        # API: status
        if path == "/api/status":
            return self._json(get_status())

        return self._404(path)

    def _serve_file(self, path: Path, mime: str):
        try:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._500(str(e))

    def _json(self, obj):
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _404(self, what):
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"404 Not Found: {what}".encode("utf-8"))

    def _500(self, msg):
        self.send_response(500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"500: {msg}".encode("utf-8"))


def main(argv: Optional[List[str]] = None) -> int:
    """UI server 主入口 — `python -m vpbuddy.ui_server` 或 `vpbuddy ui`"""
    parser = argparse.ArgumentParser(description="VPBuddy UI server")
    parser.add_argument("--port", type=int, default=8765, help="端口(默认 8765)")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址(默认 0.0.0.0)")
    args = parser.parse_args(argv)

    # KB embedding 模型首次加载慢,启动时预热
    if KB_PATH.exists():
        try:
            print(f"预热 KB embedding 模型...", flush=True)
            from .knowledge_base import KnowledgeBase
            kb = KnowledgeBase(db_path=str(KB_PATH))
            _ = kb._get_model()  # 触发加载
            kb.close()
            print(f"✅ KB 模型预热完成", flush=True)
        except Exception as e:
            print(f"⚠️ KB 预热失败(忽略): {e}", flush=True)

    print(f"🚀 VPBuddy UI server 启动", flush=True)
    print(f"   UI:    http://{args.host}:{args.port}/", flush=True)
    print(f"   DATA:  {DATA_DIR}", flush=True)
    print(f"   DOCS:  {DOCS_DIR}", flush=True)
    print(f"   KB:    {KB_PATH}", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 退出", flush=True)
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
