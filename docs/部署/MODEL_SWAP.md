# VPBuddy 模型 Swap 指南

**背景**:当前 VPBuddy 默认 LLM = `MiniMax-M3`(8B class),工具调用能力弱,
经常不调 `write_file` 工具。本指南说明如何换到更强的模型。

---

## 当前状态(2026-06-22)

| 模型 | 状态 | 工具调用成功率 | 备注 |
|---|---|---|---|
| **MiniMax-M3** (默认) | 工作中 | ~50% | 经常 thinking-only,需 fallback 兜底 |
| 任何 OpenAI 兼容 API | 可换 | 取决于模型 | 见下方 swap 步骤 |

**关键代码位置**:
- `src/vpbuddy/sub_session_controller.py:99` — `model=os.environ.get("VPBUDDY_LLM_MODEL", "MiniMax-M3")`
- 触发 LLM 在 `_trigger_via_aiagent()` 函数

---

## Swap 步骤(3 步)

### 1. 设置环境变量

```bash
# 例:换到 GPT-4o
export VPBUDDY_LLM_MODEL="openai/gpt-4o"
export OPENAI_API_KEY="sk-..."

# 例:换到 Claude Sonnet(通过 OpenRouter)
export VPBUDDY_LLM_MODEL="anthropic/claude-sonnet-4"
export OPENAI_API_KEY="sk-or-..."  # OpenRouter key
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
```

### 2. 重启 controller

```bash
pkill -f vpbuddy-controller
vpbuddy-controller --start
```

### 3. 验证

```bash
# 跑一个简单 trigger 看是否调 write_file
vpbuddy trigger TEST_MEETING_001 req
ls -la docs/TEST_MEETING_001/req.md  # 应该 1-5KB,内容真实

# 跑 kb-status 看 fallback_used 是不是 False(说明 agent 自己写的)
vpbuddy kb-status
# 看日志: [meeting_id/doc_kind] fallback wrote ... → fallback_used=True
#      : [meeting_id/doc_kind] agent ...    → 没有 fallback 日志 = agent 成功
```

---

## 推荐模型(2026-06-22 评估)

| 模型 | 工具调用 | 中文 | 速度 | 成本 | 推荐度 |
|---|---|---|---|---|---|
| Claude Sonnet 4 | ★★★★★ | ★★★★★ | 中 | $$$ | ⭐⭐⭐⭐⭐ |
| GPT-4o | ★★★★★ | ★★★★ | 中 | $$$$ | ⭐⭐⭐⭐ |
| Qwen3-72B | ★★★★ | ★★★★★ | 慢 | $ | ⭐⭐⭐⭐ |
| DeepSeek-V3 | ★★★★ | ★★★★ | 快 | $ | ⭐⭐⭐⭐ |
| Llama-3.3-70B | ★★★ | ★★★ | 慢 | $$ | ⭐⭐⭐ |

**VPBuddy 推荐**:Claude Sonnet 4(工具调用最强 + 中文好),备用 Qwen3-72B(性价比)。

---

## Prompt 已强化(2026-06-22)

`ephemeral_system_prompt` 已经做了:
1. **硬性要求**:"**必须**调用 file toolset 里的 write_file 工具"
2. **明确路径**:`输出文件路径(必须写到这里):{get_doc_path(...)}`
3. **不写文件 = 任务失败**
4. **工作流示例**:`read_file(state) → 解析 facts → 生成内容 → write_file(目标路径) → 退出`

---

## Fallback 兜底(2026-06-22)

即使 prompt 强化 + 强模型,agent 仍可能不调工具。`doc_fallback.py` 提供代码生成兜底:

```python
# trigger_sub_session 6.5 步:
if not doc_path.exists():
    if os.environ.get("VPBUDDY_FALLBACK", "1") != "0":
        # 调 doc_fallback.generate_and_write 写盘
        written = generate_and_write(meeting_id, doc_kind, state, doc_path)
        result["fallback_used"] = True
```

**关闭 fallback**:`export VPBUDDY_FALLBACK=0` — 调试模式,严格只走 agent。

---

## 测试覆盖

| 测试 | 验证 |
|---|---|
| `TestTriggerWritesFile` | trigger 验证逻辑 + fallback 触发条件 |
| `test_doc_fallback.py` | 6 种 doc_kind 模板正确性 + MeetingState 转换 |
| `test_kb_status.py` | KB 状态可观测性 |

跑测试:`pytest src/tests/ -q`(GPU 端 107 passed,0 回归)。

---

## 未来:模型微调路线

如果换模型还不满意,可以微调。`doc_fallback.py` 的输出是完美的训练数据(每条都对应一个 state + doc_kind → 文档)。可以:

1. **收集数据**:跑 fallback 100+ 会议,把 (state, doc_kind, generated_doc) 三元组存到 `data/training/`
2. **fine-tune 小模型**:Qwen2.5-7B 用 LoRA 微调,工具调用成功率会大幅提升
3. **保留 fallback**:即使微调后也保留 fallback,作为最后兜底

但这是 YAGNI,先换模型看效果。
