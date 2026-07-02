"""e2e — demo 版本号 + 切换 (用户需求 #6).

跑法: RUN_E2E=1 pytest src/tests/e2e/test_demo_version.py -v -m e2e

测什么:
- UI: 切到 demo panel, 见 demo-version-select 下拉 (默认 "暂无版本")
- 端到端: 预先在 GPU 端为某 meeting 创建 2 个 demo version (v1, v2) fixture →
  vite UI 切到 demo panel → 自动 loadDemoVersions() → 下拉填 v1, v2
- UI: 选 v1 → iframe src 切到 demo_v1.html
- UI: 选 v2 → iframe src 切到 demo_v2.html
- 端到端: GPU 上 GET /docs/{mid}/demo_v{N}.html 200 (静态文件)

不测什么:
- demo 内容生成 (LLM 强相关, 已在 test_demo_version.py unit 测)
- demo manifest 写盘 (unit 测)
"""
from __future__ import annotations

import json
import shlex
import time
import urllib.parse
import urllib.request
import subprocess

import pytest


pytestmark = pytest.mark.e2e


# === Helpers ===

def _ssh_run(cmd: str, timeout: int = 10) -> str:
    """在 GPU 端跑命令, 返 stdout.

    ssh 直接接 cmd (positional arg), ssh 端走 $SHELL -c 跑.
    不走 shell=True (避免 shlex 二次转义破坏 multi-line string + heredoc).
    """
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "zsd@192.168.10.63", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout + ("\n[STDERR] " + result.stderr if result.stderr else "")


def _write_demo_fixture(meeting_id: str, version: int, html_body: str):
    """SSH 写 fixture: docs/{mid}/demo_v{N}.html + demo_manifest.json (append to existing).

    注意: 不能覆盖式写 manifest, 不然 2 个 version 测试会只看到 1 个.
    先读现有 manifest, append 当前 v, 再写回.
    """
    docs_dir = "/home/zsd/vpbuddy/docs"
    meeting_dir = f"{docs_dir}/{meeting_id}"
    version_file = f"{meeting_dir}/demo_v{version}.html"
    manifest_file = f"{meeting_dir}/demo_manifest.json"
    symlink = f"{meeting_dir}/demo_latest.html"

    # mkdir
    out = _ssh_run(f"mkdir -p {shlex.quote(meeting_dir)}", timeout=5)
    if out.strip():
        print(f"[fixture] mkdir out: {out!r}")
    # write demo_v{N}.html (overwrite OK, 每次都写当前 v 内容)
    out = _ssh_run(
        f"cat > {shlex.quote(version_file)} <<'HTMLEOF'\n{html_body}\nHTMLEOF",
        timeout=5,
    )
    if out.strip():
        print(f"[fixture] write html out: {out!r}")
    # 读现有 manifest (如果存在), append 当前 v, 写回
    # 用 ssh python 调, 不在主机写脚本
    new_entry = json.dumps({
        "version": version,
        "created_at": "2026-07-02T05:00:00+00:00",
        "summary": f"e2e fixture demo v{version}",
        "file": f"demo_v{version}.html",
        "file_size": len(html_body),
    }, ensure_ascii=False)
    python_cmd = f"""
import json, os
mf = {manifest_file!r}
if os.path.exists(mf):
    with open(mf) as f:
        m = json.load(f)
else:
    m = []
m = [x for x in m if x.get('version') != {version}]
m.append({new_entry})
with open(mf, 'w') as f:
    json.dump(m, f, ensure_ascii=False, indent=2)
print('manifest now has', len(m), 'versions')
"""
    out = _ssh_run(
        f"/home/zsd/miniconda3/envs/vpbuddy-gpu/bin/python -c {shlex.quote(python_cmd)}",
        timeout=10,
    )
    if out.strip():
        print(f"[fixture] python out: {out!r}")
    # 软链 demo_latest.html → 当前 v (符合 _update_latest_symlink 行为)
    _ssh_run(
        f"cd {shlex.quote(meeting_dir)} && rm -f demo_latest.html && ln -s demo_v{version}.html demo_latest.html",
        timeout=5,
    )


def _remove_demo_fixture(meeting_id: str):
    """清理: SSH 删 docs/{mid} 目录."""
    _ssh_run(f"rm -rf /home/zsd/vpbuddy/docs/{meeting_id}", timeout=5)


@pytest.fixture
def demo_meeting(gpu_server):
    """在 GPU 上准备一个含 2 个 demo version 的 meeting. yield meeting_id, 完事清理."""
    ts = int(time.time_ns())
    mid = f"e2e_demo_{ts}"
    _write_demo_fixture(
        mid, 1,
        f"<!DOCTYPE html><html><body><h1>Demo v1 (e2e fixture {ts})</h1>"
        f"<p>First version content. Meeting {mid}.</p></body></html>",
    )
    _write_demo_fixture(
        mid, 2,
        f"<!DOCTYPE html><html><body><h1>Demo v2 (e2e fixture {ts})</h1>"
        f"<p>Second version content. Meeting {mid}.</p></body></html>",
    )
    yield mid
    _remove_demo_fixture(mid)


# === Tests ===

def test_get_demo_versions_via_server(gpu_server, demo_meeting):
    """端到端: GPU server GET /api/meetings/{mid}/demo/versions 返 v1, v2 列表."""
    mid = demo_meeting
    with urllib.request.urlopen(f"{gpu_server}/api/meetings/{urllib.parse.quote(mid)}/demo/versions", timeout=5) as r:
        body = json.loads(r.read())
    assert r.status == 200
    assert body.get("meeting_id") == mid
    assert body.get("count") == 2
    versions = body.get("versions", [])
    # 顺序: 倒序, 最新在前
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1
    # 必填字段
    for v in versions:
        assert v.get("file") == f"demo_v{v['version']}.html"
        assert v.get("file_size", 0) > 0
        assert "summary" in v
        assert "created_at" in v


def test_static_demo_html_served(gpu_server, demo_meeting):
    """端到端: GPU server GET /docs/{mid}/demo_v{N}.html 200 + 内容正确."""
    mid = demo_meeting
    for n in (1, 2):
        with urllib.request.urlopen(
            f"{gpu_server}/docs/{urllib.parse.quote(mid)}/demo_v{n}.html", timeout=5
        ) as r:
            content = r.read().decode("utf-8")
        assert r.status == 200
        assert f"Demo v{n}" in content
        assert f"Meeting {mid}" in content


class TestDemoVersionUI:
    """vite UI 切到 demo panel + 加载版本 + 切换 iframe src."""

    def test_demo_panel_has_version_select(self, page):
        """demo panel: 下拉 + iframe + 跳到最新按钮."""
        page.locator('.bottom-nav button[data-panel="demo"]').click()
        page.wait_for_function(
            "() => document.getElementById('panel-demo').classList.contains('active')",
            timeout=3000,
        )
        assert page.locator("#demo-version-select").count() == 1
        assert page.locator("#demo-iframe").count() == 1
        # 跳到最新按钮
        assert page.locator("#demo-latest-btn").count() == 1

    def test_demo_version_select_populated_from_server(self, page, gpu_server, demo_meeting):
        """端到端: 切到 demo panel + 触发 loadDemoVersions → 下拉填 v1 v2."""
        mid = demo_meeting

        # 1. 选会议 (前 e2e 验证过的)
        page.locator("#meeting-new").fill(mid)
        page.locator("#meeting-new").dispatch_event("input")
        page.locator("#btn-rec").click()
        page.wait_for_function(
            "() => document.getElementById('btn-rec').dataset.state === 'recording'",
            timeout=5000,
        )

        # 2. 切到 demo panel
        page.locator('.bottom-nav button[data-panel="demo"]').click()
        page.wait_for_function(
            "() => document.getElementById('panel-demo').classList.contains('active')",
            timeout=3000,
        )

        # 3. 等 loadDemoVersions() 真 fetch + 填下拉
        #    loadDemoVersions 在 start_capture 阶段也跑过, 但 panel 切换会重 trigger
        # 等选项数量 >= 2 (1 placeholder + 2 versions)
        try:
            # 1 placeholder + 2 versions = 3 option; 但 renderDemoVersionSelect 在 demoVersions
            # 非空时**不**加 placeholder (line 297-301), 所以只 2 options
            page.wait_for_function(
                "() => document.querySelectorAll('#demo-version-select option').length >= 2",
                timeout=10000,
            )
        except Exception:
            cur_options = page.evaluate("() => Array.from(document.querySelectorAll('#demo-version-select option')).map(o => o.value + ':' + o.textContent)")
            print(f"[debug] option count = {len(cur_options)}, options = {cur_options}")
            raise

        # 4. 验下拉内容
        options = page.locator("#demo-version-select option").all_text_contents()
        option_values = [
            page.locator("#demo-version-select option").nth(i).get_attribute("value")
            for i in range(page.locator("#demo-version-select option").count())
        ]
        # 应有 v1 + v2 两个值
        assert "1" in option_values, f"v1 缺: values={option_values}"
        assert "2" in option_values, f"v2 缺: values={option_values}"
        # 任何 option text 应有版本号
        assert any("v1" in o.lower() for o in options), f"v1 文本缺: {options}"
        assert any("v2" in o.lower() for o in options), f"v2 文本缺: {options}"

        # 5. 默认应选最新 (loadDemoVersions 末尾有 auto-select latest)
        current = page.locator("#demo-version-select").input_value()
        assert current == "2", f"默认应选最新 v2, 实际: {current}"

    def test_demo_switch_version_changes_iframe_src(self, page, gpu_server, demo_meeting):
        """端到端: 选 v1 → iframe src 切到 demo_v1.html, 选 v2 → demo_v2.html."""
        mid = demo_meeting

        # 1. 选会议
        page.locator("#meeting-new").fill(mid)
        page.locator("#meeting-new").dispatch_event("input")
        page.locator("#btn-rec").click()
        page.wait_for_function(
            "() => document.getElementById('btn-rec').dataset.state === 'recording'",
            timeout=5000,
        )

        # 2. 切到 demo panel
        page.locator('.bottom-nav button[data-panel="demo"]').click()
        page.wait_for_function(
            "() => document.getElementById('panel-demo').classList.contains('active')",
            timeout=3000,
        )

        # 3. 等下拉填好 (renderDemoVersionSelect 填 2 options, 不含 placeholder)
        page.wait_for_function(
            "() => document.querySelectorAll('#demo-version-select option').length >= 2",
            timeout=10000,
        )

        # 4. 选 v1 (通过 select_option 触发 change event)
        page.locator("#demo-version-select").select_option("1")
        # 等 iframe src 切到 demo_v1.html
        page.wait_for_function(
            f"() => document.getElementById('demo-iframe').src.includes('demo_v1.html')",
            timeout=3000,
        )
        v1_src = page.locator("#demo-iframe").get_attribute("src") or ""
        assert "demo_v1.html" in v1_src, f"iframe src 应含 demo_v1.html, 实际: {v1_src}"
        assert mid in v1_src, f"iframe src 应含 meeting_id {mid}, 实际: {v1_src}"

        # 5. 选 v2
        page.locator("#demo-version-select").select_option("2")
        page.wait_for_function(
            f"() => document.getElementById('demo-iframe').src.includes('demo_v2.html')",
            timeout=3000,
        )
        v2_src = page.locator("#demo-iframe").get_attribute("src") or ""
        assert "demo_v2.html" in v2_src, f"iframe src 应含 demo_v2.html, 实际: {v2_src}"

        # 6. 跳到最新按钮
        page.locator("#demo-latest-btn").click()
        page.wait_for_function(
            f"() => document.getElementById('demo-version-select').value === '2'",
            timeout=3000,
        )
        # 同时 iframe src 应也是 v2
        latest_src = page.locator("#demo-iframe").get_attribute("src") or ""
        assert "demo_v2.html" in latest_src
