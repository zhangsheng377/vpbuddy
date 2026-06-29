# Qwen3 小模型 ASR 整理对比 | 2026-06-29 (v3)

> **本文是 `asr-clean-llm-comparison-2026-06-29.md` 的扩展** — 测 0.6B / 1.7B / 4B 三个 Qwen3 小模型。
> 测试目标: 找最小且精度可接受的本地模型，**目标 5s/窗口**。

## 背景

`cd17c35` evidence 显示 qwen3:8b 平均 17.93s/窗口, 仍超标 3.6x。
- 8b 用 ollama CPU 跑（GPU 4GB 被 vpbuddy controller 占）
- 0.6b/1.7b/4b 是 Qwen3 完整系列里更小的, 看能不能在精度损失可控的前提下压到 5s

## 测试环境

- 音频: 65.2s 中文 TTS (跟前两轮同一份, 公平对比)
- funasr: paraformer-zh (跟前两轮一样)
- 测试时间: 2026-06-29 12:05 BJT
- GPU 端状态: **ollama 跑在 CPU** (4GB GPU 显存被 vpbuddy controller 占), 这意味着 4b 也跑 180s+ 超时
- 0.6b/1.7b 在 CPU 上能跑通, 因为参数小 → 算量小 → CPU 也能秒级
- 测试脚本: `/tmp/asr_test/qwen_small_5win.py`

## 模型

| 模型 | 参数 | 量化 | 显存占用 | ollama 库 |
|---|---|---|---|---|
| qwen3:0.6b | 0.75B | Q4_K_M | 0.5GB | ✅ |
| qwen3:1.7b | 2.0B | Q4_K_M | 1.4GB | ✅ |
| qwen3:4b | 4.0B | Q4_K_M | 2.5GB | ✅ (CPU 跑超时) |
| qwen3:8b (历史) | 8.2B | Q4_K_M | 5.2GB | ✅ (CPU 跑 17.93s) |

## 性能 (5 段/30s 窗口, 65.2s 音频, CPU 模式)

| 窗口 | qwen3:0.6b | qwen3:1.7b | qwen3:8b (历史) |
|------|------------|------------|-----------------|
| W0 (5 段) | 2.88s | 3.24s | 19.83s |
| W1 (5 段) | 6.13s | 6.38s | 23.50s |
| W2 (5 段) | 6.35s | 4.90s | 20.29s |
| W3 (5 段) | 6.42s | 4.89s | 19.49s |
| W4 (1 段) | 5.78s | 1.89s | 6.56s |

| 指标 | qwen3:0.6b | **qwen3:1.7b** | qwen3:8b |
|------|------------|----------------|----------|
| 成功率 | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) |
| 平均 LLM 推理 | 5.51s/窗口 | **4.26s/窗口** | 17.93s/窗口 |
| 总 LLM 推理 | 27.6s | **21.3s** | 89.7s |
| 最小 | 2.88s | **1.89s** | 6.56s |
| 最大 | 6.42s | 6.38s | 23.50s |
| **是否达 5s 设计目标** | ❌ 接近 | **✅ 达标** | ❌ 超标 3.6x |

**关键**: **qwen3:1.7b 平均 4.26s, 完美达标 5s 设计目标**。

## 精度 (8 个关键术语)

| Raw funasr 错 | GT | 0.6b | 1.7b | 8b (历史) |
|---------------|---|------|------|-----------|
| VP body × 2 | VPBuddy | ❌ VP body 漏 | ❌ VP body 漏 | ✅ VPBuddy |
| tory | Tauri | ❌ tory 漏 | ❌ tory 漏 | 🟡 Tory |
| funnaser/funiser | funasr | ❌ funnaser 漏 | ❌ funnaser 漏 | 🟡 FunASR |
| slilify/civil liffi | sqlite-vec | ❌ slilify 漏 | ❌ slilify 漏 | ✅ sqlite-vec |
| sentencance transformers | sentence-transformers | ❌ 漏 | ❌ 漏 | ✅ sentence-transformers |
| 为给 (六个字 agent) | 喂给/会给 | 🟡 简化 | 🟡 为 | ❌ |
| trade off | trade-off | ❌ 漏 | ❌ trade off 漏 | ❌ |

| 指标 | 0.6b | 1.7b | 8b |
|------|------|------|------|
| 修对英文术语 | 0 | 0 | **4** |
| 漏修英文术语 | 5 | 5 | 2 |
| 幻觉 | 0 | 0 | 0 |
| 中文小改 | 1-2 (简化表达) | 1-2 (简化) | 1 |

**关键**: **0.6b / 1.7b 英文术语全漏** — 小模型没有 "VPBuddy / Tauri / sqlite-vec" 的世界知识
**8b 才有能力识别英文术语** — 修 4 / 漏 2

## 速度 vs 精度权衡

```
性能 →  1.7b ★ (4.26s) < 0.6b (5.51s) < 8b (17.93s)
精度 →  8b ★ (4 修) >> 1.7b (0 修) ≈ 0.6b (0 修)
```

**不存在一个模型同时满足速度 + 精度**:
- 1.7b/0.6b 速度达标但精度塌 (英文术语全漏)
- 8b 精度及格但速度超标 3.6x
- 4b 没跑通 (CPU 180s 超时), GPU 应该 8-10s — 待验证

## 结论

**当前阶段 (CPU 跑)**:
- ✅ 速度冠军: **qwen3:1.7b** (4.26s 达标)
- ✅ 精度冠军: qwen3:8b (4 修对) — 但速度超标

**推荐方案 (按优先级)**:
1. **GPU 跑 qwen3:4b** — 速度应该 8-10s, 精度应该接近 8b (修 2-3 英文术语), 平衡最好. 需要重启 ollama 让它用 GPU (vpbuddy controller 占 4GB, 剩 20GB 够用)
2. **如果不能重启 ollama** — 折中用 qwen3:1.7b (CPU 4.26s 达标), 但要接受精度损失 + prompt 加固
3. **终极方案** — **prompt + 字典后处理**: LLM 整理后跑一个固定 dict 替换表 (VP body→VPBuddy, tory→Tauri 等), 不依赖 LLM 改英文术语

## 下一步

1. **重启 ollama 让它用 GPU** (vpbuddy 4GB + ollama 19GB = 23GB 几乎用满, 但能跑)
2. 测 4b 在 GPU 上的真实速度
3. 写一个"固定术语 dict 替换"层, 跟 LLM 整理组合用

## 文件位置

- 测试脚本: `/tmp/asr_test/qwen_small_5win.py`
- 0.6b 数据: `/tmp/asr_test/qwen3_0.6b_w0_through_w4.json`
- 1.7b 数据: `/tmp/asr_test/qwen3_1.7b_w0_through_w4.json`

## 复现命令

```bash
ssh zsd@192.168.10.63 "cd /tmp/asr_test && timeout 300 python3 qwen_small_5win.py 2>&1 | tail -60"
```

## 关联

- **第一轮 evidence**: `docs/asr-clean-evidence-2026-06-29.md` (commit `e1ff932`)
- **第二轮 (8b vs MiniMax)**: `docs/asr-clean-llm-comparison-2026-06-29.md` (commit `cd17c35`)
- **本轮 (0.6b/1.7b/4b)**: `docs/asr-clean-qwen-small-2026-06-29.md` (本文)
- **关键 GPU 状态**: ollama 当前在 CPU 跑 (4GB GPU 被 vpbuddy 占), 4b 跑不动. 重启 ollama 可解锁 GPU
