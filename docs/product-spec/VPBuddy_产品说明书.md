|> **说明**:本文档是 `VPBuddy_产品说明书_v2.0_2026-07-01.md` 的 Markdown 渲染。
> 原始 .docx 文件同目录保留。
>
> **版本历史**:
> - **v1.0 - v1.13**: 见 `VPBuddy_产品说明书_v1.*_2026-06-20.md` (历史归档, 都已过时)
> - **v2.0** (2026-07-01): **8 项产品需求合入 v0.6**:
>   1. **录音支持麦克风 + 内录** (Linux 0 操作, macOS 需 BlackHole, Windows WASAPI loopback 0 操作) — 见 ADR-0021
>   2. **chat 页面支持文件/图片上传** (复用 KB 上传 API) — 见 ADR-0023
>   3. **知识库方案废弃旧库 + 改用户主动上传 + 会议隔离** (旧 sqlite-vec 全部删, 切 Chroma 嵌入式) — 见 ADR-0019/0020
>   4. **首页录音强制会议选择/创建** (按钮 disabled 直到选了旧会议或输入新会议名) — 见 ADR-0022
>   5. **chat 允许 agent 主动提问** (6 doc 完成 / 风险命中 / demo 新版本 / 停顿 / 节点 5 类 trigger, 可关) — 见 ADR-0023
>   6. **demo 版本号 + 多版本切换** (v1/v2/v3... 增量存档, 客户端可切) — 见 ADR-0024
>   7. **6 doc / demo / chat agent 都能网络搜索 + KB 检索** (DDG + Chroma 检索当前会议) — 见 ADR-0025
>   8. **RAG 切分逻辑跟项目解耦** (切 Chroma 嵌入式, 升级只换 framework 不动业务) — 见 ADR-0019
>
> 详见 [总体架构 v1.21](../design/总体架构.md) + [ADR-0019 ~ 0025](../decisions/)

---

# VPBuddy 产品说明书 v2.0

> **v2.0** (2026-07-01 修订 — **8 项需求合入**): 录音双轨(麦克风+内录) / chat 上传+agent 主动 / KB 切 Chroma 嵌入式+用户主动上传+会议隔离 / 首页强制会议选择 / demo 多版本 / agent 工具(web+KB)。详见 [ADR-0019 ~ 0025](decisions/)。

> **历史版本**:v1.0-v1.13 见 `VPBuddy_产品说明书_v1.*_2026-06-20.md`(都已过时,仅留作历史)

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

### v1.13: 6 个子 session 常驻循环实现(Step 3)

**核心架构**:每种交付物由 1 个**独立常驻子 session** 持续维护,共 6 个:

| session_id | doc_kind | 输出 | 维护的文档 |
|---|---|---|---|
| `meeting:{mid}:req` | req | Markdown 需求清单 | `docs/{mid}/req.md` |
| `meeting:{mid}:arch` | arch | Markdown 架构图 | `docs/{mid}/arch.md` |
| `meeting:{mid}:tasks` | tasks | Markdown 任务卡片 | `docs/{mid}/tasks.md` |
| `meeting:{mid}:api` | api | OpenAPI / GraphQL schema | `docs/{mid}/api.md` |
| `meeting:{mid}:risk` | risk | Markdown 风险评估 | `docs/{mid}/risk.md` |
| `meeting:{mid}:demo` | demo | **可运行的 HTML/代码/mermaid** | `docs/{mid}/demo/` |

**子 session 怎么工作**:
1. 同一个 `session_id` 跨轮触发 → **Hermes 自动保留历史上下文**
2. 每次触发:读 MeetingState JSON + 自己之前的 doc → 判断是否更新 → **直接 write_file/patch 改文档**(不输出 JSON 让别的进程写)
3. 后台循环:`sub_session_controller.py` 脚本 + `hermes cron` 每 30s 触发一轮

**关键不做的**(YAGNI):
- ❌ 自己设计 VPBuddy 专用 tool(直接用 Hermes 通用 tool)
- ❌ 自己实现 session 持久化(用 Hermes `session_search` + 同 `session_id`)
- ❌ 自己设计知识库"双模式"(统一 sqlite-vec + 跨会议 RAG)
- ❌ 子 session 输出 JSON 让别人写(LLM 自己写文件)
- ❌ 注入量精确控制(跑起来再说)

详见 [ADR-0006](../decisions/0006-MVP-Step3-子session架构.md) + [总体架构 v1.17 §三](../design/总体架构.md)

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

- v1.0: 初版
- v1.1: 去"实时"
- v1.2: 后台并行生成
- v1.3: 知识库双模式
- v1.4: 展示不注入 prompt
- v1.5: Steer 控制层
- v1.6: Hermes-native + 可选生产
- v1.7: 投屏 = 会议原生
- v1.8: 回退 session 生命周期(YAGNI)
- v1.9: VPBuddy 连投屏按钮都不提供
- v1.10: 疑问窗口 + 预准备内容
- v1.11: ~~重大简化 — 默认用平台原生 ASR/转写~~ → **Superseded by ADR-0008 (2026-06-21)**
- **v1.13 (2026-06-21 重大修订 by ADR-0008): 删除飞书 SDK / 妙记 API** — 数据源改为 VP 桌面客户端麦克风/系统音频 loopback(ADR-0004 自接 Whisper + pyannote);说话人校准改人工/stt_map 填入;飞书 SDK / miaoji_calibration.py / FeishuAdapter / Platform.FEISHU 全部删除 (commit `5048936`);详细决策见 `docs/decisions/0008-ADR-0001-决策1-Superseded.md`;**VP 设备硬约束保留**:必须桌面客户端 + 麦克风授权