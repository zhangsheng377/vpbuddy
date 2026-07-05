# ADR-0044: FastAPI 迁移 (BFF API)

- **状态**: 已采纳
- **日期**: 2026-07-05
- **作者**: AI Agent (Hermes)
- **替代**: 无
- **依赖**: ADR-0022 (会议生命周期), ADR-0023 (Chat 上传), ADR-0028 (Collaboration 疑问)

## Context

VPBuddy 的 HTTP 服务端最初基于 Python 标准库 `http.server.BaseHTTPRequestHandler` 手写路由 (`ui_server.py`)。

痛点:

1. **手写路由不安全**: 路径解析、参数提取、HTTP 方法分发全靠字符串匹配和正则。容易漏处理边界(如 URL 编码、特殊字符)。
2. **CORS 手写易错**: CORS 头在 `_send_cors_headers()` 中手动组装, 容易漏 `Vary: Origin`、`Access-Control-Allow-Credentials` 等细节。
3. **SSE 手写易漏**: `text/event-stream` 的 `Cache-Control`、`Connection` 头手动设置, 漏了 `X-Accel-Buffering: no` 导致 Nginx 缓冲 SSE。
4. **参数提取繁琐**: 每个 handler 里手动 `parse_qs` 和 `urlparse`, 代码重复、易错。
5. **没有 OpenAPI**: BaseHTTPRequestHandler 不自带 OpenAPI schema, 客户端写 fetch 全靠读源码+试错。

## Decision

引入 FastAPI 作为 BFF (Backend For Frontend) API 层, 同时保持 `ui_server.py` 的向后兼容。

### 架构方案

1. **新增 `fastapi_app.py`**: 完全独立的 FastAPI 应用, 从 `ui_server.py` 导入所有业务函数。
2. **双兼容**: 旧 `BaseHTTPRequestHandler` 代码不动, `vpbuddy ui --fastapi` 启动 FastAPI 版本。
3. **按需迁移**: 不一次性重写全部, 先迁移核心路由, 边缘端点保持原样。
4. **部署双路径**: GPU 上两个 server 进程均可部署, 通过 `--fastapi` flag 切换。

### FastAPI 特性使用

| 特性 | 用法 |
|------|------|
| `CORSMiddleware` | 替代手写 CORS, 支持 `allow_origins`, `allow_credentials` |
| `StreamingResponse` | 替代手写 SSE, 确保 `media_type="text/event-stream"` |
| `Query` / `Path` | 替代手写 `parse_qs`, 自带校验和类型转换 |
| `HTTPException` | 统一错误响应格式 |
| `File` / `Form` / `UploadFile` | 替代手写 multipart parser |
| `StaticFiles` | 替代手写静态文件服务 |
| 自动 OpenAPI | `/docs` + `/openapi.json` 开箱即用 |

### 路由映射

| 旧路由 | FastAPI 路由 | 方法 |
|--------|-------------|------|
| `/api/meetings` | `/api/meetings` | GET |
| `/api/meetings?check_id=` | `/api/meetings/check_id` | GET |
| `/api/timeline` | `/api/timeline` | GET |
| `/api/kb/search` | `/api/kb/search` | GET |
| `/api/kb/list` | `/api/kb/list` | GET |
| `/api/kb/{doc_id}` (DELETE) | `/api/kb/{doc_id}` | DELETE |
| `/api/status` | `/api/status` | GET |
| `/api/meetings/{id}` | `/api/meetings/{meeting_id}` | GET (aggregate) |
| `/api/meetings/{id}/state` | `/api/meetings/{meeting_id}/state` | GET |
| `/api/meetings/{id}/chat/history` | `/api/meetings/{meeting_id}/chat/history` | GET |
| `/api/meetings/{id}/collab` | `/api/meetings/{meeting_id}/collab` | GET |
| `/api/meetings/{id}/docs` | `/api/meetings/{meeting_id}/docs` | GET |
| `/api/meetings/{id}/docs/{kind}` | `/api/meetings/{meeting_id}/docs/{kind}` | GET |
| `/api/meetings/{id}/demo/versions` | `/api/meetings/{meeting_id}/demo/versions` | GET |
| `/api/meetings/{id}/events` | `/api/meetings/{meeting_id}/events` | GET (SSE) |
| `/api/meetings/stream_start` | `/api/meetings/stream_start` | POST |
| `/api/meetings/{id}/stream_chunk` | `/api/meetings/{meeting_id}/stream_chunk` | POST |
| `/api/meetings/{id}/upload_audio` | `/api/meetings/{meeting_id}/upload_audio` | POST |
| `/api/meetings/{id}/stream_stop` | `/api/meetings/{meeting_id}/stream_stop` | POST |
| `/api/meetings/{id}/close` | `/api/meetings/{meeting_id}/close` | POST |
| `/api/meetings/{id}/chat` | `/api/meetings/{meeting_id}/chat` | POST |
| `/api/meetings/{id}/collab/ask` | `/api/meetings/{meeting_id}/collab/ask` | POST |
| `/api/meetings/{id}/collab/answer` | `/api/meetings/{meeting_id}/collab/answer` | POST |
| `/api/kb/search` (POST) | `/api/kb/search` | POST |
| `/api/kb/upload` | `/api/kb/upload` | POST |
| `/api/client/device-status` | `/api/client/device-status` | GET |
| `/api/meetings/{id}` (aggregate) | `/api/meetings/{meeting_id}/aggregate` | GET |

约 28 个端点。

## Consequences

### 正面

- **安全性**: FastAPI 的路由/参数校验/异常处理更健壮, 不再手写 parse_qs。
- **CORS 正确性**: CORSMiddleware 自动处理 `Vary: Origin`、`Allow-Credentials`、preflight。
- **SSE 正确性**: StreamingResponse 自动设置 `text/event-stream` + 正确头。
- **OpenAPI**: 客户端开发者可以直接看 `/docs` 交互式文档。
- **零 break**: 旧 `ui_server.py` 不变, 双兼容路径, 可以逐步切换。
- **性能**: FastAPI/uvicorn 基于 asyncio, 比 BaseHTTPRequestHandler 的同步模型吞吐更高。

### 负面

- **双代码维护**: `fastapi_app.py` 和 `ui_server.py` 共存, 新端点需要在两边注册(直到旧版完全淘汰)。
- **异步学习曲线**: FastAPI 部分端点需要 async (如 StreamingResponse), 与现有同步业务函数混用有认知负担。
- **依赖增加**: 引入 `fastapi` + `uvicorn` 依赖 (已在 pyproject.toml 中)。

### Migration 策略

1. Phase 1 (已完成): `fastapi_app.py` 创建, 注册所有 28 个路由, 通过 `vpbuddy ui --fastapi` 启用。
2. Phase 2 (当前): E2E 测试覆盖所有 FastAPI 端点, 验证 CORS/SSE/参数校验。
3. Phase 3 (v0.10): 默认启动切换为 FastAPI, `ui_server.py` 保留为 fallback。
4. Phase 4 (v0.11): 删除 `ui_server.py`, 仅保留 FastAPI。
