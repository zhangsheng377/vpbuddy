# 源代码 (Source)

本目录用于 VPBuddy 的最终实现。

## 状态

⏳ 待启动。

## 计划

在启动实现前,先完成:

1. `docs/design/` 下的系统设计文档
2. `docs/decisions/` 下的关键 ADR(语言选型、Agent 框架、知识库选型等)
3. 技术选型确定后,在此目录初始化子项目(预期是 monorepo 结构,涵盖 backend / agent-runtime / frontend / knowledge-base 等子模块)

## 预期子目录(待定)

```
src/
├── backend/                 # 后端服务(API / 任务调度 / 持久化)
├── agent-runtime/           # Sub-agent 运行时(产品/架构/UI/数据/风险 Agent)
├── meeting-adapters/        # 会议平台适配器(腾讯会议/飞书/钉钉/Zoom)
├── artifact-engine/         # 交付物生成引擎(Demo / 页面 / API / 任务)
├── knowledge-base/          # 统一知识库系统(个人/企业/行业三层)
├── command-center/          # 指令控制层(自然语言指令解析与执行)
└── frontend/                # 前端 UI(与 ui-mockups/ 对应)
```

> ⚠️ **以上为初步规划,实际模块划分以 `docs/design/` 定稿为准。**
