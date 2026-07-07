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

**用户 A 的会议 M1 调用知识库检索时 → 查用户 A 的知识库，不是查 M1 的知识库。**

### 2. 用户认证

**方案**: 邮箱 + 验证码登录，JWT token，72 小时过期。

- 注册: `POST /api/auth/register` — 邮箱 + 密码，返回 JWT
- 登录: `POST /api/auth/login` — 邮箱 + 密码，返回 JWT
- 验证: `GET /api/auth/me` — 返回当前用户信息

不实现 OAuth（Google/GitHub/微信），不实现邮箱验证码注册，不实现密码重置——MVP 阶段只做最基本的邮箱密码登录。客户端本地持久化 token，启动时自动验证。

### 3. API 认证

所有 `/api/meetings/*` 和 `/api/kb/*` 端点要求 `Authorization: Bearer <token>` header。

`GET /api/auth/me` 和 `POST /api/auth/login` / `register` 例外。

### 4. 多设备 / 多人同会

不做。一个用户一个客户端一条会话。没有"邀请他人加入会议"的概念——会议本身就是用户私有的。

### 5. 数据迁移

现有数据（无 `user_id` 的会议和知识库）统一归属到一个默认用户。迁移脚本在服务启动时自动执行：

```python
# 启动时检查
if not db.has_any_user():
    default_user = create_user(email="admin@vpbuddy.local")
    migrate_all_meetings_to(default_user.id)
    migrate_all_kb_to(default_user.id)
```

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
| 新增迁移脚本 | `_migrate_v0_16_to_user_model()` |

### 前端

| 组件 | 变更 |
|------|------|
| 新增登录页 | 邮箱 + 密码 → `POST /api/auth/login` → 存 token → 跳主界面 |
| 新增注册页 | 邮箱 + 密码 → `POST /api/auth/register` → 自动登录 |
| 修改 `main.js` | 启动时检查 token → 无效则跳登录页 |
| 修改所有 API 调用 | 统一带上 `Authorization` header |

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
- 知识库的访问权限细化（读/写/管理）
- 前端路由级别的 token 刷新（只做启动时验证一次）

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| JWT 泄露 → 任意 HTTP 调用 | HTTPS + token 过期 72h |
| 知识库文件被其他用户直接 URL 访问 | 静态文件路径不在 Nginx 暴露；下载走 `/api/kb/{doc_id}/file` 带 auth |
| 数据库明文存储密码 | 使用 `bcrypt` hashing |
| 现有数据迁移丢失 | 启动时自动迁移 + 日志记录 |
