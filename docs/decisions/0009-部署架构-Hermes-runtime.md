# ADR-0009: VPBuddy 部署架构 — 以 Hermes Agent 作为生产 runtime

- **状态**: Accepted
- **日期**: 2026-06-21
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无(首次将总体架构 §0 "VPBuddy = Hermes skill 集合" 升级为独立 ADR)
- **被依赖**: [ADR-0001](./0001-MVP-选型.md) (MVP 选型) · [ADR-0004](./0004-MVP-Step2-ASR设计.md) (Step 2 ASR) · [ADR-0006](./0006-MVP-Step3-子session架构.md) (Step 3 子 session 循环架构) · [ADR-0008](./0008-ADR-0001-决策1-Superseded.md) (飞书删除)

---

## 背景与问题

总体架构 v1.7 (2026-06-20) 在 `docs/design/总体架构.md` §0 写过一段关键认知:
> "VPBuddy 不是自研 LLM 框架,**直接运行在 Hermes Agent 之上**。一个会议 = Hermes 的一个 session;系列会议 = 同一 session 持续;skill 自动生成/复用。"

但这段认知**只在总架构 §0 出现,没有独立 ADR**。实际工程上:
1. 部署文档 (`docs/部署/gpu服务器部署.md`) **完全没提**安装 Hermes,只写怎么装 GPU 模型
2. 顶层 `README.md` 没提 Hermes 是 runtime
3. `src/vpbuddy/__init__.py` 注释写了"运行在 Hermes Agent 之上",但 `import hermes` 0 命中
4. **没有 `pyproject.toml`**(只有 `requirements.txt`),未声明 `hermes-agent` 依赖
5. ADR-0006 写"用 `delegate_task` 并行" 但实际 `controller.py` 仍是手编循环(代码层 drift)
6. 真实使用场景 = VPBuddy 部署到非本机的生产服务器(用户私有服务器/客户机房/云),每台目标服务器都需要自己起 Hermes runtime

**问题**:我们嘴上说"运行在 Hermes 上",工程层完全没有对应物。一旦部署到新服务器,操作员不知道要先装 Hermes,也不知道 VPBuddy 的 LLM/session/skill/memory 全部委托给谁。

## 考虑的选项

### 选项 A — 自研 runtime(VPBuddy 独立 Python 包)
- VPBuddy 自己管理 LLM API key、session 持久化、skill 框架、记忆
- 与 Hermes 解耦
- 优点:可独立部署,无外部依赖
- 缺点:重造 4 套轮子(LLM 调度 / session 管理 / skill 库 / 记忆系统),与 ADR-0001 §0.3 不变量"不自研 LLM 框架"直接冲突

### 选项 B — 继续手编 5 Agent 循环,文档加 ADR 不改代码
- 保留 controller.py 手编循环,只加 ADR 描述"未来应该用 delegate_task"
- 优点:零代码风险
- 缺点:架构与代码持续 drift;总体架构 §0.1 表说"用 delegate_task",实际没有,继续骗自己

### 选项 C — **以 Hermes Agent 作为生产 runtime,代码层真接 `delegate_task`** ✅
- 目标服务器 = `pip install hermes-agent` + `pip install vpbuddy`
- VPBuddy = 装在 `~/.hermes/skills/vpbuddy/` 的 skill 集合
- 一次会议 = 一个 Hermes session,系列会议 = 同一 session 持续(已有)
- 5 Agent 并行 = `delegate_task`(替换 controller.py 手编循环)
- 部署文档重写:5 分钟部署 = `pip install hermes-agent` + `pip install vpbuddy` + `hermes skills install vpbuddy`
- 优点:架构与代码对齐;复用 Hermes 全部能力(上下文/工具/技能/会话/cron/子 agent/gateway);零自研
- 缺点:依赖 Hermes;V1.0 之前 controller.py 重构工作量较大

## 决策

**采用选项 C**。理由:
1. 与总体架构 §0.3 不变量一致(VPBuddy 不直接调 LLM / 不自管 session / 不自造 skill 框架 / 不自造记忆)
2. 与 ADR-0001 §6 决策 1 一致(LLM 框架 = Hermes Agent,非自研)
3. 真实生产场景 = 部署到客户服务器,必须给操作员一个"5 分钟起"的命令,选项 A 不可行
4. 投入产出比最高:用 Hermes 已有能力,几乎零自研成本

### 具体落地

| 项 | 现状 | ADR-0009 之后 |
|---|---|---|
| 部署到新服务器 | 装 Python 3.11 + requirements.txt + GPU 模型 | `pip install hermes-agent` + `pip install vpbuddy` + 装 GPU 模型 + `hermes skills install vpbuddy` |
| 一次会议 = ? | 进程级 meeting_id | **Hermes session_id**(`meeting:{mid}`) |
| 5 Agent 并行 | `controller.py` 手编 `asyncio.gather` | `delegate_task(tasks=[...5], toolsets=[...])`(Hermes 真并行 ThreadPoolExecutor) |
| 工具调用 | 自封装 subprocess/HTTP | **Hermes native tools** (terminal/file/web/browser) + MCP(通过 `hermes chat` subprocess 间接调用) |
| 知识库 | `knowledge_base.py` 自封装 sqlite-vec | 复用 Hermes memory + sqlite-vec(知识库作为 vpbuddy skill 暴露 schema) |
| Cron / 7×24 任务 | 没接 | 复用 Hermes cron 调度(可选扩展) |
| 跨 session 历史 | 手写 JSON 文件 | 改用 `hermes chat` subprocess + 外部 session_id 跟踪(详见 §具体落地) |
| LLM API key | 散落多处 | 集中到 `~/.hermes/.env`,VPBuddy 通过 `hermes chat` subprocess 间接调用 |

### 部署包结构(目标)

```
目标服务器
├── /home/{user}/.hermes/                  # Hermes runtime
│   ├── hermes-agent/                       # Hermes 源码
│   ├── venv/                               # Python venv
│   ├── .env                                # LLM API keys / 模型路径
│   ├── config.yaml                         # Hermes 配置
│   ├── skills/vpbuddy/                     # VPBuddy skill 包 ✅
│   │   ├── SKILL.md                        # 触发 + 文档
│   │   ├── scripts/
│   │   └── templates/
│   └── sessions.db                         # 会议历史
└── ~/vpbuddy/                              # VPBuddy 源码(开发模式)或 wheel
```

### 集成方式(2026-06-21 修正 — subprocess 优先,非 in-process import)

**当前 controller.py 实际做的事** (L188-208):
```python
# subprocess.run 调 hermes CLI,非 in-process import
cmd = ["hermes", "chat", "-q", prompt, "-Q"]  # 单次对话
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
```

| 集成方式 | 描述 | 何时用 | 状态 |
|---|---|---|---|
| **subprocess 调 `hermes chat`** | VPBuddy 进程 → `subprocess.run("hermes chat ...")` 阻塞等返回 | 6 文档生成(controller.py) | ✅ **当前使用** |
| **subprocess 调 `hermes chat` + 主 session 直写** | VPBuddyDIRECT=1 → controller 渲染 prompt 让主 session 写 | 6 文档生成(快速模式) | ✅ **当前使用** |
| **in-process `from hermes_agent import AIAgent`** | VPBuddy 进程内 import Hermes,共享内存 | 真 5 Agent 并行(替代 subprocess) | ⏳ **未来** (Step B,见后) |
| **Hermes daemon + HTTP/gRPC** | Hermes 起 daemon server,VPBuddy 调 SDK | 多 VPBuddy 实例共享 Hermes | ⏳ 远期 |

**Step B 决策** (下个 PR): 把 `subprocess.run("hermes chat")` 替换为 `from hermes_agent import AIAgent` + `delegate_task` 真并行 — 消除 5 分钟 × N 串行延迟。

### 安装命令(目标 — 5 分钟部署)

```bash
# 1. 装 Hermes(目标服务器一次)
pip install hermes-agent
hermes setup  # 交互式配 LLM API key

# 2. 装 VPBuddy skill
pip install vpbuddy
hermes skills install vpbuddy

# 3. (可选) 装 GPU 模型
vpbuddy setup-gpu

# 4. 启动 VPBuddy UI(VP/用户实际用的入口)
vpbuddy ui
# 浏览器打开 http://localhost:8765

# 5. (后台) 启动 controller 跑 6 文档生成
vpbuddy controller  # 7×24 跑,每 30s 轮询
```

**重要**: `hermes` TUI 是开发/调试工具,**不是** VPBuddy 用户界面。VP/用户永远从 `vpbuddy ui` 入口进。

## 后果

### 正面

1. **架构与代码对齐** — 文档/ADR/code 一致,可信度提升
2. **5 分钟部署** — 真实生产场景可一键起
3. **复用 Hermes 能力**:
   - 上下文管理(system prompt + memory + skills 注入)
   - 工具集(terminal/file/web/browser/MCP)
   - 技能库(`skill_manage` 自动生成/复用/版本化)
   - 会话历史(`session_search` 全文检索)
   - 子 agent 并行(`delegate_task` 真并行)
   - 定时任务(cron 7×24)
   - 多平台 gateway(未来扩展,可选)
4. **零自研 LLM/session/skill/memory 框架** — 与 ADR-0001 §0.3 不变量完全对齐
5. **跨会议连续** — VP 第二天开会,自动记得昨天所有内容(系列会议 = 同 session)

### 负面

1. **强依赖 Hermes** — VPBuddy 不能独立部署,必须装 Hermes
2. **controller.py 重构工作量大** — 把 `asyncio.gather` 5 个子任务改写为 `delegate_task(tasks=[...])` 调用,影响 Step 3 实现
3. **部署文档需重写** — 5 分钟部署命令需要测试通过(目前没真测过)
4. **Hermes 升级风险** — Hermes API 变更时 VPBuddy 需同步升级

### 中性

1. **pyproject.toml 必须存在** — 当前只有 requirements.txt,需补现代 Python 包声明
2. **vpbuddy 进程内 vs Hermes 进程内** — 取决于 skill 实现方式(scripts-only / Python module / MCP server),需后续 ADR 决定
3. **Hermes version 兼容性** — 需在 pyproject 声明 `hermes-agent>=0.16.0,<1.0`

## 实施路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| **Step A**(本次) | 写 ADR-0009 + 同步文档 + 补 pyproject.toml + __init__.py 进度表 | 🟢 当前 |
| **Step B**(下个 PR) | `controller.py` 重构:5 Agent 改用 `delegate_task` + 写 `hermes-integration` 测试 | ⏳ 待办 |
| **Step C**(再下个) | 部署文档真测:5 分钟命令从 0 到跑通端到端 | ⏳ 待办 |
| **Step D**(可选) | 把 vpbuddy 打包成 PyPI wheel + Hermes skill 一体化 | ⏳ 远期 |

## 关联文档

- [总体架构 §0 运行时基础](../design/总体架构.md)
- [ADR-0001 §6 决策 1 LLM 框架 = Hermes](./0001-MVP-选型.md)
- [ADR-0006 Step 3 子 session 循环架构](./0006-MVP-Step3-子session架构.md)
- [ADR-0008 飞书删除](./0008-ADR-0001-决策1-Superseded.md)
