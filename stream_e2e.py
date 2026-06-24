"""流式 E2E 验证: 跑 stream_client 推 3 chunk + 截图 Web UI 看累积"""
import time
import re
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("/tmp/stream_screenshots")
OUT.mkdir(exist_ok=True)


def main():
    # 1. 跑 stream client 推 3 chunk (在本机直接调 GPU URL)
    print(">>> 推 3 个 30s chunk (90s 会议) ...")
    r = subprocess.run(
        ["python3", "/home/zsd/vpbuddy/stream_client.py", "/tmp/e2e_meeting.wav",
         "--gpu", "http://localhost:8765", "--chunk", "30", "--max-chunks", "3"],
        capture_output=True, text=True, timeout=300,
    )
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print("STDERR:", r.stderr[-500:])
        return

    # 提取 meeting_id
    m = re.search(r"(STREAM_\d+_\d+_[a-f0-9]+)", r.stdout)
    if not m:
        print("❌ 没拿到 meeting_id")
        return
    mid = m.group(1)
    print(f">>> meeting_id: {mid}")

    # 2. 等 90s 让 controller 跑 6 docs
    print(">>> 等 90s 让 controller 写 6 docs ...")
    time.sleep(90)

    # 3. 验证 6 docs 已生成
    docs_dir = Path(f"/home/zsd/vpbuddy/docs/{mid}")
    if docs_dir.exists():
        print(f">>> docs dir: {[f.name for f in docs_dir.rglob('*')]}")
    else:
        print(">>> docs dir 还没创建, 等更长...")

    # 4. Playwright 截图
    print(">>> Playwright 截图 ...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        # 主页
        page.goto("http://localhost:8765/", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / "01_home.png"))

        # 选 STREAM 会议
        try:
            page.select_option("#meeting-select", value=mid, timeout=5000)
        except Exception as e:
            print(f"  select_option err: {e}")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / "02_main_selected.png"))
        print("  ✓ 01_home.png, 02_main_selected.png")

        # 时间线
        page.click("text=时间线")
        page.wait_for_timeout(4000)
        page.screenshot(path=str(OUT / "03_timeline.png"))
        print("  ✓ 03_timeline.png")

        # KB
        page.click("text=知识库")
        page.wait_for_timeout(4000)
        page.locator("#kb-query").fill("步骤")
        page.click("button:has-text('检索')")
        page.wait_for_timeout(5000)
        page.screenshot(path=str(OUT / "04_kb.png"))
        print("  ✓ 04_kb.png")

        # 设置 (看 STREAM 状态)
        page.click("text=设置")
        page.wait_for_timeout(4000)
        page.screenshot(path=str(OUT / "05_settings.png"))
        print("  ✓ 05_settings.png")

        browser.close()
    print()
    print("=" * 60)
    print(f"流式 E2E 验证完成: {mid}")
    print("=" * 60)


if __name__ == "__main__":
    main()
