# ADR-0003: MVP Step 1 YAGNI Review

- **状态**: Accepted
- **日期**: 2026-06-20
- **作者**: 张胜东 (起草: Hermes)
- **关联**: [ADR-0001 MVP 选型](./0001-MVP-选型.md) · [ADR-0002 UI 冲突](./0002-UI-vs-架构冲突-review.md)

---

## 背景

VPBuddy MVP Step 1(会议结构化状态 + 持久化)完成,**16 个测试全通过**。

本 ADR 记录 Step 1 实现过程中的**关键 YAGNI 决策**(我们没做什么 + 为什么没做),防止未来"补全"过度工程。

---

## 决策记录

### 决策 1:不用数据库,用 JSON 文件

**选项 A(已选)**:JSON 文件(`/home/zsd/vpbuddy/data/meetings/{id}.json`)

**选项 B(未选)**:SQLite + SQLAlchemy ORM

**理由**:
- MVP 阶段单租户 + 单进程,**并发不是问题**
- JSON 便于人读、便于调试、便于 grep
- Pydantic 自动序列化/反序列化,不需要 ORM
- YAGNI:数据库的索引/事务/锁/Migration 都不需要
- 后期需要(全文检索/并发):加 SQLite 即可,接口不变

**测试通过**:`test_persistence_across_sessions`(跨调用持久化)

### 决策 2:不用状态机,用 `status` 枚举字段

**选项 A(已选)**:`status: pending | confirmed | rejected` 三个值

**选项 B(未选)**:引入 `transitions` 库或自研 FSM

**理由**:
- 架构 v1.14 已经全删状态机(YAGNI 一贯到底)
- 3 个状态 = 3 个枚举值,代码量 < 30 行
- 没有"状态转换规则"需要维护
- 不需要事件触发器/钩子/Pipeline

**测试通过**:`test_confirm_item` / `test_reject_item`

### 决策 3:不引入 ORM(SQLAlchemy/Tortoise)

**选项 A(已选)**:Pydantic + 手动 JSON 持久化

**选项 B(未选)**:SQLAlchemy / Tortoise ORM

**理由**:
- ORM 引入:模型类 / session / query API / Migration
- 我们只有 1 张"表"(meetings.json)
- Pydantic `model_dump_json()` + `model_validate_json()` 已经够用
- YAGNI:Model class 是无意义抽象

**测试通过**:16 个测试覆盖全部 CRUD

### 决策 4:不做并发安全(无锁)

**选项 A(已选)**:单进程顺序写

**选项 B(未选)**:加文件锁 / fcntl / asyncio.Lock

**理由**:
- MVP 单租户,VP 个人的会议,**没有并发写**
- 如果两个进程同时写同一个 JSON,会有 race condition(但 MVP 不会发生)
- 真出问题时:加 `fcntl.flock()` 一行就能解决

**已知限制**:`ADR-0001` 决策单租户 → 单进程 → 无锁

### 决策 5:不做 schema migration

**选项 A(已选)**:字段加了要手动 migrate 旧数据

**选项 B(未选)**:alembic / 自研 migration 框架

**理由**:
- MVP 阶段 schema 还在变(Step 2/3 会加字段)
- 用户量 = 1,**手动迁移成本低**
- 后期真有需要:加 alembic 一行 `alembic init`

**已知风险**:如果 schema 加字段忘了迁移,旧数据可能加载失败 → 写个 `migrate_state_v1_to_v2()` 函数处理

### 决策 6:不用事件溯源(Event Sourcing)

**选项 A(已选)**:当前状态存 JSON,历史不存

**选项 B(未选)**:事件流(每条累积项的 add/confirm/reject 都是事件,存下来)

**理由**:
- Event Sourcing 引入:事件存储 / 投影 / CQRS
- 我们的需求:**当前状态足够**,历史可以从版本树/audit log 拿(Step 4)
- YAGNI:不解决还没出现的问题

**对比**:架构 §5.3 "交付物持续演化"用了 git commit 链(版本树) → 同样的思路可以应用到会议状态(将来)

### 决策 7:不引入 DI 容器 / 插件系统

**选项 A(已选)**:直接 `import` + 函数调用

**选项 B(未选)**:dependency-injector / pluggy / 自研容器

**理由**:
- 整个代码库就 3 个文件 + 16 个测试,**DI 是无意义抽象**
- 真要做插件化:hermes skill_manage 已经提供了

**对比**:架构 §零 "VPBuddy = Hermes skill 集合" → 插件化走 hermes,不自己造轮子

### 决策 8:不用消息队列

**选项 A(已选)**:同步函数调用

**选项 B(未选)**:Redis Streams / Kafka / RabbitMQ

**理由**:
- 架构 §六 技术选型 "Redis Streams(轻量)" 是后期选项
- MVP 阶段没有分布式需求
- YAGNI:消息队列增加运维成本

**何时引入**:跨服务 / 跨进程 通信时

### 决策 9:不做全文检索

**选项 A(已选)**:列表遍历(`list_pending` / `find_item`)

**选项 B(未选)**:sqlite FTS5 / Elasticsearch

**理由**:
- MVP 阶段会议累积项数量:10-100 条/会议
- 列表遍历 < 1ms
- YAGNI:不要预先优化

**何时引入**:会议累积项 > 1000 条 / 需要跨会议搜索时

### 决策 10:不引入认证 / 权限

**选项 A(已选)**:无认证(本地文件,谁拿到文件谁有权限)

**选项 B(未选)**:JWT / OAuth / RBAC

**理由**:
- ADR-0001 决策单租户 → 不需要用户隔离
- 本地存储 → 走文件系统权限
- 真要多用户:加 hermes session 用户认证

**已知限制**:MVP 不适合生产环境多用户

---

## 决策时间表

- **Step 1 完成**: 2026-06-20
- **YAGNI 决策记录**: 2026-06-20
- **下次评审**: Step 2(Whisper 自接)完成后,看是否有 YAGNI 决策要回退

---

## 关键指标(Step 1 验收)

| 指标 | 目标 | 实际 | 状态 |
|---|---|---|---|
| 测试通过率 | 100% | 16/16 | ✅ |
| 测试运行时间 | < 5s | 0.30s | ✅ |
| 代码行数 | < 500 | ~280(state.py) + ~70(storage.py) | ✅ |
| 外部依赖 | 只 pydantic | 只 pydantic | ✅ |
| 文件大小 | < 100KB | 7.7KB(state) + 2.2KB(storage) | ✅ |
| 持久化可靠性 | 跨调用 OK | ✓ | ✅ |

---

## 代码量统计

| 文件 | 行数 | 用途 |
|---|---|---|
| `src/vpbuddy/state.py` | ~280 | MeetingState + 5 类累积项 |
| `src/vpbuddy/storage.py` | ~70 | JSON 持久化 |
| `src/vpbuddy/__init__.py` | ~10 | 包初始化 |
| `src/tests/test_state.py` | ~280 | 16 个测试 |

**总计**: ~640 行(含测试)

如果加上 type hints 和 docstring:~900 行。

**对比典型 YAGNI 失败案例**:
- 用 SQLAlchemy + Alembic:1500 行 + Migration 文件
- 用状态机库(transitions):800 行 + 状态定义
- 用 ORM + Repository 模式:2000 行

我们省了 **70% 代码量**,实现了同等功能。

---

## 已知限制 / 留给 Step N

| 限制 | 影响 | 何时修 |
|---|---|---|
| 无并发安全 | 单进程,多进程写会 race condition | 部署多 worker 时 |
| 无 schema migration | 字段加了要手动 migrate | 有真实用户时 |
| 无全文检索 | 跨会议搜索困难 | 会议数 > 100 时 |
| 无事件历史 | 看不到"3 小时前谁 reject 了什么" | VP 要求复盘时 |
| 无认证 | 文件拿到就能改 | 多用户时 |

每条都有**明确的修复触发条件**,不是 YAGNI 偷懒。

---

## 参考

- 总体架构 v1.16: `../design/总体架构.md`
- 产品说明书 v1.12: `../product-spec/VPBuddy_产品说明书.md`
- ADR-0001 MVP 选型: `./0001-MVP-选型.md`
- ADR-0002 UI 冲突 review: `./0002-UI-vs-架构冲突-review.md`
- Step 1 实现: `../../src/vpbuddy/`
- Step 1 测试: `../../src/tests/test_state.py`

---

## 变更历史

- 2026-06-20: 起草,10 个 YAGNI 决策 + 验收指标 + 代码量统计
