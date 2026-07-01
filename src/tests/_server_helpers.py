"""测试共享 helper — 等 ui_server 在 thread 启动后真 bind 端口

历史问题 (v0.8.0 验证发现):
- `ui_server.main()` 在后台 thread 启动后, **KB Chroma 首次加载 embedding 模型**
  需要 ~10-12s (CPU 本机, 取决于 sentence-transformers 模型大小)
- 老 fixture 用 `time.sleep(1)` 远不够, test 立刻 POST → Connection refused
- 修复: 轮询 socket.create_connection 直到 port 真 listen, max 30s 超时

用法:
    from ._server_helpers import wait_for_server
    @pytest.fixture(scope="module")
    def server():
        # ... 启动 thread ...
        return wait_for_server(TEST_HOST, TEST_PORT, timeout=30)
"""
from __future__ import annotations
import socket
import time


def wait_for_server(host: str, port: int, timeout: float = 30.0) -> str:
    """等 server 真 listen 该端口, 返回 base URL

    轮询: 100ms 间隔, 直到 socket.create_connection 成功 或 timeout.
    返回: f"http://{host}:{port}"

    Raises:
        RuntimeError: timeout 内 port 没起来
    """
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return f"http://{host}:{port}"
        except OSError as e:
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(
        f"server {host}:{port} 在 {timeout}s 内未起来 (最后错误: {last_err})"
    )