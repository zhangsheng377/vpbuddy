"""rate_limit — 轻量级内存速率限制中间件.

不引入 slowapi 等外部依赖, 使用令牌桶算法 + IP 维度限流.
适用于单实例部署 (VPBuddy 当前架构).

配置:
    VPBUDDY_RATE_LIMIT_RPM  — 每分钟请求数上限 (默认 120)
    VPBUDDY_RATE_LIMIT_AUTH_RPM — 认证端点每分钟上限 (默认 10)
"""
from __future__ import annotations

import os
import time
import threading
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

# ── 配置 ──
DEFAULT_RPM = int(os.environ.get("VPBUDDY_RATE_LIMIT_RPM", "120"))
AUTH_RPM = int(os.environ.get("VPBUDDY_RATE_LIMIT_AUTH_RPM", "10"))

# ── 令牌桶 ──

class _TokenBucket:
    """单 IP 令牌桶."""
    __slots__ = ("tokens", "max_tokens", "refill_rate", "last_refill")

    def __init__(self, max_tokens: int, refill_rate: float):
        self.tokens = float(max_tokens)
        self.max_tokens = float(max_tokens)
        self.refill_rate = refill_rate  # tokens per second
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """尝试消费 1 个令牌, 成功返回 True."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """IP 维度速率限制器, 带定期清扫."""

    def __init__(self, rpm: int = DEFAULT_RPM, auth_rpm: int = AUTH_RPM):
        self._rpm = rpm
        self._auth_rpm = auth_rpm
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300.0  # 5 分钟清扫一次

    def _bucket(self, key: str, rpm: int) -> _TokenBucket:
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = _TokenBucket(
                    max_tokens=rpm,
                    refill_rate=rpm / 60.0,
                )
            return self._buckets[key]

    def _cleanup(self) -> None:
        """清理超过 2 分钟未活跃的桶."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - 120.0
        stale = [k for k, v in self._buckets.items() if v.last_refill < cutoff]
        for k in stale:
            del self._buckets[k]

    def check(self, ip: str, is_auth: bool = False) -> bool:
        """检查是否允许请求. 返回 True = 允许."""
        rpm = self._auth_rpm if is_auth else self._rpm
        key = f"{ip}:{'auth' if is_auth else 'api'}"
        bucket = self._bucket(key, rpm)
        self._cleanup()
        return bucket.consume()


# ── FastAPI 中间件 ──

class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件."""

    def __init__(self, app: FastAPI, limiter: RateLimiter | None = None):
        super().__init__(app)
        self._limiter = limiter or RateLimiter()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        # 跳过非 API 路径 (静态文件等)
        path = request.url.path
        if not path.startswith("/api") and not path.startswith("/meetings") and not path.startswith("/deliverables"):
            return await call_next(request)

        # 获取客户端 IP
        ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            ip = forwarded.split(",")[0].strip()

        # 认证端点用更严格限制
        is_auth = path.startswith("/api/auth/")

        if not self._limiter.check(ip, is_auth=is_auth):
            return JSONResponse(
                status_code=429,
                content={"error": "请求过于频繁，请稍后再试", "status": 429},
                headers={"Retry-After": "60"},
            )

        return await call_next(request)
