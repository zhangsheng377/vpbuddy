|> **说明**:本文档是 VPBuddy 产品说明书的当前版本。
|> 
|> **版本历史**:
|> - **v2.5** (2026-07-13): **v0.22.7 — pause≠stop + chat注入子agent + close 120s兜底**: 客户端 `stop_capture({close_meeting: true})` 区分暂停/结束；`format_state_summary()` 注入 chat 历史 + 上传文件列表；`_close_meeting()` 延迟关闭 SSE
- **v2.4** (2026-07-12): **v0.22.6 — vision 三层通道 + mmx-cli**: OpenAI 兼容端点 → monkeypatch → mmx-cli MiniMax 原生 VLM 后备，图片识图永不 401
  1. **toolsets 扩展**: agent 工具集从 `["terminal","file"]` 扩展到 `["terminal","file","vision","web"]`
  2. **KB search 非阻塞**: `POST /api/kb/search` 改为 `run_in_executor`，不再阻塞 event loop
  3. **.env 自动加载**: 多路径 fallback + OPENAI_* 从 DASHSCOPE_API_KEY 兜底推导
  4. **gkd 无阈值**: 字数门槛去掉，hash 变化即触发文档生成
  5. **vision 三层逃生通道 (ADR-0054)**: OpenAI 兼容端点 (DashScope qwen-vl-max) → monkeypatch Hermes 路由 → mmx-cli MiniMax 原生 VLM 后备
  6. **mmx-cli 集成**: `npm install -g mmx-cli` + `mmx auth login`，图片识图永不 401
  7. **idle 文案**: `"未连接"` → `"录音就绪"` (录音断开 ≠ 服务断开)
|> - **v2.3** (2026-07-07): **v0.10 — 百炼 ASR 替换 + 文档自驱动**:
|>   1. **百炼 Fun-ASR-Realtime** (ADR-0046): 替换 funasr+pyannote 本地 GPU 推理，阿里云云端实时逐句转写，无需管理 GPU 模型
|>   2. **WebSocket 实时 ASR**: 客户端 PCM 流直连百炼，逐句回调写入 `cleaned_text`，延迟 <1s
|>   3. **文档自驱动 15s 轮询**: 不再等 close，15s 后无条件提交第一轮，之后每 30s 检查增量自动重生成
|>   4. **会议名长度**: 3-32 → 3-48 字符
|> - **v2.2** (2026-07-05): **v0.9.0 — 任务队列 + 经验蒸馏 + FastAPI 迁移 + BFF API**:
|>   1. **后台任务队列 #5** (ADR-0042): per-meeting debounce + generation_id + bounded ThreadPoolExecutor
|>   2. **经验蒸馏 Phase 1 #1** (ADR-0043): 会议结束时自动提取 6 类经验候选
|>   3. **FastAPI 迁移 #6** (ADR-0044): FastAPI + CORSMiddleware + StreamingResponse + OpenAPI 自动文档
|>   4. **BFF API #9** (ADR-0044): 会议聚合 + 设备状态
|> - **v2.1** (2026-07-04): **LLM env 透传 + fork 架构 + API 参考文档** (ADR-0040/0041)
|> - **v2.0** (2026-07-01): **8 项产品需求合入 v0.6** (ADR-0019~0025)

---

# VPBuddy 产品说明书 v2.5

> **v2.5** (2026-07-13 修订 — **v0.22.7**): 暂停≠结束 (客户端 `close_meeting` 参数 + 服务端延迟关闭) + chat 历史注入子 agent (ADR-0055) + 上传文件路径暴露给子 agent。详见 [总体架构 v1.48](../design/总体架构.md) + [API 参考 v0.22.7](../api-reference.md)。

> **历史版本**:v1.0-v1.13 已归档删除。

## 一、产品定位

VPBuddy 是面向软件开发公司 VP / 售前负责人 / 项目负责人的 人机协同会议操作系统级 AI 助手,**运行在 Hermes Agent 之上**(v1.6 新增底层说明)。一个会议 = Hermes 一个 session,系列会议 = 同一 session 持续。**v1.10 新增**: 两个为 VP 服务的主动推送能力——**疑问窗口**(AI 主动问 VP 没听清/有矛盾/需要确认的地方)和**预准备内容**(AI 主动准备客户可能要问的弹药,VP 决定用不用)。**v1.13 重大修订(2026-06-21 ADR-0008)**: **删除飞书妙记**(降为会后校准源也取消),数据源改为 **VP 桌面客户端麦克风/系统音频 loopback**(ADR-0004 自接 Whisper + pyannote),说话人校准改人工/stt_map 填入;飞书 SDK / miaoji_calibration.py / FeishuAdapter / Platform.FEISHU 全部删除。

它不是传统意义上的AI助手，而是运行在会议中的协同系统：

- 人类负责决策与主导

- VPBuddy负责理解、结构化、生成与演化

VPBuddy 直接接 VP 桌面客户端的麦克风/系统音频 loopback(ADR-0004 自接音频流 + Whisper + pyannote),后台并行做两件事:① 累积结构化信息(客户需求/业务目标/风险点),② 持续生成候选交付物(Demo/页面/数据模型/任务清单等)。VP 任何时候调取,都已有可投屏的候选交付物(由 hermes skill 持续生成):

- 会议理解与结构化累积

- 需求分析(后台,hermes skill)

- 解释材料生成(后台,hermes skill)

- Sub-agent并行推理(后台,hermes delegate_task)

- 交互Demo与交付物持续生成(后台并行,VP 调取即用;**可选生产模式**:VP 显式启用后,会议结束自动转生产:导出代码 + 推 GitHub + 部署测试);**v1.10 新增**: 疑问窗口(AI 主动问 VP)+ 预准备内容(AI 主动为 VP 准备弹药);**v1.13 重大修订(ADR-0008)**: 删除飞书 SDK / 妙记集成,数据源 = VP 桌面客户端麦克风/系统音频 loopback

- 企业/个人/行业知识库调用(hermes memory 持久化,跨会议连续)

- 软件交付资产持续累积+生成(后台并行);所有能力作为 **hermes skill 集合**实现,自动生成/复用/版本化

核心定义：

会议 = 人机协同过程

VPBuddy = 会议中的AI协同执行系统(运行在 Hermes Agent 之上,会议 = hermes session,系列会议上下文自动接上)

## 二、产品本质

VPBuddy不是单纯AI助手，而是三层系统融合：

1. 人机协同助手（用户体验层）

2. 会议操作系统（产品能力层）

3. 软件需求持续累积引擎(技术本质层)

## 三、系统结构

VPBuddy由五大系统组成：

1. 会议接入层（Join Meeting）

2. 会议理解与Agent系统（Runtime Layer）

3. 交付物生成层（Artifact Engine）

4. 指令控制层（Command Center）

5. 统一知识库系统（Knowledge Layer）

## 四、会议接入层

功能:会议入口与系统初始化;**v1.13 重大修订(ADR-0008)**: 数据源 = VP 桌面客户端麦克风/系统音频 loopback + Whisper + pyannote(本地模型);**飞书 SDK / 妙记 API 全部删除**。**VP 设备硬约束**:必须用桌面客户端 + 授权麦克风/系统音频

输入：会议链接、平台、项目名称、知识库选择

行为：

- 加入会议(通过平台 SDK)

- 初始化 ASR 通道(v1.12 双轨,默认 Whisper 自接:VP 设备 loopback + 服务端 faster-whisper + pyannote 说话人;可选 Zoom RTMS / 小鱼易连)

- 启动 Agent 系统

- 加载知识库

- 创建会议工作台 + 启动后台 3 轨(累积/生成/疑问+预准备);**v1.13 修订(ADR-0008)**: 飞书妙记会后校准删除,说话人识别由 pyannote 3.1 本地完成,人工/stt_map 填昵称

## 五、会议理解与Agent系统

会议持续累积结构化为(后台,无延迟约束;**来源:v1.13 (ADR-0008) Whisper + pyannote 本地模型**,说话人由人工/stt_map 填入):

- 客户需求

- 业务目标

- 功能点

- 风险点

- 待确认问题

Sub-agent系统：

- 产品Agent

- 架构Agent

- UI Agent

- 数据Agent

- 风险Agent

特点：并行推理系统，而非单一工具调用

## 六、交付物持续累积与按需投屏系统

在会议过程中后台持续生成候选交付物,VP 调取即用:**v1.16 固定 6 项交付物清单**(UI 必须统一显示,禁止增减):

| # | 交付物 | 主责 Agent | 内容 |
|---|---|---|---|
| 1 | **交互 Demo** | UI Agent + 产品 Agent | 可点击的 HTML/React 原型,VP 看 demo 的核心 |
| 2 | **需求清单** | 产品 Agent | 编号需求(REQ-001)+ 优先级 + 状态 + 关联客户原话 |
| 3 | **技术架构** | 架构 Agent | 模块图 + 技术选型 + 关键风险标注 |
| 4 | **任务拆解** | 产品 Agent + 风险 Agent | 任务卡片(含工期)+ 依赖关系 |
| 5 | **API 设计** | 数据 Agent | OpenAPI / GraphQL schema + 字段说明 |
| 6 | **风险分析** | 风险 Agent | 风险清单 + 缓解方案 + 合规审计 |

**为什么是 6 项**:太少(3 项)覆盖不全;太多(10+ 项)VP 看不过来;6 项 = 一次会议能 review 的上限(认知研究:7±2 短时记忆容量)。

核心机制:交付物后台持续生成+ 持续累积演化(无延迟约束)+ **VP 在过程中持续 steer**(改/跳/加/换/参考,任何时候给出方向性输入,AI 实时整合,无『已完成』概念);投屏/外发 = VP 任何时候想看/想发当前状态

### v1.41: session fork 架构(ADR-0040/0041, 2026-07-04)

**当前架构**: 1 个 master session + 2 个 fork 子 agent

```
master session: meeting:{mid}:vp-chat     ← 客户端 Chat 页签
   ├── fork → meeting:{mid}:batch         ← 继承 chat 历史, 生成 req/arch/tasks/api/risk
   └── fork → meeting:{mid}:demo          ← 继承 chat 历史, 生成 HTML 演示
```

| session_id | 角色 | 产生内容 |
|-----------|------|---------|
| `meeting:{mid}:vp-chat` | master — VP Chat 主控 | 对话历史, 上下文来源 |
| `meeting:{mid}:batch` | batch_docs — 5 文档一次 LLM 调用 | `req.md` / `arch.md` / `tasks.md` / `api.md` / `risk.md` |
| `meeting:{mid}:demo` | demo — HTML 原型 | `demo/` 目录 (多版本) |

**关键设计决策**:
1. **fork = parent_session_id**: 子 agent 从 master session 读取整个对话历史作为初始化上下文 (Hermes 0.18.0+ 原生支持)
2. **单向继承**: 子 agent 不修改 parent 历史, parent 感知不到 child
3. **LLM provider 统一**: chat / batch_docs / demo 都走 MiniMax-M3 (OpenAI 兼容 API, `https://api.minimax.chat/v1`), 确保 fork 上下文兼容
4. **prompt 差异化**: system prompt 各自不同 (chat=对话, batch_docs=结构化写作, demo=HTML), 但 parent_session_id 只继承对话历史, 不覆盖 system prompt

**历史背景**:
- v1.13 (Step 3): 6 个完全独立的子 session, 彼此不可见, 各自调 LLM
- v1.30 (ADR-0029): 6→2 合并 (batch_docs + demo), 但 chat 仍独立
- v1.41 (ADR-0041): 合并为 fork 模型, chat 上下文自动注入 doc 生成

**工作流**:
1. 用户在 chat 页签输入消息 → master session 累积上下文
2. controller 触发 batch_docs agent → fork 自 master (继承 chat 历史) → AIAgent.chat(prompt) → write_file × 5
3. controller 触发 demo agent → fork 自 master (继承 chat 历史) → AIAgent.chat(prompt) → write_file HTML
4. 文档生成后推 SSE doc-update 事件 → 客户端面板更新

**性能**: LLM 调用 6 次 → 2 次 (66%↓), 总文档生成时间 3-5min → 1-2min, 一致性提升 (5 文档共享 LLM 上下文)。

七、Steer 控制层(Steer Center,v1.5 改名)

VP与VPBuddy唯一交互入口(v1.5 改为『steer 入口』)

VP 在会议中**持续 steer(方向盘式引导)**:随时给出方向性输入,AI 边做边接收,边整合,边继续。**没有『已完成』概念**——不是"完成后的干预",而是"持续过程中的方向引导"。支持:

- 修改 UI/功能(steer Demo 方向)

- 跳过某项(steer 任务优先级)

- 加个字段(steer 数据模型)

- 调参考(steer 知识库约束)

- 控制 AI 行为(steer prompt)

本质:**方向盘式引导**(v1.5 改),不是"批改已完成的成果";VP 在过程中持续 steer,AI 持续整合。**v1.10 新增**两个为 VP 服务的主动推送(不是"推结果"): ① **疑问窗口**(AI 主动问 VP,持续听,识别模糊/矛盾/未澄清,生成疑问列表按紧急度排序) ② **预准备内容**(AI 主动准备客户可能问的问题 + 答案,VP 决定用不用)。**外发** = VP 想把当前状态导出/发送(VPBuddy 处理);**投屏** = VP 在会议客户端自己点"共享屏幕",**VPBuddy 不参与**

## 八、会议时间轴（需求演化系统）

记录不是日志，而是：

需求如何被逐步构建为系统的过程

包含：

- 客户发言

- VP理解

- Agent触发

- 交付物变化

## 九、统一知识库系统（核心升级）

知识库包含三类知识,采用**统一搜索模式**(v1.13 简化:删 v1.3 的"双模式"概念);后台**实时提取并展示**,VP 可改但**不阻塞 AI 继续做 demo**;**v1.13 起 UI 展示什么,AI prompt 就有什么**(张胜东纠正:不区分"AI 主动拉")。

1. 个人知识

- VP经验
- 决策逻辑
- 个人方案习惯

2. 企业知识

- PRD / MRD
- 技术规范
- UI规范
- 历史项目
- SOP与交付标准

3. 行业知识

- 行业标准方案
- 通用架构
- 竞品方案
- 最佳实践

作用：

知识**统一存**到 sqlite-vec(单一表 + embedding),按 query 做 RAG 检索,跨会议/跨类型。**v1.13 起 UI 展示什么,AI prompt 就有什么**(不区分"展示 vs 检索");子 session 读 meeting_state.json 时,所有累积项(REQ/RISK/QUE)都进 context,直接被 LLM 看到。详细机制见 [总体架构 v1.17 §九](../design/总体架构.md) + [ADR-0006](../decisions/0006-MVP-Step3-子session架构.md)

影响：

- Demo结构

- UI设计

- 技术架构

- API设计

- 交付标准

## 十、核心系统闭环

会议输入（语音/屏幕/文档）

↓

后台持续累积(无延迟约束)

↓

后台 Agent 并行推理

↓

知识库约束

↓

后台持续生成候选交付物

↓

↓

VP 持续 steer(任何时候)

↓

## 十一、产品本质总结

VPBuddy不是传统AI助手。

它是：

- 人机协同会议操作系统

- 软件需求持续累积+生成引擎

- 企业知识驱动的交付生成系统

VP 任何时候投屏/外发(无『完成』前提)

## 十二、版本历史

- **v2.4 (2026-07-12)**: v0.22.6 — toolsets 扩展 (vision+web) / KB 非阻塞 (run_in_executor) / .env 自动加载 / gkd 去阈值 / vision 配置看护 / idle 文案
- **v2.3 (2026-07-07)**: v0.10 — 百炼 Fun-ASR-Realtime 替换本地 ASR + WebSocket 实时逐句转写 + 文档自驱动 15s 轮询 (ADR-0046)
- **v2.2 (2026-07-05)**: v0.9.0 — 后台任务队列 / 经验蒸馏 Phase 1 / FastAPI 迁移 / BFF API (ADR-0042~0044)
- **v2.1 (2026-07-04)**: LLM env 透传 / fork 架构 / API 参考文档 (ADR-0040~0041)
- **v2.0 (2026-07-01)**: 8 项产品需求合入 v0.6 (ADR-0019~0025)
- v1.13 (2026-06-21): 删除飞书 SDK / 妙记 API, 自接 Whisper + pyannote (ADR-0008)
- v1.0-v1.12: 已归档删除