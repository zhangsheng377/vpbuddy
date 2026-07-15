"""Dashboard — Linear Dark 风格,集成真实 6 文档 + demo



读 PROJECT_ROOT / "docs"/{meeting_id}/ 下的 6 文件(req/arch/tasks/api/risk.md + demo/demo.html)
生成单文件 HTML,内嵌 Linear Dark 风格,5s 自动刷新
"""

import argparse
import os
import time
from pathlib import Path

# Auto-computed project root. P1#1 (2026-07-04)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 默认路径(可通过环境变量覆盖)
DOCS_DIR = Path(os.environ.get("VPBUDDY_DOCS_DIR", PROJECT_ROOT / "docs"))


DOC_KIND_META = [
    ("req",   "需求清单", "REQ"),
    ("arch",  "技术架构", "ARCH"),
    ("tasks", "任务拆解", "TASK"),
    ("api",   "API 设计", "API"),
    ("risk",  "风险分析", "RISK"),
    ("demo",  "交互 Demo", "DEMO"),
]


def read_doc(meeting_dir: Path, doc_kind: str) -> str | None:
    """读一个 doc 文件,返回 markdown 原文(无则 None)"""
    if doc_kind == "demo":
        f = meeting_dir / "demo" / "demo.html"
    else:
        f = meeting_dir / f"{doc_kind}.md"
    if f.exists():
        return f.read_text(encoding="utf-8")
    return None


def read_doc_size(meeting_dir: Path, doc_kind: str) -> tuple[int | None, float | None]:
    """读 doc 大小 + mtime"""
    if doc_kind == "demo":
        f = meeting_dir / "demo" / "demo.html"
    else:
        f = meeting_dir / f"{doc_kind}.md"
    if f.exists():
        st = f.stat()
        return st.st_size, st.st_mtime
    return None, None


def status_emoji(size: int | None) -> str:
    """根据大小返回状态"""
    if size is None:
        return "pending"
    if size < 200:
        return "idle"
    if size < 1000:
        return "generating"
    return "done"


def build_dashboard(meeting_id: str, output: Path | None = None) -> Path:
    """生成 dashboard HTML

    Args:
        meeting_id: 会议 ID
        output: 输出路径(None = 默认 {meeting_dir}/dashboard.html)
    """
    meeting_dir = DOCS_DIR / meeting_id
    if not meeting_dir.exists():
        raise FileNotFoundError(f"Meeting dir not found: {meeting_dir}")

    if output is None:
        output = meeting_dir / "dashboard.html"

    # 收集 6 doc 状态
    docs = []
    for kind, label, code in DOC_KIND_META:
        size, mtime = read_doc_size(meeting_dir, kind)
        status = status_emoji(size)
        content = read_doc(meeting_dir, kind) if status == "done" else None
        # 对非 demo, 截前 1500 字符作为 preview
        if kind != "demo" and content:
            preview = content[:1500]
            if len(content) > 1500:
                preview += f"\n\n*(还有 {len(content) - 1500} 字符...)*"
        elif kind == "demo" and content:
            # demo 不 preview, 用 iframe
            preview = None
        else:
            preview = None
        docs.append({
            "kind": kind,
            "label": label,
            "code": code,
            "size": size,
            "mtime": mtime,
            "status": status,
            "preview": preview,
            "content": content,
        })

    # demo 单独处理(用 iframe)
    demo_doc = next(d for d in docs if d["kind"] == "demo")

    # 生成 HTML(Linear Dark 风格 + 真实数据)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VPBuddy — {meeting_id} Dashboard</title>
<style>
  :root {{
    --bg-black: #08090a;
    --bg-panel: #0f1011;
    --bg-surface: #191a1b;
    --text-primary: #f7f8f8;
    --text-secondary: #d0d6e0;
    --text-muted: #8a8f98;
    --accent: #7170ff;
    --accent-bg: #5e6ad2;
    --success: #10b981;
    --warning: #f59e0b;
    --danger: #ef4444;
    --border: rgba(255,255,255,0.08);
    --border-subtle: rgba(255,255,255,0.05);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    font-family: -apple-system, "Inter", "Segoe UI", sans-serif;
    background: var(--bg-black);
    color: var(--text-primary);
    font-size: 13px; line-height: 1.6;
  }}
  body {{ padding: 24px; }}
  .header {{
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 24px; padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }}
  .header h1 {{ font-size: 18px; font-weight: 500; }}
  .header .meta {{ font-size: 11px; color: var(--text-muted); font-family: ui-monospace, "JetBrains Mono", monospace; }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 16px;
    margin-bottom: 24px;
  }}
  .card {{
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }}
  .card-header {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 16px;
    background: var(--bg-surface);
    border-bottom: 1px solid var(--border-subtle);
  }}
  .card-title {{ font-weight: 500; font-size: 13px; }}
  .card-code {{
    font-size: 10px; color: var(--text-muted);
    font-family: ui-monospace, "JetBrains Mono", monospace;
    background: rgba(255,255,255,0.04);
    padding: 2px 6px; border-radius: 3px;
  }}
  .card-body {{
    padding: 14px 16px;
    max-height: 400px;
    overflow-y: auto;
    font-size: 12px;
    line-height: 1.55;
  }}
  .card-body pre {{
    white-space: pre-wrap; word-wrap: break-word;
    font-family: ui-monospace, "JetBrains Mono", monospace;
    font-size: 11px; color: var(--text-secondary);
  }}
  .status {{
    display: inline-block;
    width: 8px; height: 8px; border-radius: 50%;
    margin-right: 6px;
  }}
  .status.done {{ background: var(--success); }}
  .status.generating {{ background: var(--warning); animation: pulse 1.5s infinite; }}
  .status.pending {{ background: var(--text-muted); }}
  .status.idle {{ background: var(--text-subtle); }}
  @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}
  .demo-card {{
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    margin-top: 16px;
  }}
  .demo-frame {{ width: 100%; height: 600px; border: none; background: white; }}
  .meta-row {{ display: flex; gap: 16px; align-items: center; }}
  .meta-item {{ font-size: 11px; color: var(--text-muted); font-family: ui-monospace, monospace; }}
  .refresh-btn {{
    background: var(--accent-bg); color: white;
    border: none; padding: 6px 14px;
    border-radius: 5px; font-size: 12px; cursor: pointer;
  }}
  .empty {{ color: var(--text-muted); font-style: italic; }}
  .full-doc-link {{ color: var(--accent); text-decoration: none; font-size: 11px; }}
  h2, h3 {{ color: var(--text-primary); margin: 12px 0 8px; font-weight: 500; }}
  h2 {{ font-size: 14px; }}
  h3 {{ font-size: 13px; }}
  ul, ol {{ padding-left: 20px; margin: 6px 0; }}
  code {{
    background: rgba(255,255,255,0.06);
    padding: 1px 4px; border-radius: 3px;
    font-family: ui-monospace, monospace; font-size: 11px;
  }}
</style>
<script>
  setTimeout(() => location.reload(), 8000);
</script>
</head>
<body>

<div class="header">
  <div>
    <h1>VPBuddy — 会议 {meeting_id}</h1>
    <div class="meta">最后刷新: <span id="now"></span> · 8s 自动刷新</div>
  </div>
  <button class="refresh-btn" onclick="location.reload()">立即刷新</button>
</div>

<div class="grid">
"""

    for doc in docs:
        if doc["kind"] == "demo":
            continue  # demo 单独
        size_str = f"{doc['size']:,}B" if doc['size'] else "—"
        mtime_str = time.strftime("%H:%M:%S", time.localtime(doc['mtime'])) if doc['mtime'] else "—"
        html += f"""
  <div class="card">
    <div class="card-header">
      <div>
        <span class="status {doc['status']}"></span>
        <span class="card-title">{doc['label']}</span>
        <span class="card-code">{doc['code']}</span>
      </div>
      <div class="meta-row">
        <span class="meta-item">{size_str}</span>
        <span class="meta-item">{mtime_str}</span>
      </div>
    </div>
    <div class="card-body">
"""
        if doc['preview']:
            # 简单 markdown → HTML(超轻量,只处理标题+列表+代码块)
            preview = doc['preview']
            for line in preview.split("\n"):
                if line.startswith("# "):
                    html += f"<h2>{line[2:]}</h2>"
                elif line.startswith("## "):
                    html += f"<h3>{line[3:]}</h3>"
                elif line.startswith("- "):
                    html += f"<div>· {line[2:]}</div>"
                elif line.startswith("```"):
                    html += "<pre>"
                else:
                    html += f"<div>{line}</div>"
            html += f'<div style="margin-top:12px;"><a class="full-doc-link" href="{doc["kind"]}.md" target="_blank">查看完整文档 →</a></div>'
        else:
            html += '<div class="empty">尚未生成...</div>'
        html += """
    </div>
  </div>
"""

    html += "</div>"  # /grid

    # Demo iframe
    if demo_doc['content']:
        # 把 demo.html 写出去,再 iframe 引用
        demo_iframe_src = "demo/demo.html"
        html += f"""
<div class="demo-card">
  <div class="card-header">
    <div>
      <span class="status done"></span>
      <span class="card-title">交互 Demo</span>
      <span class="card-code">DEMO</span>
    </div>
    <div class="meta-row">
      <span class="meta-item">{demo_doc['size']:,}B</span>
      <a class="full-doc-link" href="demo/demo.html" target="_blank">新窗口打开 →</a>
    </div>
  </div>
  <iframe class="demo-frame" src="{demo_iframe_src}"></iframe>
</div>
"""
    else:
        html += """
<div class="demo-card">
  <div class="card-header">
    <div>
      <span class="status pending"></span>
      <span class="card-title">交互 Demo</span>
      <span class="card-code">DEMO</span>
    </div>
  </div>
  <div class="card-body">
    <div class="empty">Demo 尚未生成...</div>
  </div>
</div>
"""

    html += """
<script>
  document.getElementById('now').textContent = new Date().toLocaleTimeString('zh-CN');
</script>
</body>
</html>
"""

    output.write_text(html, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description="VPBuddy dashboard generator")
    parser.add_argument("--meeting", required=True, help="会议 ID")
    parser.add_argument("--output", help="输出 HTML 路径(默认 {meeting_dir}/dashboard.html)")
    parser.add_argument("--watch", action="store_true", help="持续重建(每 10s)")
    args = parser.parse_args()

    if args.watch:
        print(f"Watching {args.meeting}...")
        while True:
            try:
                out = build_dashboard(args.meeting, Path(args.output) if args.output else None)
                print(f"  [{time.strftime('%H:%M:%S')}] {out}")
            except Exception as e:
                print(f"  ERROR: {e}")
            time.sleep(10)
    else:
        out = build_dashboard(args.meeting, Path(args.output) if args.output else None)
        print(f"✅ Generated: {out}")


if __name__ == "__main__":
    main()
