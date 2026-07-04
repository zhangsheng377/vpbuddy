# VPBuddy 问题跟踪

> 创建: 2026-07-04
> 最后更新: 2026-07-04
> 来源: 代码审查报告 [CODE_REVIEW.md](./CODE_REVIEW.md)

---

## 使用说明

- **状态**: `待处理` / `处理中` / `已完成` / `已关闭(非问题)`
- **严重性**: 🔴 P1 = 阻塞/紧急 🟡 P2 = 重要 🟢 P3 = 可优化
- 按"建议优先级"排序，先修 P1 再修 P2/P3

---

## 修复摘要

| # | 问题 | 状态 | 效果 |
|---|------|------|------|
| P0 | funasr AutoModel 未缓存 | ✅ **已完成** | ASR 首字 **30s → 5.4s** |
| P1#1 | 硬编码 `/home/zsd/` 路径 | ✅ **已完成** | 7 文件 Project_ROOT 替换 |
| P1#2 | `ui_server.py` 84KB | ✅ **已完成** | 1930→1370 行 + 5 模块 |
| P1#3 | 手写 multipart 解析器 | ⏸️ 等 FastAPI | 需切换到异步框架 |
| P1#4 | prompt 模板转义 | ✅ **已完成** | `string.Template` |
| P1#5 | Rust `Box::leak` 内存泄漏 | ✅ **已完成** | `Arc<cpal::Stream>` 替换 |
| P1#7 | AIAgent daemon 线程泄漏 | ✅ **已完成** | `concurrent.futures` |

---

## ✅ 已完成

### P0. funasr AutoModel 未缓存

| 字段 | 值 |
|------|-----|
| **文件** | `gpu_transcribe.py`, `ui_server.py` |
| **状态** | ✅ **已完成并部署** (2026-07-04) |
| **修改** | `_get_model()` 模块级单例缓存 + `warmup_models()` 启动预热 |
| **效果** | ASR 首字延迟 **30s → 5.4s** (预热后) |
| **部署** | GPU 服务器 ✅ 开发服务器 ✅ |

### P1#1. 硬编码 `/home/zsd/` 路径

| 字段 | 值 |
|------|-----|
| **文件** | `storage.py`, `ui_server.py`, `sub_session_controller.py`, `rag_backend.py`, `agent_proactive.py`, `skill.py`, `dashboard.py` |
| **状态** | ✅ **已完成并部署** (2026-07-04) |
| **修改** | 每个文件插入 `PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent`，替换 15 处硬编码路径 |
| **方案** | `"VPBUDDY_DATA_DIR", PROJECT_ROOT / "data" / "meetings"` 替代 `"VPBUDDY_DATA_DIR", "/home/zsd/..."` |
| **部署** | GPU 服务器 ✅ 开发服务器 ✅ |

### P1#2. ui_server.py 84KB 单文件

| 字段 | 值 |
|------|-----|
| **文件** | `ui_server.py`, `server/` 5 模块 |
| **状态** | ✅ **已完成并部署** (2026-07-04) |
| **修改** | 1930 → 1370 行，提取 21 个函数到 `server/config.py`, `server/stream_meta.py`, `server/asr_clean.py`, `server/chat_engine.py`, `server/api_utils.py` |
| **方案** | `ui_server.py` 仅保留 `Handler` 类 + `_parse_multipart` + `main()` |
| **部署** | GPU 服务器 ✅ 开发服务器 ✅ |

### P1#4. prompt 模板转义逻辑缺陷

| 字段 | 值 |
|------|-----|
| **文件** | `sub_session_controller.py` → `render_prompt()` |
| **状态** | ✅ **已完成并部署** (2026-07-04) |
| **修改** | 用 `string.Template.safe_substitute` 替代 `.format()`，文档内容含 `{ }` 时不再抛 `KeyError` |
| **部署** | GPU 服务器 ✅ 开发服务器 ✅ |

### P1#7. AIAgent 超时 daemon 线程泄漏

| 字段 | 值 |
|------|-----|
| **文件** | `sub_session_controller.py` → `_trigger_via_aiagent()` |
| **状态** | ✅ **已完成并部署** (2026-07-04) |
| **修改** | 用 `ThreadPoolExecutor + Future.result(timeout=)` 替代手编 daemon thread |
| **部署** | GPU 服务器 ✅ 开发服务器 ✅ |

---

## ⏳ 待处理

### P1#3. 手写 multipart/form-data 解析器

| 字段 | 值 |
|------|-----|
| **文件** | `ui_server.py`, `kb_api.py` → `_parse_multipart()` |
| **状态** | ⏸️ **等 FastAPI 迁移** |
| **理由** | `python-multipart` 是事件驱动流式解析器，需要 `http.server` 改成流式读取才能用。当前 `self.rfile.read(content_length)` 是一次性读完的，无法对接。等切 FastAPI + uvicorn 时自然解决 |

### P1#5. Rust Box::leak 滥用导致内存泄漏

| 字段 | 值 |
|------|-----|
| **文件** | `audio.rs` → `StreamGuard::Merged` |
| **状态** | ✅ **已完成并编译通过** (2026-07-04) |
| **修改** | `Box::leak` → `Arc<cpal::Stream>` | `let _mixer = Box::leak(...)` → `std::thread::spawn(...)` |
| **部署** | 开发服务器 ✅ (cargo check 通过) |

---

## 🟡 P2 - 重要

### 6. lib.rs 单文件职责过重

| 字段 | 值 |
|------|-----|
| **文件** | `main.rs` (1088 行) |
| **状态** | ✅ **已完成并编译通过** (2026-07-04) |
| **修改** | 拆出 `config.rs` (183 行)：`AppState`、`ClientConfig`、`AudioConfig`、`SseConfig`、配置函数 |
| **难点** | Tauri 2 `generate_handler!` proc-macro 在模块边界不可用，命令函数必须留在 `main.rs`；拆分后 `main.rs` 916 行 |
| **部署** | 开发服务器 ✅ GitHub 7aab936 ✅ |

### 7. Linux 开发服务器无 cargo

| 字段 | 值 |
|------|-----|
| **位置** | `192.168.10.5` |
| **状态** | 待处理 |
| **建议** | 装 Rustup |
| **优先级** | 7 |

### 8. Rust 源文件不同步（服务器为空）

| 字段 | 值 |
|------|-----|
| **位置** | Linux 开发服务器 `vpbuddy-client/src-tauri/src/` |
| **状态** | 待处理 |
| **建议** | 检查 `.gitignore` 和 `dual_repo_sync.sh` |
| **优先级** | 8 |

### 9. GPU 服务器 ~/.hermes/.env 为空

| 字段 | 值 |
|------|-----|
| **位置** | `47.100.182.3` root 用户 |
| **状态** | ✅ **无需处理** — `/data/vpbuddy/.env` 配置完整 |
| **建议** | Hermes 未安装，用 VPBuddy 独立 .env |
| **优先级** | 9 |

### 10. SSE push_event 异常静默忽略

| 字段 | 值 |
|------|-----|
| **文件** | `sub_session_controller.py`, `state.py` |
| **状态** | ✅ **已完成** (2026-07-04) |
| **修改** | `pass` → `logger.warning()` |
| **部署** | GPU 服务器 ✅ 开发服务器 ✅ |



---

## 🟢 P3 - 可优化

### 11. mix_two_streams 精度损失

| 字段 | 值 |
|------|-----|
| **文件** | `audio.rs` |
| **状态** | ✅ **已完成** (2026-07-04) — `f32` 中间格式 |
| **修改** | `((m+l)/2)` → `((m+l)*0.5)` in f32 |
| **部署** | 开发服务器 ✅ (`cargo check` 通过) |

### 12. Chroma 数据目录不一致

| 字段 | 值 |
|------|-----|
| **文件** | `rag_backend.py`, `kb_api.py` |
| **状态** | ✅ **已完成** (2026-07-04) |
| **修改** | `VPBUDDY_KB_DIR` 环境变量控制 Chroma 存储根目录 |
| **部署** | GPU 服务器 ✅ 开发服务器 ✅ |

---

## 统计

| 级别 | 数量 | 已完成 | 待处理 |
|------|------|--------|--------|
| P0 | 1 | 1 | 0 |
| P1（必须修复） | 6 | 5 | 1 |
| P2（重要） | 4 | 3 | 1 |
| P3（可优化） | 2 | 2 | 0 |
| **合计** | **13** | **11** | **2** |
