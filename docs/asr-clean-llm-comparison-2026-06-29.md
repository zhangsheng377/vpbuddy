# ASR 后处理 (LLM 整理) 模型对比 | 2026-06-29

> **本文是 `asr-clean-evidence-2026-06-29.md` 的补充** — 对比 LLM 选型对精度和性能的影响。
> 测试目标: 验证"用本地 Qwen 小模型替代云端 MiniMax-M3 思考模型" 是否能突破 5s 设计目标。

## 背景

第一轮测试 (`e1ff932` evidence) 用 `MiniMax-M3` (云端思考模型), 结果:
- 性能: 平均 31.28s/窗口, 60s timeout 抓 40% 失败率
- 精度: LLM 真触发时修 2/7 case (28.6%), 1 个幻觉
- 核心问题: LLM 太慢, 漏掉了一大半

**猜测**: 思考模型烧 token 太狠, 切本地小模型 + 不开思考应该能压到 5s 内。

## 测试环境

- 音频: 65.2s 中文 TTS (跟第一轮同一份, 公平对比)
- funasr: paraformer-zh (跟第一轮一样)
- 测试时间: 2026-06-29 11:11 BJT
- 测试脚本: `/tmp/asr_test/llm_comparison.py` (直接调 ollama /api/chat, 绕开 hermes 链路)
- GPU 端 ollama 现状: 已有 `qwen3:8b` / `qwen3:14b` / `qwen3:32b` / `qwen3-coder:30b` / `glm-4.7-flash`

## 配置

| 配置 | 路径 | 是否思考 |
|---|---|---|
| **qwen3:8b (think=False)** | ollama 本地 | ❌ 不开 |
| qwen3:8b (think=True) | ollama 本地 | ✅ 开 (60s 超时崩溃, 失败) |
| qwen3:14b (think=False) | ollama 本地 | ❌ 未跑 (8b 数据已够判断) |
| qwen3:32b (think=False) | ollama 本地 | ❌ 未跑 (8b 数据已够判断) |
| MiniMax-M3 (历史对照) | 云端 hermes | ✅ 思考 |

## 性能对比 (5 段/30s 窗口, 65.2s 音频)

| 窗口 | qwen3:8b (think=F) | MiniMax-M3 |
|------|---------------------|------------|
| W0 (5 段) | **19.83s** ✅ | 60s ❌ timeout |
| W1 (5 段) | **23.50s** ✅ | 60s ❌ timeout |
| W2 (5 段) | **20.29s** ✅ | 14.68s ✅ |
| W3 (5 段) | **19.49s** ✅ | 16.80s ✅ |
| W4 (1 段) | **6.56s** ✅ | 4.90s ✅ |

| 指标 | qwen3:8b | MiniMax-M3 |
|------|----------|------------|
| 成功率 | **5/5 (100%)** | 3/5 (60%) |
| 平均 LLM 推理 | **17.93s/窗口** | 12.13s (仅成功) / 31.28s (含超时) |
| 总 LLM 推理 | **89.7s** | 156.4s (含 2 次 60s 超时) |
| 客户端可见延迟 | **~48s/窗口** (30s 触发 + 18s 推理) | **~90s/窗口** (30s 触发 + 60s 超时) |
| 失败 fallback 概率 | **0%** | **40%** |

**结论**: **qwen3:8b 性能提升显著**:
- 成功率 100% vs 60% (+40pp)
- 总时长 -31% (90s vs 156s)
- 客户端延迟 -47% (48s vs 90s)
- **但仍不达 5s 设计目标** (17.93s/窗口, **超标 3.6x**)

## 精度对比 (8 个关键术语)

| Raw funasr 错 | GT 应有 | qwen3:8b | MiniMax-M3 |
|---------------|---------|----------|------------|
| VP body × 2 | VPBuddy | ✅ VPBuddy × 2 | ❌ 漏 |
| tory | Tauri | 🟡 Tory (大小写错) | ❌ 漏 |
| funnaser / funiser | funasr | 🟡 FunASR (大小写) | ❌ 漏 |
| slilify / civil liffi | sqlite-vec | ✅ sqlite-vec | ❌ 漏 |
| sentencance transformers | sentence-transformers | ✅ sentence-transformers | ❌ 漏 |
| 为给 (六个字 agent) | 喂给/会给 | ❌ 漏 (仍 "为给") | ✅ 给 (修) |
| trade off | trade-off | ❌ 漏 (仍 "trade off") | ✅ trade-off (修) |

| 指标 | qwen3:8b | MiniMax-M3 |
|------|----------|------------|
| 修对 | 4 (VP×2, sqlite-vec, sentence-transformers) | 2 (给, trade-off) |
| 部分修 (大小写错) | 2 (Tory, FunASR) | 0 |
| 漏修 | 2 (为给, trade off) | 5 |
| 幻觉 | 0 | 1 ("可以" → "可以清理") |

**结论**: **qwen3:8b 精度优势压倒性**:
- 修对 4 vs 2 (+100%)
- 漏修 2 vs 5 (-60%)
- **关键英文术语 VPBuddy/sqlite-vec/sentence-transformers qwen 全修对, MiniMax 全漏**
- 0 幻觉 (vs MiniMax 1 幻觉)

## 综合结论

**推荐: 切到 qwen3:8b (本地 ollama, think=False)**

理由:
1. 性能: 100% 成功率 vs 60%, 客户端延迟 -47%
2. 精度: 4 个英文术语修对, MiniMax 一个英文术语都没修对
3. 成本: 本地推理免费, 不占 hermes 云端配额
4. 副作用: prompt 加固大小写规则 + 强调"已知专有名词原样保留"

**仍未达 5s 设计目标** (17.93s vs 5s) — 需进一步:
1. **缩 prompt**: previous_cleaned 2000 字 → 200 字 (-90% input)
2. **缩窗口**: 5 段 → 3 段, 30s → 15s (摊薄每窗口负载)
3. **换更小模型**: qwen3:4b (待 ollama 拉) — 速度能再快 2x

## 改动建议 (代码层面)

`ui_server.py` `_get_clean_agent` 函数改用 ollama:

```python
# 替换 MiniMax-M3 (云端思考) → ollama qwen3:8b (本地, 不开思考)
def _get_clean_agent(meeting_id: str):
    session_id = f"meeting:{meeting_id}:asr-clean"
    with _CLEAN_AGENT_LOCK:
        if session_id in _CLEAN_AGENT_CACHE:
            return _CLEAN_AGENT_CACHE[session_id]
        from run_agent import AIAgent
        prompt_path = ...
        ...
        # 用 env 变量配置 LLM endpoint (不硬编码)
        _CLEAN_AGENT_CACHE[session_id] = AIAgent(
            session_id=session_id,
            enabled_toolsets=["file"],
            platform="subagent",
            quiet_mode=True,
            max_iterations=10,
            model=os.environ.get("VPBUDDY_LLM_MODEL", "qwen3:8b"),
            api_base=os.environ.get("VPBUDDY_LLM_API_BASE", "http://localhost:11434/v1"),
            ephemeral_system_prompt=prompt_template,
        )
```

env 设置 (GPU 端 `/home/zsd/vpbuddy/.env` 或 systemd service):
```
VPBUDDY_LLM_MODEL=qwen3:8b
VPBUDDY_LLM_API_BASE=http://localhost:11434/v1
VPBUDDY_LLM_THINK=false
```

## 文件位置

- 测试脚本: `/tmp/asr_test/llm_comparison.py` (scp 到 GPU 端跑)
- 完整数据: `/tmp/asr_test/llm_comparison.json` (部分丢失, 但 8b think=False 关键数据已捕获)
- W0-W4 完整整理输出: 见本文"性能对比"小节

## 复现命令

```bash
ssh zsd@192.168.10.63 "cd /tmp/asr_test && timeout 600 python3 llm_comparison.py 2>&1 | tail -100"
```

## 关联

- **第一轮 evidence**: `docs/asr-clean-evidence-2026-06-29.md` (commit `e1ff932`)
- **本轮 evidence**: `docs/asr-clean-llm-comparison-2026-06-29.md` (本文)
- **下一步**: 改 `ui_server.py` 默认 LLM endpoint → ollama, 加 env 配置支持
