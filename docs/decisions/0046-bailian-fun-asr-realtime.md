# 百炼 Fun-ASR-Realtime 接入方案设计

> **文档状态**: 草案 · 2026-07-06
> **提出人**: 张胜东
> **相关 ADR**: 替代 #0002 (MVP-Step1-ASR 设计)、#0004 (MVP-Step3-子 session 架构之 ASR 部分)、#0005 (ModelScope 替代 HF_TOKEN)

---

## 1. 背景与动机

### 1.1 当前 ASR Pipeline

```
麦克风 PCM → 客户端 30s WAV chunk → HTTP upload → GPU 服务器
  ├── [1] FunASR paraformer-zh + fsmn-vad + ct-punc + cam++ (speaker diarization)
  │       └─ 本地 GPU 推理，~2s/chunk
  └── [2] ASR Clean: Qwen2.5-1.5B GGUF (ollama localhost)
          └─ 清除口语填充词、修正专有名词，~3s/chunk
```

**总计延迟**: 30s chunk 发出后 ~5-6s 返回转写文本。

### 1.2 当前痛点

| 痛点 | 说明 |
|------|------|
| **GPU 资源占用** | FunASR 4 模型 + Qwen GGUF 模型常驻显存 ~5GB |
| **维护成本** | ModelScope 模型下载不稳定；VAD cache 状态 bug 刚修好 (v0.15.3) |
| **说话人聚类不可靠** | cam++ 模型准确率一般，实际用处有限 |
| **双层推理** | ASR → 清洗两步走，增加延迟和维护复杂度 |

### 1.3 目标

用阿里云百炼 `fun-asr-realtime` 替代当前自建 ASR pipeline，同时评估：

- **实时识别** 能否覆盖本地 30s-chunk 模式的延迟要求
- **上下文增强** 能否取代本地 ASR Clean 模型
- 是否可以直接并发流式推音频（而非等 30s 批量发），彻底消除 chunk 积累延迟

---

## 2. 百炼 ASR 模型全景

### 2.1 三大系列

| 系列 | 代表模型 | 模式 | 核心差异 |
|------|---------|:---:|---------|
| **Fun-ASR** | `fun-asr-realtime` | 实时 WebSocket | 专用 ASR，热词 + **上下文增强** + 语义断句 |
| **Qwen-ASR** | `qwen3-asr-flash-realtime` | 实时 WebSocket | 情感识别，无热词 / 上下文增强 |
| **Qwen3.5-Omni** | `qwen3.5-omni-plus-realtime` | 实时 WebSocket | 不是 ASR，是理解音频的大模型，Prompt 上下文注入 |

### 2.2 Fun-ASR-Realtime 系列（我们的候选）

| 模型 ID | 亮点 |
|---------|------|
| `fun-asr-realtime` (latest) | **推荐**。30 语言 + 16 方言，首字 <100ms，尾字延迟低 |
| `fun-asr-realtime-2026-02-28` | 上一快照版 |
| `fun-asr-realtime-2025-11-07` | 支持上下文增强的最低版本 |
| `fun-asr-realtime-2025-09-15` | 仅中英，无上下文增强 |

### 2.3 关键能力矩阵

| 能力 | fun-asr-realtime | 我们的现状 | 覆盖？ |
|------|:---:|:---:|:---:|
| **ASR 转写** | ✅ 实时流式 | ✅ paraformer-zh 本地 | ✅ |
| **VAD / 断句** | ✅ VAD 或语义断句可选 | ✅ fsmn-vad | ✅ |
| **标点** | ✅ 内置 | ✅ ct-punc | ✅ |
| **说话人聚类** | ❌ 不支持 | ✅ cam++（不可靠） | ⚠️ 损失 |
| **热词增强** | ✅ 自定义词汇表 | ❌ 无 | ✅ 新增 |
| **上下文增强** | ✅ 领域语料注入 | ⚠️ 本地 ASR Clean | ✅ 可替代 |
| **中文方言** | ✅ 粤语/吴语/闽南/客家等 16 种 | ❌ 仅普通话 | ✅ 新增 |
| **情感识别** | ❌ | ❌ | — |

### 2.4 性能指标

| 指标 | 数据 |
|------|------|
| **首字延迟** | <100ms（话音刚落即出字） |
| **尾字延迟** | 低（长句无显著积压） |
| **中文普通话准确率** | 平均 **88.62%**（CER），接近离线模型 |
| **上下文增强效果** | 专业术语识别率提升 **15-30%**（官方数据） |
| **语义断句** | 比 VAD 断句准确度更高，适合会议场景 |

---

## 3. 方案设计

### 3.1 核心选型

```
模型: fun-asr-realtime (latest)
接口: DashScope SDK (Python) WebSocket 流式
断句: semantic_punctuation_enabled=True (语义断句，适合会议)
增强: 上下文增强 (input.context，每 chunk 传入前面的 cleaned_text)
```

### 3.2 架构变化

```
【当前】
桌面客户端 → 30s WAV chunk → HTTP → GPU Server
  → FunASR 本地推理 (~2s)
  → ASR Clean Qwen 本地推理 (~3s)
  → 返回转写结果 (~5-6s total)

【方案 A：保留 chunk 模式，本地 ASR 换成云 API】
桌面客户端 → 30s WAV chunk → HTTP → GPU Server
  → fun-asr-realtime HTTP 调用 (~1-2s 云端推理)
  → 上下文增强（自动修正）
  → 返回转写结果 (~2-3s total, 更快)

【方案 B：真实时流式推音频】⭐ 推荐
桌面客户端 → WebSocket 流式 PCM → GPU Server → 百炼 WebSocket
  → 边说边出字（首字 <100ms，实时字幕级）
  → 上下文增强（自动修正）
  → 客户端实时渲染转写结果
  → 说话结束后 ~500ms 拿到完整句子
```

### 3.3 方案 B 详解（推荐）

#### 流程图

```
桌面客户端                          GPU Server                    百炼 API
    │                                   │                            │
    │──── WebSocket 连接 ──────────────→│                            │
    │                                   │──── WebSocket run-task ──→│
    │                                   │    model=fun-asr-realtime │
    │                                   │    semantic_punctuation   │
    │                                   │    context=前文转写文本    │
    │                                   │                            │
    │───── PCM 音频帧 100ms/chunk ──────→│───── send_audio_frame ───→│
    │                                   │                            │
    │←──── on_event(text, begin_time) ──│←──── 实时识别结果 ────────│
    │                                   │                            │
    │───── PCM 音频帧 (持续...) ────────→│───── send_audio_frame ───→│
    │                                   │                            │
    │←──── on_event(sentence_end) ──────│←──── 完整句子 + 标点 ────│
    │                                   │                            │
    │  [会议结束]                        │                            │
    │───── stop ────────────────────────→│───── stop ───────────────→│
    │←──── on_complete ─────────────────│←──── 最终结果 ────────────│
```

#### 上下文增强策略

每个句子的文字累积后，作为下一段识别的上下文传入：

```python
# 伪代码
context_accumulated = ""

def on_event(result):
    sentence = result.get_sentence()
    if sentence.get("text"):
        context_accumulated += sentence["text"]

        # 每累积 3-5 个句子或 200+ 字，更新上下文
        if len(context_accumulated) > 200:
            recognition.update_context({
                "context": [{
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": context_accumulated[-500:]  # 最近 500 字
                    }]
                }]
            })
```

#### 与现有系统的集成点

| 现有组件 | 处理方式 |
|---------|---------|
| `gpu_transcribe.py` | **废弃**（不再需要本地 FunASR 推理） |
| `asr_clean.py` (Qwen GGUF) | **废弃**（上下文增强替代） |
| `fastapi_app.py` `_process_chunk_core` | **重构**：不再写 WAV 临时文件，改为 WebSocket relay |
| `stream_client.py` | **重构**：双向 relay 百炼 WebSocket ↔ 客户端 |
| 桌面客户端 | **改动较大**：从 HTTP chunk 改为 WebSocket 实时推音频流 |

### 3.4 方案 A：保持 HTTP chunk 兼容（过渡方案）

如果不想改客户端，可以保留 HTTP chunk 接口，仅在 GPU 服务器端替换 ASR 供应商：

```python
# _process_chunk_core 中
# 原: gpu_transcribe.process(tmp_path)  # 本地 FunASR + Qwen
# 新: dashscope_asr.transcribe(tmp_path, context=prev_text)  # 百炼 API
```

**优点**: 客户端零改动。
**缺点**: 仍需 30s 缓冲 → 30s 延迟；享受不到真实时的优势。

---

## 4. 能力覆盖分析

| 现有功能 | 百炼方案覆盖 | 备注 |
|---------|:---:|------|
| ASR 转写 | ✅ | 准确率 88.62% + 上下文增强提升 |
| VAD 断句 | ✅ | 语义断句（`semantic_punctuation_enabled`） |
| 标点恢复 | ✅ | 内建，不可关闭 |
| 说话人聚类 | ❌ | 百炼实时模型不支持。可后续通过非实时 `fun-asr` 离线补充 |
| ASR Clean (Qwen) | ✅ | 上下文增强 + 热词替代，不依赖本地模型 |
| 方言支持 | ✅ | 从 0 → 16 种（粤语/吴语/闽南语等） |
| 多语言 | ✅ | 中/英/日 + 30 语言 |

**唯一损失**: 说话人聚类。当前 cam++ 本身就不太可靠，属于锦上添花。

---

## 5. 延迟对比

| 阶段 | 当前 (本地) | 方案 A (百炼 HTTP) | 方案 B (百炼 WS) |
|------|:---:|:---:|:---:|
| 客户端缓冲 | 30s | 30s | 0 (实时推) |
| 网络传输 | ~0.5s | ~0.5s | ~0.1s/frame |
| ASR 推理 | ~2s (GPU) | ~1-2s (云端) | <100ms 首字 |
| ASR Clean | ~3s (Qwen) | 0 (上下文化) | 0 (上下文化) |
| **E2E 延迟** | **~35s** | **~33s** | **<500ms 首字** |

- 方案 B 的真实时体验是质的飞跃（从"等半分钟"到"话音刚落就出字"）
- 方案 A 的改善有限（省掉了本地推理时间 2s+3s），但客户端无需改动

---

## 6. 定价估算

百炼 ASR 按音频时长计费（非按字符数）。以 Fun-ASR-Realtime 为例（需确认最新价格）：

> **参考**: `fun-asr` 系列约 **0.22 元/万字符** 或按音频时长计费。一小时会议 ≈ 1 万字符 ≈ **0.22 元**。
> 
> ⚠️ 具体以百炼官方定价页为准，本文撰写时计费页面返回 404。

**对比当前方案**:
- 当前：GPU 服务器电费 + 维护成本（无直接 API 费用，但 GPU 常驻 ~5GB 显存）
- 百炼：按量付费，估算 1 小时会议 < 1 元

---

## 7. 实施路线图

### 7.1 建议分两阶段

#### Phase 1: 方案 A（1-2 天）— 最小改动验证

1. 获取百炼 API Key（北京 region，WorkspaceId）
2. 安装 `dashscope` SDK
3. 在 `_process_chunk_core` 中新增百炼调用路径（feature flag 控制）
4. 跑 E2E 验证准确率和延迟
5. 对比 `本地 FunASR+Qwen` vs `百炼 async call` 效果

#### Phase 2: 方案 B（3-5 天）— 真实时流式

1. GPU Server 新增 WebSocket relay 端点（`/api/meetings/{id}/realtime_asr`）
2. 客户端改造：从 HTTP chunk 改为 WebSocket 推音频帧
3. 上下文增强策略调优（传多少字、更新频率）
4. 长会议（1h+）的 WebSocket 稳定性测试
5. 清理废弃组件：`gpu_transcribe.py`、`asr_clean.py`

### 7.2 Feature Flag 设计

```python
# 环境变量控制
VPBUDDY_ASR_PROVIDER = os.environ.get("VPBUDDY_ASR_PROVIDER", "local")
# "local" → 当前 FunASR 本地推理 (不依赖百炼)
# "bailian" → 百炼 fun-asr-realtime
```

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 百炼 API 不可用 | ASR 完全中断 | Feature flag 保留 `local` fallback |
| 网络延迟波动 | 实时流音画不同步 | 客户端本地缓冲 200ms |
| 上下文增强不达预期 | 专有名词修正差 | 补充热词列表（每账号 10 个，每列表 500 词） |
| 说话人聚类需求回归 | 会议角色标注缺失 | 会议结束后异步用非实时 `fun-asr` + spk 模型补充 |
| 长会议 WebSocket 断连 | 转写丢失 | 自动重连 + 断点续传（客户端缓存未确认帧） |

---

## 9. 待确认事项

- [ ] **百炼 API Key**：是否已有阿里云账号？北京 region 的 WorkspaceId？
- [ ] **实时计费价格**：确认 `fun-asr-realtime` 最新单价（官方定价页 404）
- [ ] **上下文增强 5 轮限制**：是否需要更长的上下文窗口？（目前最多 5 轮，约 2000 字）
- [ ] **方案 A 还是 B**：客户端改造成本 vs 实时体验收益的权衡
- [ ] **说话人聚类**：是否必须有？如果必须有，是否接受非实时离线补充？

---

## 10. 参考文献

- [阿里云百炼 ASR 模型列表](https://help.aliyun.com/zh/model-studio/asr-model/)
- [提升识别准确率（热词 + 上下文增强）](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)
- [Fun-ASR 实时 Python SDK](https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk)
- [Fun-ASR-Realtime 升级新闻 (2026-07-06)](https://help.aliyun.com/zh/model-studio/asr-model/)
