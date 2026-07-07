# ADR-0047: 用户认证与知识库跨会议共享

- **状态**: 草案
- **日期**: 2026-07-07
- **决策者**: 张胜东, 张帅东
- **关联**: ADR-0019 (Chroma RAG) · ADR-0020 (KB 文件上传)

---

## 背景

当前系统所有数据按 `meeting_id` 隔离——知识库、会议材料、会议历史均无用户维度。这意味着：

1. 任何人都能通过 `GET /api/meetings` 列出所有会议
2. 知识库上传的文件全局可见，无法区分"是谁的知识"
3. API 无认证，公网服务器 `47.100.182.3:28765` 任何知道 URL 的人都能调

产品定位已明确为**单用户会议系统**——每个用户下载客户端 → 登录 → 一个人使用。知识库是个人资产，应该跨用户的全部会议共享；会议材料则仍然按 `meeting_id` 隔离。

---

## 决策

### 1. 数据隔离模型

两棵树，不是一棵树：

```
知识库（按 user_id）
  ├── 用户 A 的知识库   ── 用户 A 的所有会议都可以检索
  └── 用户 B 的知识库   ── 用户 B 的所有会议都可以检索

会议（按 meeting_id，owner = user_id）
  ├── 用户 A 的会议 M1（含会议材料、文档、chat）
  ├── 用户 A 的会议 M2
  ├── 用户 B 的会议 M3
  └── ...
```

用户 A 的会议 M1 调用知识库检索时 → 查用户 A 的知识库，不是查 M1 的知识库。

### 2. 用户认证

邮箱 + 密码登录，JWT token，72 小时过期。

- 注册: `POST /api/auth/register` — 邮箱 + 密码，返回 JWT
- 登录: `POST /api/auth/login` — 邮箱 + 密码，返回 JWT
- 验证: `GET /api/auth/me` — 返回当前用户信息

不实现 OAuth（Google/GitHub/微信），不实现邮箱验证码注册，不实现密码重置——MVP 阶段只做最基本的邮箱密码登录。

### 3. API 认证

所有 `/api/meetings/*` 和 `/api/kb/*` 端点要求 `Authorization: Bearer <token>` header。

以下端点例外（无需 token）：
- `POST /api/auth/login`
- `POST /api/auth/register`
- `GET /api/auth/me`

### 4. 客户端启动流程

```
打开客户端
  → 读取 localStorage 中的 JWT token
  → GET /api/auth/me 验证 token
  → 有效 → 进入主界面（现有逻辑）
  → 无效/过期 → 显示登录页
       ├─ 输入邮箱+密码 → POST /api/auth/login
       │    → 成功 → 存 token 到 localStorage → 进入主界面
       │    → 失败 → 显示错误提示
       └─ 点击"注册" → POST /api/auth/register
            → 成功 → 自动登录 → 进入主界面
```

登录页是一个覆盖在主界面之上的全屏 UI 层，登录成功后隐藏。

### 5. 多设备 / 多人同会

不做。一个用户一个客户端一条会话。

### 6. 历史数据

不迁移。部署需求方注册新用户，从零开始。

---

## 变更清单

### 后端

| 模块 | 变更 |
|------|------|
| 新增 `auth.py` | 注册/登录/验证端点，JWT 签发与校验 |
| 新增 `models.py` | `User` 数据模型（id, email, password_hash, created_at） |
| 修改 `kb_api.py` | KB 检索 `meeting_id` → `user_id`；KB 上传关联 `user_id` |
| 修改 `fastapi_app.py` | 所有 `/api/meetings/*` / `/api/kb/*` 路由加 `Depends(get_current_user)` |
| 修改 `storage.py` | `MeetingState` 新增 `user_id` 字段 |

### 客户端

| 组件 | 变更 |
|------|------|
| `index.html` | 新增 `#auth-overlay` 全屏登录层（含登录/注册表单） |
| `main.js` | 启动时检查 token → 有效则隐藏 overlay；登录/注册逻辑；所有 API 调用加 `Authorization` |
| `style.css` | 新增 `.auth-overlay` / `.auth-form` / `.auth-error` 样式 |

### 数据库

新增 SQLite 文件 `data/auth.db`（与 Chroma KB 分开）：

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 知识库存储结构调整

```
旧: kb/{meeting_id}/{doc_id}
新: kb/{user_id}/{doc_id}
```

Chroma collection metadata 加 `user_id` 字段。检索时按 `user_id` 过滤。

---

## 不做的

- OAuth 第三方登录
- 多人协作 / 多人同会
- 密码重置 / 邮箱验证
- 组织 / 团队 / 企业空间
- 历史数据迁移
- 前端路由级别的 token 刷新（只做启动时验证一次）

---

## 测试用例

### 模块 1: `test_auth.py` — 用户认证

| # | 测试 | 说明 |
|---|------|------|
| 1 | `test_register_creates_user` | 新邮箱注册成功，返回 JWT token |
| 2 | `test_register_duplicate_email` | 重复邮箱注册 → 409 Conflict |
| 3 | `test_register_invalid_email` | 无效邮箱格式 → 400 Bad Request |
| 4 | `test_register_short_password` | 密码少于 6 位 → 400 Bad Request |
| 5 | `test_login_valid_credentials` | 正确的邮箱密码 → 返回 JWT |
| 6 | `test_login_wrong_password` | 错误密码 → 401 Unauthorized |
| 7 | `test_login_nonexistent_email` | 未注册邮箱 → 401 Unauthorized |
| 8 | `test_login_empty_body` | 空 JSON → 400 Bad Request |
| 9 | `test_me_returns_user_info` | 带有效 token → 返回 `{email, created_at}` |
| 10 | `test_me_missing_token` | 无 Authorization header → 401 |
| 11 | `test_me_expired_token` | 过期 token → 401 |
| 12 | `test_me_malformed_token` | 伪造 token → 401 |
| 13 | `test_password_hashed` | 数据库中存储的是 hash，不是明文 |

### 模块 2: `test_auth_middleware.py` — API 认证中间件

| # | 测试 | 说明 |
|---|------|------|
| 14 | `test_meetings_require_auth` | `GET /api/meetings` 无 token → 401 |
| 15 | `test_meetings_with_token` | `GET /api/meetings` 带 token → 200 |
| 16 | `test_kb_search_requires_auth` | `GET /api/kb/search` 无 token → 401 |
| 17 | `test_auth_endpoints_no_token_ok` | `/api/auth/*` 不走认证 |
| 18 | `test_stream_start_requires_auth` | `POST /api/meetings/stream_start` 无 token → 401 |

### 模块 3: `test_auth_kb_isolation.py` — 知识库按 user 隔离

| # | 测试 | 说明 |
|---|------|------|
| 19 | `test_kb_upload_sets_user_id` | 用户 A 上传文件 → Chroma metadata 含 `user_id=A` |
| 20 | `test_kb_search_only_own_docs` | 用户 A 检索 → 只返回 A 的文件，不返回 B 的 |
| 21 | `test_kb_upload_cross_user_invisible` | 用户 B 上传 → 用户 A 查不到 |
| 22 | `test_kb_meeting_material_still_meeting_scoped` | 会议材料上传 → 仍按 meeting_id 隔离，不影响 |

### 模块 4: `test_auth_unauthenticated.py` — 未认证客户端行为

| # | 测试 | 说明 |
|---|------|------|
| 23 | `test_unauthenticated_lists_empty` | 未认证时 GET /api/meetings 返回 401（不是空列表） |
| 24 | `test_unauthenticated_cannot_chat` | 未认证时 POST /api/chat → 401 |
| 25 | `test_unauthenticated_cannot_view_docs` | 未认证时 GET /docs → 401 |

### 模块 5: `test_auth_meeting_isolation.py` — 会议按 owner 隔离

| # | 测试 | 说明 |
|---|------|------|
| 26 | `test_create_meeting_owns_it` | 用户 A 创建的会议，owner_id = A |
| 27 | `test_own_meetings_visible` | 用户 A 的 GET /api/meetings 只返回自己的会议 |
| 28 | `test_other_users_meetings_invisible` | 用户 B 看不到用户 A 的会议 |
| 29 | `test_cannot_close_other_meeting` | 用户 B POST /api/meetings/A-mtg/close → 404 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| JWT 泄露 → 任意 HTTP 调用 | HTTPS + token 过期 72h |
| 知识库文件被其他用户直接 URL 访问 | 静态文件路径不在 Nginx 暴露；下载走 `/api/kb/{doc_id}/file` 带 auth |
| 数据库明文存储密码 | 使用 `bcrypt` hashing |
| 客户端 token 被清除后反复登录 | 72h 过期 + 客户端本地持久化 |
