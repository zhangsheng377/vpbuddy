#!/usr/bin/env python3
"""ASR text cleanup via LLM — full-meeting cleaning (v0.10.0).

取代旧的 windowed 版本。新版本:
- 接受全量 transcript segments (整场会议, 不是按窗口)
- 格式化为带时间戳的行
- 调用 Ollama (qwen3:8b) 时传入 FULL history (cleaned_text from state + new raw segments)
- 返回完整的 cleaned text
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any
from urllib.request import Request, urlopen


def clean_transcript(
    segments: list[dict],
    previous_cleaned: str = "",
    timeout: int = 120,
) -> str:
    """对一段 (或整场) 转录 segments 做 LLM 清洗, 返回完整 cleaned text.

    Args:
        segments: funasr ASR 输出的 segments 列表, 每个含 start_sec, speaker_id, text
        previous_cleaned: 已有的 cleaned_text (来自 MeetingState), 空字符串表示首次运行
        timeout: LLM 调用超时 (秒), 默认 120

    Returns:
        清洗后的完整文本。失败时返回原始拼接 (fallback, 不阻塞流程)。
    """
    if not segments:
        return previous_cleaned  # 无新段, 原样返回已有 cleaned

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

    # 2. 加载 prompt 模板
    prompt_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "prompts", "asr_clean.md"
    )
    try:
        with open(prompt_path, encoding="utf-8") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        system_prompt = "你是 VPBuddy 会议转写整理助手。"

    # 3. 构造 user message: 包含完整历史 + 新原始段
    prev_text = previous_cleaned if previous_cleaned else "(无历史, 会议开始)"
    user_msg_lines = [
        "请整理下面这段 funasr ASR 原始输出。",
        "",
        "之前已清洗的全文 (供上下文参考):",
        prev_text,
        "",
        "当前新增的原始 ASR segments:",
        raw_block,
        "",
        "【输出要求】",
        "- 整合已有清洗文本和新增原始文本, 输出一份**完整的清洗后全文**",
        "- 修正 funasr 常见错误 (同音字、英文术语)",
        "- 删除噪声/无意义内容",
        "- 保留说话人标记和时间戳",
        "- 只输出清洗后的文本, 不要任何解释或 markdown 标题",
    ]
    user_msg = "\n".join(user_msg_lines)

    # 4. 调用 Ollama /api/chat
    ollama_url = os.environ.get(
        "VPBUDDY_OLLAMA_URL", "http://localhost:11434/api/chat"
    )
    model = os.environ.get("VPBUDDY_CLEAN_MODEL", "qwen3:8b")
    actual_timeout = timeout

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"num_predict": 8192, "temperature": 0.1},
    }

    holder: dict[str, Any] = {"done": False, "response": None, "error": None}

    def _runner():
        try:
            req = Request(
                ollama_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=actual_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                holder["response"] = data.get("message", {}).get("content", "")
        except Exception as e:
            holder["error"] = e
        finally:
            holder["done"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=actual_timeout + 5)

    if holder["response"]:
        return holder["response"].strip()
    if holder["error"]:
        print(f"[asr_clean/clean_transcript] LLM 调用失败: {holder['error']}")

    # 失败时 fallback: 拼接到 previous_cleaned 后
    fallback = previous_cleaned
    if previous_cleaned and raw_block:
        fallback += "\n" + raw_block
    elif not previous_cleaned:
        fallback = raw_block
    print(f"[asr_clean/clean_transcript] fallback 到原始拼接 ({len(fallback)} chars)")
    return fallback
