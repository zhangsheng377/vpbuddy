# VPBuddy 端到端截图存档

`git push` 后 V 可直接 `git pull` + `open screenshots/*.png` 查看。

## 1. 完整 demo 页面(retina 全页,优先看这些)

| 文件 | 大小 | 会议 | 端到端轮次 | 关键内容 |
|---|---|---|---|---|
| **`demo_phase3_v2_full.png`** | **1.13MB** | PHASE3_MEETING_REAL | **第 4 次 ✅** | 5 段 + **3 真名**(张胜东/周华健/李丹) + VPBUDDY_DIRECT 模式说明 + 老实交代 callout |
| `demo_phase3_full.png` | 534KB | PHASE3_MEETING_REAL | 第 3 次 | 5 卡片(实况转写/协同/通知/RBAC/导出) + Mermaid 架构图 |
| `demo_phase2_full.png` | 898KB | PHASE2_AUDIO_REAL | 第 2 次 | 6 段(Pipeline/性能/8 说话人时间线/RISK+QUE callout/转写样本/交付矩阵) |
| `demo_phase3.png` | 140KB | PHASE3_MEETING_REAL | 第 3 次(小尺寸) | 同 v_full,但只截 viewport |
| `demo_phase2_audio.png` | 126KB | PHASE2_AUDIO_REAL | 第 2 次(小尺寸) | 同上,viewport 截 |

## 2. UI 主页面

| 文件 | 大小 | 内容 |
|---|---|---|
| `ui_main_full.png` | 73KB | UI 主屏全页(Linear 暗色 + 4 tab) |
| `ui_main_v2.png` | 75KB | UI 主屏 v2(retina viewport) |
| `ui_main.png` | 28KB | UI 主屏 viewport |
| `ui_timeline.png` | 158KB | UI 时间线 tab(11 条目:3 QUE + 6 RISK + 3 FEAT) |
| `ui_kb.png` | 25KB | UI 知识库 tab(跨会议 RAG 检索) |
| `ui_kb_search.png` | 28KB | UI KB 搜索"协同编辑"后 |
| `ui_meetings.png` | 28KB | UI 会议列表 |
| `dashboard.png` | 9KB | financial-data-service 仪表板(对比) |

## 3. 时间线

- **第 1 次端到端**(15:20 左右):209s 歌曲,8 speaker 误分,作为 baseline
- **第 2 次端到端**:踩坑固化(GPU 部署 6 文件 875 行入 git,commit `f8ceac0`)
- **第 3 次端到端**(15:20):150s 3 人 TTS 真会议,3 speaker 精准分出,真名占位符
- **第 4 次端到端**(15:33):**VPBUDDY_DIRECT 模式** 真重写 6 docs + 3 真名映射生效,commit `f8e42ef`

## 4. 推荐阅读顺序

1. **`demo_phase3_v2_full.png`** — 看最新版 demo(5 段 + 真名 + DIRECT 模式说明)
2. `demo_phase2_full.png` — 看歌曲 demo 对比(8 speaker 误分 → 3 真会议精准)
3. `ui_timeline.png` — 看 UI 时间线

## 5. 复现命令

```bash
# UI 主屏(端口 8765)
cd /home/zsd/vpbuddy
PYTHONPATH=src python3 -m vpbuddy.ui_server --port 8765 &

# demo 页面(本地 file://)
xdg-open docs/PHASE3_MEETING_REAL/demo/demo.html
```

## 6. 截图工具

`tools/screenshots.py` (Playwright Python,headless Chromium,viewport 1600x1100,device_scale_factor=2)
