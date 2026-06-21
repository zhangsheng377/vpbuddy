# VPBuddy — MVP Step 1 实现

> **状态**: Step 1 完成 ✅(2026-06-20)
> **范围**: 会议结构化状态(单一可信源)+ JSON 持久化

## 这是什么

VPBuddy MVP **Step 1** 的最小可工作实现(MVP):
- `MeetingState`:Pydantic 定义的会议结构化状态对象
- `MeetingStorage`:JSON 持久化(NFS)
- 5 类累积项:`Requirement / Goal / Feature / Risk / Question`
- 说话人映射 + 元数据

## 文件结构

```
src/
├── vpbuddy/
│   ├── __init__.py      # 包初始化
│   ├── state.py         # MeetingState + 5 类累积项(Pydantic)
│   └── storage.py       # MeetingStorage(JSON 持久化)
└── tests/
    └── test_state.py    # 16 个测试(全通过)
```

## 数据模型

```python
class MeetingState(BaseModel):
    meeting_id: str             # 自动生成
    platform: Platform          # local (默认) / tencent / dingtalk / wecom (YAGNI)
    project_name: Optional[str]
    started_at: str            # ISO 8601 UTC

    # 5 类累积项(单一可信源)
    requirements: List[Requirement]      # 客户需求 REQ-001
    goals: List[Goal]                     # 业务目标 GOAL-001
    features: List[Feature]               # 功能点 FEAT-001
    risks: List[Risk]                     # 风险点 RISK-001
    open_questions: List[Question]        # 待确认问题 QUE-001

    # 元数据
    speaker_map: Dict[str, str]   # speaker_id -> speaker_name
    last_updated: str              # ISO 8601 UTC
```

## 快速使用

```python
from vpbuddy.state import MeetingState, Platform, Priority
from vpbuddy.storage import create_storage

storage = create_storage()

# 1. 创建会议
state = MeetingState(platform=Platform.FEISHU,
                     project_name="XX公司-ESG需求沟通会")
state.add_requirement("碳排放数据统一管理", priority=Priority.HIGH)
state.add_goal("碳中和目标")
state.add_question("是否支持 Scope 3?", is_urgent=True)

# 2. 说话人映射
state.register_speaker("u_client", "张总")

# 3. 保存
storage.save(state)
print(f"Created: {state.meeting_id}")

# 4. 跨调用(下次开会)— 重新加载
loaded = storage.load(state.meeting_id)
loaded.add_risk("排放因子来源不确定")
loaded.confirm_item("requirement",
                    loaded.requirements[0].id,
                    speaker_name="张总")
storage.save(loaded)

# 5. 查询
pending = loaded.list_pending()  # 按优先级排序
for item in pending:
    print(f"  [{item.priority.value}] {item.text}")

stats = loaded.stats()
print(stats)
```

## 测试

```bash
PYTHONPATH=src python3 -m pytest src/tests/test_state.py -v
```

**16 个测试,全通过(0.30s)**:

- 9 个 CRUD 测试(create / add / confirm / reject / list / find / etc)
- 5 个 Storage 测试(save / load / persist / list / delete)
- 1 个端到端测试(完整会议流程)
- 1 个跨调用持久化测试(**Step 1 关键验证**)

## 设计原则

### YAGNI(不引入)

- ❌ **状态机**:用 `status` 字段枚举(`pending/confirmed/rejected`)代替
- ❌ **数据库**:用 JSON 文件,后续需要再加 SQLite
- ❌ **锁**:本地单进程,先不考虑并发
- ❌ **ORM**:Pydantic + 手动持久化就够
- ❌ **system prompt 注入**:约束不在 LLM prompt 里,只给 VP 看

### 简单胜过完美

- **单一 JSON 对象**:全部累积项在一个 `MeetingState` 里
- **自动时间戳**:每次 `_touch()` 更新 `last_updated`
- **人类可读 ID**:`REQ-A1B2C3`(前 6 字节 hex)
- **类型安全**:Pydantic 验证,错误早暴露

### 跨调用持久化

Step 1 的**关键验证**是"会议开始 → 会议结束 → 下次会议开始,状态能跨调用持久化":
- 存到 NFS JSON 文件(`/home/zsd/vpbuddy/data/meetings/{meeting_id}.json`)
- hermes session 通过 `MeetingState` 引用持续累积
- 验证见 `test_persistence_across_sessions`

## 下一步(Step 2)

Step 2 = **自接音频流 + Whisper + pyannote** (ADR-0004):
1. VP 设备 loopback(AVAudioEngine / WASAPI / PulseAudio)
2. 服务端 `faster-whisper` 流式转写
3. `pyannote-audio 3.1` 说话人聚类
4. 转写段 → 自动调用 `state.add_requirement(...)` 累积
5. ~~飞书 REST API 会后校准~~ → ⚠️ **Superseded by ADR-0008 (2026-06-21)**;说话人校准改人工/stt_map 填入

详见 `docs/research/asr-speaker-diarization-survey.md` v2。

## 数据存储位置

- **开发**:`/home/zsd/vpbuddy/data/meetings/{meeting_id}.json`(默认)
- **生产**(MVP):`/mnt/nfs_fn/zsd_server/codes/vpbuddy/data/meetings/`
- **迁移**:改 `create_storage(data_dir=...)` 即可

## 已知限制 / YAGNI 留给 Step N

- ⚠️ 无并发安全(单进程用即可)
- ⚠️ 无 schema migration(JSON 字段加了要小心)
- ⚠️ 无全文检索(需要时再加 sqlite FTS)
- ⚠️ 无权限隔离(单租户,MVP 阶段不需要)
- ⚠️ 无版本树(每条累积项只有当前状态,需要时再加 history)

## 参考

- 总体架构 v1.16: `../docs/design/总体架构.md`
- 产品说明书 v1.12: `../docs/product-spec/VPBuddy_产品说明书.md`
- ADR-0001 MVP 选型: `../docs/decisions/0001-MVP-选型.md`
- ADR-0002 UI vs 架构冲突: `../docs/decisions/0002-UI-vs-架构冲突-review.md`
- ASR 调研 v2: `../docs/research/asr-speaker-diarization-survey.md`
