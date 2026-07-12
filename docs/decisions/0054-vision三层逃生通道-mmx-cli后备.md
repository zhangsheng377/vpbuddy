# ADR-0054: Vision 识图三层逃生通道 + mmx-cli 后备

**状态**: ✅ 已落地 (2026-07-12)

---

## 问题背景

VPBuddy 用户上传图片后，Hermes Agent 的 vision 工具链在这个 GPU 服务器环境中不可用：

1. `resolve_runtime_provider("custom")` 硬编码返回 OpenRouter（Hermes 常量 `OPENROUTER_BASE_URL`）
2. 删除 `/etc/environment` 里的 `OPENROUTER_API_KEY`、`auth.json` 凭据池后仍然返回 OpenRouter
3. `/root/.hermes/config.yaml` 缺 `model.base_url`，导致 URL 回退链条最终落到 `https://openrouter.ai/api/v1`
4. Anthropic SDK 路由用 MiniMax key 打 `api.anthropic.com` → 401
5. DashScope 端点不支持 Anthropic messages 协议 → 404

**核心矛盾**: Hermes 的 vision 路由与 GPU 服务器的 LLM 环境不兼容，且 Hermes 源码不可改（pip 安装的外部依赖）。

---

## 三层通道设计

| 通道 | 位置 | 触发条件 | 说明 |
|------|------|----------|------|
| **monkeypatch** | `api_utils.py:_get_chat_agent()` + `sub_session_controller.py:_get_or_create_agent()` | AIAgent 创建前 | 注入 `resolve_runtime_provider = lambda: None`，让 Hermes vision 走 env fallback → `_create_openai_client(DashScope)` |
| **mmx-cli 后备** | `fastapi_app.py:_run_vision_async()` | OpenAI 主路径失败/空结果/异常 | `mmx vision describe --image xxx.jpg`，MiniMax 原生 VLM，不经过 Hermes |
| **OPENAI_* 兜底** | `fastapi_app.py` .env 加载后 | `.env` 缺 `OPENAI_API_KEY` | 从 `DASHSCOPE_API_KEY` 推导 `OPENAI_API_KEY` + `OPENAI_BASE_URL` |

### monkeypatch 生效范围

| 文件 | 覆盖 agent | session_id |
|------|-----------|------------|
| `src/vpbuddy/server/api_utils.py:L353-L364` | 主 chat agent | `meeting:{mid}:vp-chat` |
| `src/vpbuddy/sub_session_controller.py:L144-L153` | 文档生成子 agent | `meeting:{mid}:batch_docs` / `demo` |

### mmx-cli 后备路径

```
fastapi_app.py POST /api/meetings/{id}/materials
  → _run_vision_async()                          # 图片上传后台线程
    → 主: OpenAI /chat/completions (DashScope)
    → 备: _try_mmx_vision(file_data)             # MiniMax 原生 VLM
      → subprocess.run(["mmx", "vision", "describe", ...])
      → JSON {"content": "...", "base_resp": {"status_code": 0}}
```

---

## 依赖清单

| 组件 | 安装方式 | 版本 | 用途 |
|------|----------|------|------|
| `mmx-cli` | `npm install -g mmx-cli` | ≥1.0.16 | vision 后备 CLI |
| Node.js | 系统包管理器 | ≥20 | mmx-cli 运行时 |
| MiniMax API Key | `mmx auth login --api-key sk-xxx` | — | mmx-cli 鉴权 |

---

## 客户端影响

**无需更新客户端二进制。** 所有改动在服务端 Python 代码：

- `src/vpbuddy/server/api_utils.py` — monkeypatch 主 chat agent
- `src/vpbuddy/server/fastapi_app.py` — mmx 后备 + OPENAI 兜底
- `src/vpbuddy/sub_session_controller.py` — monkeypatch 子 agent

---

## 部署检查清单

```bash
# 1. 确认 mmx-cli 已安装
mmx --version        # ≥ 1.0.16

# 2. 确认 mmx 已登录
mmx auth status      # "method": "api-key"

# 3. 确认 vision 可用
mmx vision describe --image /path/to/test.jpg --prompt "一句话描述"

# 4. 确认 monkeypatch 在代码中
grep 'resolve_runtime_provider = lambda' src/vpbuddy/server/api_utils.py
grep 'resolve_runtime_provider = lambda' src/vpbuddy/sub_session_controller.py

# 5. 确认 mmx 后备在代码中
grep '_try_mmx_vision' src/vpbuddy/server/fastapi_app.py
```
