"""图片视觉分析工具 — agent 读取图片内容 (v0.22.6)

调用 MiniMax 多模态 API (fallback: 百炼 DashScope qwen-vl),
agent 通过 terminal 调:
    python -c "from vpbuddy.tools.vision_analyze import analyze; print(analyze('/path/to/image.jpg'))"

接口:
    analyze(file_path: str, question: str = "") -> dict
        返回 {"ok": bool, "text": str, "error"?: str}
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _guess_mime(path: str) -> str:
    ext = Path(path).suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return mapping.get(ext, "image/png")


def analyze(file_path: str, question: str = "") -> dict:
    if not file_path:
        return {"ok": False, "error": "file_path 必填"}
    p = Path(file_path)
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {file_path}"}
    if p.stat().st_size > 5 * 1024 * 1024:
        return {"ok": False, "error": "图片超过 5MB, 拒绝分析"}

    try:
        file_data = p.read_bytes()
        mime = _guess_mime(file_path)
        b64 = base64.b64encode(file_data).decode("ascii")

        prompt = question or "请详细描述这张图片的内容，提取所有可识别的文字信息，包括标题、按钮、表单字段、表格数据等。"

        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.minimax.chat/v1")
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY") or ""

        if not api_key:
            return {"ok": False, "error": "未配置 OPENAI_API_KEY / MINIMAX_API_KEY"}

        import requests as _requests
        resp = _requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("MODEL", "minimax-m3"),
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mime};base64,{b64}"
                        }},
                    ],
                }],
                "max_tokens": 2000,
            },
            timeout=60,
        )

        if resp.status_code != 200:
            return {"ok": False, "error": f"Vision API 返回 {resp.status_code}: {resp.text[:200]}"}

        text = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        return {"ok": True, "text": text[:3000]}

    except Exception as e:
        logger.warning("vision_analyze 失败: %s", e)
        return {"ok": False, "error": f"视觉分析失败: {str(e)[:200]}"}


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m vpbuddy.tools.vision_analyze <文件路径> [问题]")
        sys.exit(2)
    out = analyze(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
