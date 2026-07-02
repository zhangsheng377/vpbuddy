# 0034. batch_docs 模板 {last_docs_block} plural 变量 vs controller.render_prompt singular {last_doc} — 修 3 stale test + 暴露隐性 bug

- **状态**: 已接受 (2026-07-02)
- **日期**: 2026-07-02
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (test-only 修复 + 1 个隐性真 bug 显式化)
- **依赖**: [ADR-0029](./0029-6-kinds-合-batch-docs-2-kinds.md) (commit `4ab04bb` 引入 batch_docs kind)
- **落地**: v0.8.1 (test fix + release bump, 见下面 "为什么不发新 release" update)

## 背景

v0.8.0 verification 时跑 `pytest src/tests/test_sub_session.py` 发现 3 个测试 stale 自 v0.7.0 commit `4ab04bb` (ADR-0029) 起就 broken, 但**没有 CI fail** — 原因见下. 这次把它们一起修.

### 3 个 stale test

| # | Test | 旧断言 | 真因 |
|---|------|--------|------|
| 1 | `TestRenderPrompt::test_subsequent_run_includes_previous` | `render_prompt(BATCH_DOCS_KIND, ...) == "上次的 req 文档" in p` | `BATCH_DOCS_KIND="batch_docs"` 走 controller `render_prompt()` 模板, 但 batch_docs.md 用 `{last_docs_block}` (plural), controller 只 escape/format `{last_doc}` (singular) → `{last_docs_block}` 被静默吞掉 |
| 2 | `TestParallelRun::test_run_one_round_parallel` | `len(results) == 6` | ADR-0029 落地 6→2 kinds, 真只跑 2 |
| 3 | `TestParallelRun::test_run_one_round_serial` | `len(results) == 6` | 同上 |

### 为什么 8d8c531 没发现?

v0.8.0 commit `8d8c531` (上一轮 fix) 修过 `TestRenderPrompt::test_uses_specific_template` 那 4 条 stale, 改用 `BATCH_DOCS_KIND` / `DEMO_KIND` 分流. 但只动了工具名断言, 没动 `last_doc` 那条 — 因为:

- 测试作者**误以为** controller `render_prompt()` 是 `batch_docs` kind 的渲染入口.
- 实际上 `batch_docs` 走 `sub_sessions/batch_docs.py::render_batch_prompt()` (专属), 那个用 `last_docs: dict` 正确注入 5 文档.
- controller `render_prompt()` 是 `demo` / 老 1-on-1 kind 的入口, 签名是单 `last_doc: str | None`.

→ controller `render_prompt("batch_docs", ...)` 在生产里**没人调** (只有 `run_one_round` 走 `_dispatch_kind` → `trigger_batch_docs` → `render_batch_prompt`). 这测试调的是死路径, 但 controller `render_prompt` 也不报错 — `.format()` 对未知 `{key}` 默认当字面量保留, 所以测试 fail 但**生产 prompt 是对的** (因为真生产路径不调它).

## 决策

### 1. Test 1 改用真生产路径 `render_batch_prompt`

```python
# src/tests/test_sub_session.py
def test_subsequent_run_includes_previous(self):
    last_docs: dict[str, Optional[str]] = {
        "req":   "## 上次的 req 文档",
        "arch":  "## 上次的 arch 文档",
        "tasks": "## 上次的 tasks 文档",
        "api":   "## 上次的 api 文档",
        "risk":  "## 上次的 risk 文档",
    }
    p = render_batch_prompt("MID", "## 累积", last_docs)
    for kind, content in last_docs.items():
        assert content is not None  # 类型收窄
        assert content in p, f"batch_docs prompt 缺 last_docs['{kind}'] 注入"
```

设计要点:
- 5 个 kind 都验, 防止以后只验 1 个漏掉
- 用 `Optional[str]` 类型注解 (与 `render_batch_prompt` 签名一致), 避免 pyright variance 报错

### 2. Test 2/3 改 `6` → `len(SCHEDULED_KINDS)` (动态断言)

```python
assert len(results) == len(SCHEDULED_KINDS)  # 当前 2, 未来加 kind 自动跟随
kinds = {r["session_id"].split(":")[-1] for r in results}
assert kinds == set(SCHEDULED_KINDS)
```

设计要点:
- 不写死 2, 用 `len(SCHEDULED_KINDS)` — 以后 ADR 加 kind (比如回到 6 kinds) 测试自动跟随, 不再 stale
- 顺带 assert `kinds` set 跟 `SCHEDULED_KINDS` 一致, 防止 dispatch 路由打错漏派/重派

### 3. TestParallelRun class docstring 更新

旧: `2026-06-22 ADR-0009 落地:ThreadPoolExecutor 真并行触发 6 doc_kind`
新: `2026-07-01 ADR-0029 落地:2 kinds (batch_docs + demo) 并行触发,非老 ADR-0009 的 6 kinds.`

## 设计取舍

### 为什么 controller.render_prompt 不改签名, 而是测试用专属函数?

`controller.render_prompt(doc_kind, ..., last_doc: str)` 是**单文档模板**的渲染器 — demo / req / arch / tasks / api / risk 老 6 kinds 的入口. 这些 kind 真 1-on-1 对应 1 文件, 1 `last_doc` 字符串合理.

`batch_docs` 是 1 LLM 写 5 文件的**特例**, 不该塞进同一签名 — 它要的是 5 文件 dict, 不是 1 文件 string. 强塞进同一签名就要么:
- (a) `last_doc: str | dict | list` 联合类型, 模板里要 if/else — 丑
- (b) 删 controller.render_prompt, 全用 render_batch_prompt — 改 5 个老 templates 工作量, 不在本 PR 范围

→ **保留 controller.render_prompt 单文档签名**, 文档化它**不接受 batch_docs kind** (加 docstring 警告).

### 为什么不在 controller.render_prompt 加 "未识别 key" 报错, 提前暴露 bug?

`.format(**kwargs)` 默认对未知 `{key}` 静默保留为字面量, 这是 Python stdlib 行为. 改成 `format_map` + 自定义 mapper 检测会:
- 改 1 个 helper, 7+ 个调用点测试要重写
- 容易误伤 (模板里 CSS/JS 偶尔有 `{` 配 `}`)

→ **不加** . 修测试 + ADR 文档化隐性 bug 路径, 防止后人再踩.

### 为什么不发新 release?

写 ADR 时 (2026-07-02 上午): 同 ADR-0033 口径, 本 PR 只改 test + ADR 文档, 用户可见 0 影响 → 不发 v0.8.1, 直接 commit 到 main.

**2026-07-02 下午 update**: 张胜东 override 这条决策, 直接说要发 v0.8.1. 既然用户决定 release, 本 ADR 也跟着 release 一起落地:
- pyproject.toml version 0.8.0 → 0.8.1
- src/vpbuddy/__init__.py __version__ 0.8.0 → 0.8.1
- design doc v1.33 → v1.34 (本 release 标记)
- commit + `git tag v0.8.1 && git push --tags` 触发 tauri-multi-build.yml CI 出 3 平台 release artifact

→ 本 ADR 最终状态: **发 v0.8.1 release**, 即便产品代码 0 改动 (跟 semver 严格说不算 minor, 但跟用户决策走).

## 实施细节

| 文件 | 改动 |
|------|------|
| `src/tests/test_sub_session.py` | +1 import (`render_batch_prompt`), TestRenderPrompt.test_subsequent_run_includes_previous 改用真生产路径, TestParallelRun 2 test 断言 6→`len(SCHEDULED_KINDS)`, class docstring 更新 |
| `docs/decisions/0034-...md` | 本文件 |

**LOC**: +20 lines (新测试体), -10 lines (旧 6 断言), 0 product code change, 0 API change, 0 breaking change

## 后果

### 积极
- ✅ 3 个 stale test (v0.7.0 commit `4ab04bb` 起 broken) 全部修复 — test_sub_session.py **33/33 pass** (排除 5 个 KbStatus pre-existing flake, 不在本 ADR 范围)
- ✅ **暴露 1 个隐性 bug**: controller `render_prompt("batch_docs", ...)` 静默吞掉 `{last_docs_block}`. 修法 = 不调它, 改用 `render_batch_prompt`. ADR 文档化防止后人再踩.
- ✅ 2/3 test 改动态断言 `len(SCHEDULED_KINDS)`, 未来 ADR 加 kind 不再 stale

### 消极
- ❌ 隐性 bug 没在 controller 端 patch — 是"文档化 + 走真路径" workaround, 理论上后人再调 `controller.render_prompt("batch_docs", ...)` 仍会中招
- 缓解: controller.render_prompt docstring 加显式警告 "do not use with 'batch_docs' kind" (本 PR 不做, 留 follow-up)

### 风险
- 无. 本 PR 不改 product code, 不改 API, 不改依赖. 纯 test + ADR.

## 关联

- 上游: [ADR-0029](./0029-6-kinds-合-batch-docs-2-kinds.md) (commit `4ab04bb` 引入 batch_docs kind, 3 个 stale test 的源头)
- 上游: [ADR-0033](./0033-e2e_realtime-fixture-wait-server.md) (test-only fix 不发新 release 的先例)
- 上游: commit `8d8c531` (v0.8.0 修了 `test_uses_specific_template` 那 4 条 stale, 本 ADR 修剩下 3 条)
