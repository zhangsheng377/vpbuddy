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
| P1#5 | Rust `Box::leak` 内存泄漏 | 待处理 | Rust 端 |
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
| **状态** | 待处理 |
| **描述** | `both` 路径用 `Box::leak` 延寿 cpal Stream，频繁 start/stop 录音会内存持续增长 |
| **建议** | 改用 `Arc<Mutex<Option<cpal::Stream>>>` 共享所有权 |
| **优先级** | 5 |

---

## 🟡 P2 - 重要

### 6. lib.rs 单文件职责过重

| 字段 | 值 |
|------|-----|
| **文件** | `lib.rs` (~500 行) |
| **状态** | 待处理 |
| **建议** | 拆出 `config.rs`、`commands.rs` |
| **优先级** | 6 |

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
| **状态** | 待处理 |
| **建议** | 确认 API key 注入路径 |
| **优先级** | 9 |

### 10. SSE push_event 异常静默忽略

| 字段 | 值 |
|------|-----|
| **文件** | `sub_session_controller.py`, `state.py`, `agent_proactive.py` |
| **状态** | 待处理 |
| **建议** | 改为 `logger.warning` + 健康检查 |
| **优先级** | 10 |

---

## 🟢 P3 - 可优化

### 11. mix_two_streams 精度损失

| 字段 | 值 |
|------|-----|
| **文件** | `audio.rs` |
| **描述** | `(m + l) / 2` 在 i16 域计算可能导致饱和失真 |
| **建议** | 用 f32 中间格式计算 |

### 12. Chroma 数据目录不一致

| 字段 | 值 |
|------|-----|
| **文件** | `rag_backend.py` |
| **建议** | 统一到 `config.py` 管理 |

---

## 统计

| 级别 | 数量 | 已完成 | 待处理 |
|------|------|--------|--------|
| P0 | 1 | 1 | 0 |
| P1（必须修复） | 6 | 4 | 2 |
| P2（重要） | 5 | 0 | 5 |
| P3（可优化） | 2 | 0 | 2 |
| **合计** | **14** | **5** | **9** |
