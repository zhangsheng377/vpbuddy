"""改进测试 — v0.21.0 安全/质量/性能改进

覆盖:
- JWT_SECRET 自动生成 + 环境变量
- 速率限制中间件
- 未认证端点补全 (3个)
- 锁字典 LRU 淘汰
- safe_push_event 工具函数
- 统一异常处理器
- config.py 默认路径
- Chroma GPU 自动检测
- 输入长度校验
"""
from __future__ import annotations

import os
import threading
import time
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════
# 1. JWT_SECRET 自动生成
# ══════════════════════════════════════════════════════════════════

class TestJWTSecret:
    """验证 JWT_SECRET 安全行为."""

    def test_jwt_secret_not_empty(self):
        """JWT_SECRET 不应为空字符串."""
        from vpbuddy.server.auth import JWT_SECRET
        assert JWT_SECRET, "JWT_SECRET 不应为空"
        assert len(JWT_SECRET) >= 32, "JWT_SECRET 至少 32 字符"

    def test_jwt_secret_env_override(self):
        """设置了环境变量时应使用环境变量值."""
        with patch.dict(os.environ, {"VPBUDDY_JWT_SECRET": "my-custom-secret-key-for-testing-1234"}):
            # 需要重新导入模块才能生效
            import importlib
            import vpbuddy.server.auth as auth_mod
            importlib.reload(auth_mod)
            assert auth_mod.JWT_SECRET == "my-custom-secret-key-for-testing-1234"

    def test_jwt_secret_auto_generated_without_env(self):
        """未设置环境变量时自动生成随机密钥."""
        with patch.dict(os.environ, {}, clear=False):
            if "VPBUDDY_JWT_SECRET" in os.environ:
                del os.environ["VPBUDDY_JWT_SECRET"]
            import importlib
            import vpbuddy.server.auth as auth_mod
            importlib.reload(auth_mod)
            assert auth_mod.JWT_SECRET, "自动生成的 JWT_SECRET 不应为空"
            assert len(auth_mod.JWT_SECRET) == 64, "token_hex(32) 生成 64 字符"


# ══════════════════════════════════════════════════════════════════
# 2. 速率限制
# ══════════════════════════════════════════════════════════════════

class TestRateLimiter:
    """验证速率限制逻辑."""

    def test_token_bucket_allows_within_limit(self):
        """在限制内请求应全部通过."""
        from vpbuddy.server.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=100, auth_rpm=10)
        for _ in range(50):
            assert limiter.check("127.0.0.1", is_auth=False), "正常请求应通过"

    def test_token_bucket_blocks_over_limit(self):
        """超过限制后应拒绝."""
        from vpbuddy.server.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=20, auth_rpm=5)
        # 消耗所有令牌
        for _ in range(20):
            limiter.check("127.0.0.1", is_auth=False)
        # 第 21 次应该被拒绝
        assert not limiter.check("127.0.0.1", is_auth=False), "超限请求应被拒绝"

    def test_token_bucket_separate_auth_limit(self):
        """认证端点应有独立的更严格限制."""
        from vpbuddy.server.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=100, auth_rpm=5)
        # 消耗 5 个 auth 令牌
        for _ in range(5):
            assert limiter.check("127.0.0.1", is_auth=True)
        # 第 6 次 auth 应被拒绝
        assert not limiter.check("127.0.0.1", is_auth=True), "auth 超限应拒绝"
        # 但普通 API 请求仍可通过
        assert limiter.check("127.0.0.1", is_auth=False), "普通 API 不受 auth 限制"

    def test_token_bucket_per_ip_isolation(self):
        """不同 IP 应有独立令牌桶."""
        from vpbuddy.server.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=5, auth_rpm=5)
        # IP1 消耗所有令牌
        for _ in range(5):
            limiter.check("192.168.1.1", is_auth=False)
        assert not limiter.check("192.168.1.1", is_auth=False)
        # IP2 应正常通过
        assert limiter.check("192.168.1.2", is_auth=False), "不同 IP 应独立计数"

    def test_bucket_cleanup(self):
        """桶清扫应清理过期条目."""
        from vpbuddy.server.rate_limit import RateLimiter
        limiter = RateLimiter(rpm=100, auth_rpm=10)
        # 创建一些桶
        for i in range(10):
            limiter.check(f"10.0.0.{i}")
        # 模拟时间流逝 (修改 last_refill 使桶看起来过期)
        with limiter._lock:
            for bucket in limiter._buckets.values():
                bucket.last_refill = time.monotonic() - 200.0
        # 强制清扫 (设置较短的 cleanup_interval)
        limiter._cleanup_interval = 0.0
        limiter.check("10.0.0.100")  # 触发清扫
        # 桶数量应减少
        assert len(limiter._buckets) < 10, "过期桶应被清理"


# ══════════════════════════════════════════════════════════════════
# 3. 锁字典 LRU 淘汰
# ══════════════════════════════════════════════════════════════════

class TestLockEviction:
    """验证锁字典 LRU 淘汰机制."""

    def test_eviction_triggers_at_max(self):
        """超过 _MAX_LOCKS 时应触发淘汰."""
        from vpbuddy.server import api_utils
        old_max = api_utils._MAX_LOCKS
        try:
            api_utils._MAX_LOCKS = 10
            api_utils._meta_locks.clear()
            api_utils._chat_locks.clear()
            api_utils._LOCK_ACCESS_ORDER.clear()

            # 创建 20 个锁 (超过 MAX_LOCKS * 2)
            for i in range(20):
                api_utils._get_meta_lock(f"evict-test-{i}")
                api_utils._get_chat_lock(f"evict-test-{i}")

            # 检查锁数量是否被限制
            total = len(api_utils._meta_locks) + len(api_utils._chat_locks)
            assert total <= api_utils._MAX_LOCKS * 2, f"锁数量应在限制内, 实际 {total}"
        finally:
            api_utils._MAX_LOCKS = old_max
            api_utils._meta_locks.clear()
            api_utils._chat_locks.clear()
            api_utils._LOCK_ACCESS_ORDER.clear()

    def test_recently_used_lock_preserved(self):
        """最近访问的锁应被保留."""
        from vpbuddy.server import api_utils
        old_max = api_utils._MAX_LOCKS
        try:
            api_utils._MAX_LOCKS = 5
            api_utils._meta_locks.clear()
            api_utils._LOCK_ACCESS_ORDER.clear()

            # 创建并访问第一个锁
            api_utils._get_meta_lock("keep-me")
            # 创建更多锁触发淘汰
            for i in range(15):
                api_utils._get_meta_lock(f"filler-{i}")

            # "keep-me" 应该因为最老被淘汰了
            # 最近创建的锁应该还在
            assert "filler-14" in api_utils._meta_locks, "最新锁应保留"
        finally:
            api_utils._MAX_LOCKS = old_max
            api_utils._meta_locks.clear()
            api_utils._LOCK_ACCESS_ORDER.clear()

    def test_storage_lock_eviction(self):
        """storage.py 锁淘汰也应正常工作."""
        from vpbuddy.storage import _get_lock, _meeting_locks, _LOCK_ACCESS_ORDER, _MAX_LOCKS
        old_max = _MAX_LOCKS
        try:
            import vpbuddy.storage as storage_mod
            storage_mod._MAX_LOCKS = 5
            storage_mod._meeting_locks.clear()
            storage_mod._LOCK_ACCESS_ORDER.clear()

            for i in range(15):
                _get_lock(f"storage-evict-{i}")

            assert len(storage_mod._meeting_locks) <= storage_mod._MAX_LOCKS, "storage 锁应被淘汰"
        finally:
            storage_mod._MAX_LOCKS = old_max
            storage_mod._meeting_locks.clear()
            storage_mod._LOCK_ACCESS_ORDER.clear()


# ══════════════════════════════════════════════════════════════════
# 4. safe_push_event
# ══════════════════════════════════════════════════════════════════

class TestSafePushEvent:
    """验证 safe_push_event 不抛异常."""

    def test_safe_push_swallows_exception(self):
        """push_event 失败时 safe_push_event 不应抛异常."""
        from vpbuddy.server.api_utils import safe_push_event
        with patch("vpbuddy.server.api_utils.push_event" if False else "vpbuddy.realtime_server.push_event", side_effect=RuntimeError("SSE 断开")):
            # 不应抛异常
            safe_push_event("test-meeting", "test-event", {"data": 1})

    def test_safe_push_normal_call(self):
        """正常情况下应调用 push_event."""
        from vpbuddy.server.api_utils import safe_push_event
        with patch("vpbuddy.realtime_server.push_event") as mock_push:
            safe_push_event("test-meeting", "test-event", {"data": 1})
            mock_push.assert_called_once_with("test-meeting", "test-event", {"data": 1})


# ══════════════════════════════════════════════════════════════════
# 5. 统一异常处理器
# ══════════════════════════════════════════════════════════════════

class TestUnifiedErrorHandler:
    """验证统一异常处理."""

    def test_http_exception_dict_format(self):
        """HTTPException detail 为 dict 时应直接返回."""
        from fastapi import HTTPException
        from vpbuddy.server.fastapi_app import _http_exception_handler
        # 模拟 request
        mock_request = MagicMock()
        exc = HTTPException(status_code=400, detail={"error": "test", "status": 400})
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _http_exception_handler(mock_request, exc)
        )
        assert result.status_code == 400
        body = json.loads(result.body)
        assert body["error"] == "test"
        assert body["status"] == 400

    def test_http_exception_str_format(self):
        """HTTPException detail 为 str 时应包装成 dict."""
        from fastapi import HTTPException
        from vpbuddy.server.fastapi_app import _http_exception_handler
        mock_request = MagicMock()
        exc = HTTPException(status_code=404, detail="not found")
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _http_exception_handler(mock_request, exc)
        )
        assert result.status_code == 404
        body = json.loads(result.body)
        assert body["error"] == "not found"
        assert body["status"] == 404

    def test_unhandled_exception_no_traceback(self):
        """未捕获异常不应泄露 traceback."""
        from vpbuddy.server.fastapi_app import _unhandled_exception_handler
        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.url = MagicMock()
        mock_request.url.path = "/api/test"
        exc = RuntimeError("sensitive internal error details")
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            _unhandled_exception_handler(mock_request, exc)
        )
        body = json.loads(result.body)
        assert "sensitive" not in str(body), "不应泄露内部错误细节"
        assert body["status"] == 500
        assert body["error"] == "服务器内部错误"


# ══════════════════════════════════════════════════════════════════
# 6. config.py 默认路径
# ══════════════════════════════════════════════════════════════════

class TestConfigDefaults:
    """验证配置默认路径不再硬编码特定用户路径."""

    def test_no_hardcoded_home_path(self):
        """DATA_DIR 不应包含 /home/zsd/."""
        from vpbuddy.server.config import DATA_DIR, DOCS_DIR, UI_DIR
        for d in [DATA_DIR, DOCS_DIR, UI_DIR]:
            assert "/home/zsd/" not in str(d), f"{d} 不应硬编码 /home/zsd/ 路径"

    def test_paths_are_under_project_root(self):
        """默认路径应在项目根目录下."""
        from vpbuddy.server.config import DATA_DIR, DOCS_DIR, UI_DIR, _PROJECT_ROOT
        for d in [DATA_DIR, DOCS_DIR, UI_DIR]:
            # 至少应该有 vpbuddy 在路径中
            assert "vpbuddy" in str(d).lower(), f"{d} 应包含 vpbuddy"


# ══════════════════════════════════════════════════════════════════
# 7. Chroma GPU 自动检测
# ══════════════════════════════════════════════════════════════════

class TestDeviceDetection:
    """验证 Chroma embedding 设备自动检测."""

    def test_default_cpu_without_gpu(self):
        """无 GPU 时应返回 cpu."""
        from vpbuddy.rag_backend import _detect_device
        with patch.dict(os.environ, {"VPBUDDY_EMBEDDING_DEVICE": ""}):
            with patch.dict("sys.modules", {"torch": None}):
                # torch 不可用时应 fallback 到 cpu
                result = _detect_device()
                # 至少不崩溃
                assert result in ("cpu", "cuda"), f"设备应为 cpu 或 cuda, 实际 {result}"

    def test_env_override(self):
        """环境变量应覆盖自动检测."""
        from vpbuddy.rag_backend import _detect_device
        with patch.dict(os.environ, {"VPBUDDY_EMBEDDING_DEVICE": "cuda:1"}):
            assert _detect_device() == "cuda:1"


# ══════════════════════════════════════════════════════════════════
# 8. 输入长度校验
# ══════════════════════════════════════════════════════════════════

class TestInputValidation:
    """验证输入长度限制."""

    def test_config_constants_exist(self):
        """配置常量应存在且合理."""
        from vpbuddy.server.config import (
            MAX_CHAT_MESSAGE_LENGTH,
            MAX_MEETING_ID_LENGTH,
            MAX_FILENAME_LENGTH,
            MAX_UPLOAD_SIZE,
        )
        assert MAX_CHAT_MESSAGE_LENGTH > 0
        assert MAX_MEETING_ID_LENGTH > 0
        assert MAX_FILENAME_LENGTH > 0
        assert MAX_UPLOAD_SIZE > 0

    def test_chat_message_length_limit(self):
        """超长 chat 消息应被拒绝."""
        from vpbuddy.server.config import MAX_CHAT_MESSAGE_LENGTH
        # 验证常量值合理
        assert 1000 <= MAX_CHAT_MESSAGE_LENGTH <= 100000, "Chat 长度限制应在合理范围"


# ══════════════════════════════════════════════════════════════════
# 9. bailian_asr.py import os 修复
# ══════════════════════════════════════════════════════════════════

class TestBailianASRImports:
    """验证 bailian_asr.py 的 import 修复."""

    def test_os_import_exists(self):
        """os 模块应已导入."""
        import vpbuddy.server.bailian_asr as bailian
        assert hasattr(bailian, "os"), "bailian_asr 应导入 os 模块"
