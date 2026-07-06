#!/usr/bin/env python3
"""ASR text cleanup via local GGUF model (llama-cpp-python) — full-meeting cleaning (v0.11.0).

取代之前版本:
- v0.10.0 (Ollama HTTP API, 已废弃)
- v0.11.0 (当前): 直接加载 GGUF, 用 llama-cpp-python 推理, 不走 Ollama

架构:
- 模块级 _LLM_CACHE (dict + Lock) 缓存单个 Llama 实例, 进程内共享
- clean_transcript() 是唯一入口, 线程安全
- 失败时自动 fallback 到原始文本拼接, 不阻塞流
"""
from __future__ import annotations

import os
import threading
from typing import Any

from llama_cpp import Llama


# ---------------------------------------------------------------------------
# 模块级模型缓存 — 进程内只加载一次 GGUF
# ---------------------------------------------------------------------------
_LLM_CACHE: dict[str, Any] = {}
_LLM_LOCK = threading.Lock()

_MODEL_PATH_ENV = "VPBUDDY_LLM_MODEL_PATH"
_DEFAULT_MODEL_PATH = (
    "/data/vpbuddy/models/models--Qwen--Qwen2.5-1.5B-Instruct-GGUF/blobs/"
    "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
)

# ---------------------------------------------------------------------------
# system prompt — 从 prompts/asr_clean.md 提取, 内联在此
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """你是 VPBuddy 会议转写整理助手 (ASR post-processor)。

## 你是谁
- VPBuddy 是一个桌面会议工具：cpal 采集系统音频 → 30s 切片 → funasr ASR 转写 → 6 类子 agent 自动生成文档
- 你的角色: 把 funasr ASR 的"乱"原始输出整理成清晰可读的中文段落
- 客户端用户在你的工作流的下游 — 看到的就是你整理后的文本
- 6 个子 session (req/arch/tasks/api/risk/demo) **直接使用** 你整理后的 segments 进行事实抽取
- 所以**你的整理质量直接影响整个系统的质量**

## 你要干什么
输入: 一段窗口 (5 段或 30s) 的 funasr ASR 原始 segments，含 [MM:SS] SPEAKER_ID: text 格式
输出: 整理后的清晰文本（修正同音字错 / 英文术语 / 合理断句）

## 输入示例
[00:12] SPEAKER_00: 我们要用 VPBuddy 这个工具, 它会转写会议
[00:18] SPEAKER_01: 嗯 VP body, 我觉得可以试一下
[00:25] SPEAKER_00: 然后用 sql lite vec 做检索
[00:32] SPEAKER_01: 嗯 好的, 我觉得这个方案可以

## 输出示例
[00:12] SPEAKER_00: 我们要用 VPBuddy 这个工具，它会转写会议
[00:18] SPEAKER_01: 嗯，VPBuddy，我觉得可以试一下
[00:25] SPEAKER_00: 然后用 sqlite-vec 做检索
[00:32] SPEAKER_01: 嗯，好的，我觉得这个方案可以

## 处理规则

### 强制修正 (优先级最高)
- 以下 funasr 常见错误识别必须**强制修正**, 不要犹豫:
  - "VP body" / "vp body" / "VP Body" → **VPBuddy**
  - "tory" / "Tory" → **Tauri** (不是 TypeScript)
  - "funnaser" / "funiser" / "FunASR" → **funasr**
  - "slilify" / "civil liffi" / "sql lite vec" → **sqlite-vec**
  - "sentencance transformers" / "sentence transformers" → **sentence-transformers**
  - 同音字错: "速据" → "数据" / "厉史" → "历史" / "不会传传任" → "不会上传"
  - 中英混断句: 英文术语后加合理空格或中文逗号
- **不要写"修正说明"** — 直接改, 不要解释

## 输出格式
每行: [MM:SS] SPEAKER_ID: 整理后的中文文本
- 严格保持 [时间戳] 和 SPEAKER_ID 原样
- 整理后的 text 字段直接输出，不要带引号或前缀

### 噪声过滤
- **意义不明的 ASR 噪声**（回声残留 / 半句话 / 孤立零碎词 / 无意义重复）— **直接删除**
- **保留有内容的发言**（即使很短但语义明确）— 照常保留整理
- **"嗯"、"好的"、"对"** 等短反馈 — 保留不删（它们是对话的一部分）

### 边界情况
- 如果输入只有 1 段且是明显噪声 / 无意义: **直接删除**（输出空行）
- 如果输入跨多个说话人: 按 speaker 分段输出
- 如果输入太短 (< 10 字): 用上下文判断, 是延续上一次发言则保留, 否则删除"""


def _get_llm() -> Llama:
    """获取或初始化缓存的 Llama 实例 (线程安全)。"""
    model_path = os.environ.get(_MODEL_PATH_ENV, _DEFAULT_MODEL_PATH)

    with _LLM_LOCK:
        cached = _LLM_CACHE.get("llm")
        if cached is not None:
            return cached

        # 首次加载
        print(f"[asr_clean] 首次加载 GGUF 模型: {model_path}")
        llm = Llama(
            model_path=model_path,
            n_ctx=8192,  # 上下文长度, 匹配 max_tokens
            n_gpu_layers=-1,  # 尽量用 GPU (如果编译了 CUDA / Metal)
            verbose=False,  # 生产环境不打印加载细节
        )
        _LLM_CACHE["llm"] = llm
        print("[asr_clean] GGUF 模型加载完成")
        return llm


def clean_transcript(
    segments: list[dict],
    timeout: int = 120,
) -> str:
    """对一段转录 segments 做增量 LLM 清洗, 返回仅本次 segments 的 cleaned text.

    Args:
        segments: funasr ASR 输出的 segments 列表, 每个含 start_sec, speaker_id, text
        timeout: LLM 调用超时 (秒), 默认 120

    Returns:
        清洗后的文本。失败时返回原始拼接 (fallback, 不阻塞流程)。
        调用方负责拼接到 state.cleaned_text。
    """
    if not segments:
        return ""

    # 1. 格式化为带时间戳的行
    timestamp_lines = []
    for s in segments:
        start = float(s.get("start_sec", 0))
        mm = int(start // 60)
        ss = start - mm * 60
        spk = s.get("speaker_id", "UNKNOWN")
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        timestamp_lines.append(f"[{mm:02d}:{ss:04.1f}] {spk}: {txt}")
    raw_block = "\n".join(timestamp_lines)

    # 2. 构造 user message: 仅含本次待清洗 segments
    user_msg_lines = [
        "请整理下面这段 funasr ASR 原始输出。",
        "",
        "原始 ASR segments:",
        raw_block,
        "",
        "【输出要求】",
        "- 修正 funasr 常见错误 (同音字、英文术语)",
        "- 删除噪声/无意义内容",
        "- 保留说话人标记和时间戳",
        "- 只输出清洗后的文本, 不要任何解释或 markdown 标题",
    ]
    user_msg = "\n".join(user_msg_lines)

    # 3. 调用 llama-cpp-python
    try:
        llm = _get_llm()
        response = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=8192,
        )
        cleaned = response["choices"][0]["message"]["content"].strip()
        if cleaned:
            return cleaned
        else:
            print("[asr_clean/clean_transcript] LLM 返回空内容, fallback 到原始拼接")
    except Exception as e:
        print(f"[asr_clean/clean_transcript] LLM 调用失败: {e}")

    # 失败时 fallback: 返回原始拼接
    print(f"[asr_clean/clean_transcript] fallback 到原始拼接 ({len(raw_block)} chars)")
    return raw_block
