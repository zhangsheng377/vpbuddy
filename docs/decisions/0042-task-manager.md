# ADR-0042: 后台任务队列 (per-meeting debounce)

- **状态**: 已采纳
- **日期**: 2026-07-05
- **作者**: AI Agent (Hermes)
- **替代**: 无
- **依赖**: ADR-0029 (6→2 batch agent 合并)

## Context

长会议持续 1-2 小时, 每 30s 产生一个 WAV chunk, 每次 chunk 处理完成后通过 controller 触发 6 文档/demo 生成.

原有实现的问题:

1. **线程无限堆积**: 每个 chunk 处理末尾直接创建 `threading.Thread` / 调用 `ThreadPoolExecutor.submit(per-chunk)`, 没有 per-meeting 级别的去重。如果 chunk 处理快于 6 doc LLM 调用(通常 30-60s), 队列无限增长。
2. **旧写覆盖**: 多个 generation 同时 running, 后完成的旧 generation 可能覆盖新 generation 的文档内容, 导致用户看到旧版本文档。
3. **无可见性**: 无法查询"当前会议的文档生成任务状态", 调试困难。

## Decision

引入 `MeetingTaskQueue` (per-meeting 单任务队列) + `DocTaskManager` (全局管理器)。

### 核心设计

1. **per-meeting 单任务队列**: 每个会议只有一个 `MeetingTaskQueue`, 同一时间只允许一个 pending/running 任务。新任务提交时自动 cancel 旧任务(debounce 语义)。
2. **generation_id 递增**: 每次 submit 自增。任务回调内检查当前 `current_task.generation_id` 是否匹配, 不匹配则不写结果(防旧写覆盖)。
3. **全局 bounded ThreadPoolExecutor**: `DocTaskManager` 持有一个 `max_workers=4` 的线程池, 所有会议共享, 避免无限线程。
4. **任务状态**: QUEUED → RUNNING → COMPLETED / TIMED_OUT / CANCELLED, 可通过 `get_status()` 查询。

### 接口

```python
get_task_manager(max_workers=4) -> DocTaskManager
manager.submit(meeting_id, runner: Callable[[int, str], Any]) -> DocTask
manager.cancel_meeting(meeting_id)
manager.cleanup_meeting(meeting_id)
manager.get_status(meeting_id=None) -> dict
```

### 在 fastapi_app.py 中的集成

`_process_chunk_sync` 和 `_process_chunk_background` 末尾调用:
```python
get_task_manager().submit(meeting_id, _doc_runner)
```
runner 接受 `(gen_id, meeting_id)` 参数, 在内部检查 `is_stale(gen_id)` 决定是否写结果。

## Consequences

### 正面

- **debounce 减少重复触发**: 30s chunk 间隔内, 即使旧任务未完成, 新来的也自动 cancel 旧任务, 确保只有最新一次文档生成执行。
- **generation_id 防旧写覆盖**: 即使旧任务在 cancel 前已完成 LLM 调用, 回调检查 generation_id 不匹配则丢弃结果。
- **资源可控**: 全局 4 线程, 不再无限创建。
- **可观测**: `get_status()` 返回每个会议的队列状态, 方便调试和监控面板。

### 负面

- **cancel 不保证立即停止**: LLM 调用已经在 running 时无法强制 kill, 只能通过 generation_id 检查丢弃结果。已经产生的 API 调用不可撤回(浪费)。
- **单任务队列可能延迟**: 如果某次 LLM 调用超时(120s), 后续 task 会被 blocking (debounce 设计下这是期望行为 — 不会堆积, 只是最新任务等旧超时)。

### Migration

- `from ..task_manager import get_task_manager` 替换旧的直接 submit 模式。
- 需要修改 `fastapi_app.py` 中 `_process_chunk_sync` 和 `_process_chunk_background` 两处提交逻辑。
- 不需要改存储格式, 纯运行时变更。
