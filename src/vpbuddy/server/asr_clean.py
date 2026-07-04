#!/usr/bin/env python3
"""ASR text cleanup via LLM (P1#2 2026-07-04)"""
from __future__ import annotations

import json, os, threading, time
from typing import Any
from urllib.request import Request, urlopen

from .config import (
    ASR_CLEAN_DEFAULT_MODEL, ASR_CLEAN_WINDOW_SIZE,
    ASR_CLEAN_WINDOW_TIMEOUT_S, ASR_CLEAN_MAX_CHARS, _CLEAN_AGENT_LOCK,
)

_CLEAN_AGENT_CACHE: dict[str, Any] = {}


def _get_clean_agent(meeting_id: str):
    """Get or create ASR clean AIAgent (cached by meeting_id)."""
    if meeting_id not in _CLEAN_AGENT_CACHE:
        from run_agent import AIAgent
        sid = f"meeting:{meeting_id}:asr-clean"
        _CLEAN_AGENT_CACHE[meeting_id] = AIAgent(
            session_id=sid, enabled_toolsets=[],
        )
    return _CLEAN_AGENT_CACHE[meeting_id]


def _run_asr_clean(meeting_id: str, raw_segments: list[dict], previous_cleaned: str = "") -> str:
    """Call LLM to clean funasr ASR raw segments."""
    if not raw_segments:
        return ""
    timestamp_lines = []
    for s in raw_segments:
        start = float(s.get("start_sec", 0))
        mm = int(start // 60)
        ss = start - mm * 60
        spk = s.get("speaker_id", "UNKNOWN")
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        timestamp_lines.append(f"[{mm:02d}:{ss:04.1f}] {spk}: {txt}")
    raw_block = "\n".join(timestamp_lines)

    prompt_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts", "asr_clean.md"
    )
    try:
        with open(prompt_path, encoding="utf-8") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        system_prompt = "You are VPBuddy meeting transcription assistant."

    prev = previous_cleaned[:2000] if previous_cleaned else "(none, meeting start)"
    user_msg_lines = [
        "Please clean the following funasr ASR raw output.",
        "",
        "Previous cleaned text (for reference):",
        prev,
        "",
        "Raw funasr segments:",
        raw_block,
        "",
        "Output only the cleaned text, no markdown titles or explanations.",
    ]
    user_msg = "\n".join(user_msg_lines)

    ollama_url = os.environ.get("VPBUDDY_OLLAMA_URL", "http://localhost:11434/api/chat")
    model = ASR_CLEAN_DEFAULT_MODEL
    timeout = int(os.environ.get("VPBUDDY_CLEAN_TIMEOUT", "60"))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"num_predict": 4096, "temperature": 0.1},
    }

    result: dict[str, Any] = {"done": False, "response": None, "error": None}

    def _runner():
        try:
            req = Request(
                ollama_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result["response"] = data.get("message", {}).get("content", "")
        except Exception as e:
            result["error"] = e
        finally:
            result["done"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if result["response"]:
        return result["response"].strip()
    if result["error"]:
        print(f"[asr_clean] {result['error']}")
    return raw_block
