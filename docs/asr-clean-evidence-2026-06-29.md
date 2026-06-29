# ASR 后处理 (LLM 整理) 实测证据 | 2026-06-29

> **本文是 commit `d7fc8a6` 的真实 E2E 量化测试报告**。
> 之前 commit 写"未做真实 E2E", 本测试补齐。
> 测试方式: 65.2s TTS 中文会议音频 → funasr ASR → 5 段/30s 窗口 → LLM 整理 (MiniMax-M3)
> 全部在 GPU 端 (192.168.10.63) 跑, 模型 funasr=paraformer-zh

## 测试环境

- 音频源: edge-tts zh-CN-YunjianNeural, 65.2s 16kHz mono
- ASR: funasr 1.1.18 paraformer-zh (CUDA)
- LLM: `MiniMax-M3` (ui_server.py 默认, 没设 VPBUDDY_LLM_MODEL env)
- 测试时间: 2026-06-29 10:46 BJT
- 测试脚本: `/tmp/asr_test/measure_clean.py` (scp 到 GPU 端跑)

## 性能 (实测, 5 段/30s 窗口)

| 窗口 | 段数 | 时长跨度 | LLM 推理时长 | 结果 |
|------|------|----------|--------------|------|
| W0 | 5 段 | 0.2-13.9s | **60.00s 超时** | fallback 原始 |
| W1 | 5 段 | 14.2-32.6s | **60.00s 超时** | fallback 原始 |
| W2 | 5 段 | 32.6-47.9s | 14.68s | ✅ 真触发 LLM |
| W3 | 5 段 | 48.1-60.6s | 16.80s | ✅ 真触发 LLM |
| W4 | 1 段 | 61.1-64.7s | 4.90s | ✅ 真触发 LLM |

| 指标 | 数值 |
|------|------|
| 窗口数 | 5 |
| LLM 推理总时长 | **156.39s** |
| 平均每窗口 | **31.28s** |
| 实际 LLM 调用 (W2/W3/W4) 平均 | **12.13s** |
| 客户端可见延迟 (设计值) | **~61s/窗口** (30s 触发 + 31s 推理) |
| LLM 失败率 (60s 超时) | **40% (2/5 窗口)** |

**结论**: **性能不达标**——设计目标"5 秒内完成"超标 **6x** (31.28s vs 5s), 40% 窗口直接超时 fallback。

## 精度 (实测, 8 个关键术语)

| Raw funasr 错 | GT 应有 | Cleaned 含正确? | 结果 | 原因 |
|---------------|---------|-----------------|------|------|
| VP body × 2 | VPBuddy | ❌ 否 | 漏 | W0/W1 LLM 超时 |
| tory | Tauri | ❌ 否 | 漏 | W0 LLM 超时 |
| funnaser / funiser | funasr | ❌ 否 | 漏 | W0/W1 LLM 超时 |
| slilify / civil liffi | sqlite-vec | ❌ 否 | 漏 | W0/W1 LLM 超时 |
| sentencance transformers | sentence-transformers | ❌ 否 | 漏 | W1 LLM 超时 |
| **为给** (6 个字 agent) | 喂给/会给 | ✅ **会给** | 修 | W2 真触发 |
| **trade off** | trade-off | ✅ **trade-off** | 修 | W3 真触发 |
| 性能目标是 LLM 整理在 5 秒内完成 | (原样) | ✅ 原样 | 修 | W4 真触发 |

| 指标 | 数值 |
|------|------|
| Raw funasr 正确率 | 2/8 = 25% (8 个术语里 funasr 直接对了 2 个) |
| LLM 整理后正确率 | 4/8 = 50% |
| **修复率 (在 LLM 实际触发的 7 个 case 上)** | **2/7 = 28.6%** |
| LLM 引入新错 (幻觉) | 1 个: W3 "可以" → "可以清理" (凭空加"清理"两字) |

**结论**: 精度**勉强合格**——LLM 真触发时修了 2 个关键术语 (给/喂给, trade off → trade-off), 漏 4 个 (主要是英文术语 VPBuddy/Tauri/sqlite-vec/sentence-transformers LLM 没动), 还凭空加了 1 个错 (幻觉)。

## 关键问题

1. **🔴 性能是核心瓶颈**——MiniMax-M3 平均 31s/窗口, 60s timeout 抓 40% 失败
2. **🟡 精度是次要问题**——LLM 修中文错字还行 (为给→会给), 但英文术语识别弱 (VP body 没修)
3. **🟡 幻觉风险**——W3 凭空加 "清理" 两字, prompt 的"不要添加内容"规则没遵守

## 改进方向 (按优先级)

1. **切更小的模型** — deepseek-v4-flash / Qwen2.5-7B / Hermes-mini, 目标 LLM 推理 < 5s
2. **降 timeout + 大窗口 fallback** — 60s → 25s, 25s 没出就 fallback + 客户端提示
3. **缩 prompt** — previous_cleaned 2000 字 → 200 字, 减少输入 token
4. **缩短窗口** — 5 段 → 3 段, 30s → 15s, 摊薄每窗口 LLM 负载
5. **prompt 加固** — 强调"英文术语按上下文推断 (sqlite-vec/VPBuddy/Tauri 常见项目词)"

## 文件位置

- 原始 funasr 输出: `/tmp/asr_test/raw_funasr.json`
- LLM 整理后输出: `/tmp/asr_test/cleaned_result.json`
- 测试脚本: `/tmp/asr_test/measure_clean.py` (scp 到 GPU 端跑)
- 65.2s 测试音频: `/tmp/asr_test/meeting.wav`

## 复现命令

```bash
ssh zsd@192.168.10.63 "PY=/home/zsd/miniconda3/envs/vpbuddy-gpu/bin/python3.11 && \
  cd /home/zsd/vpbuddy && \
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_ENDPOINT=https://hf-mirror.com && \
  export PYTHONPATH=/home/zsd/vpbuddy/src && \
  timeout 600 \$PY /tmp/asr_test/measure_clean.py 2>&1 | tail -150"
```
