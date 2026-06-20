# VPBuddy

> **人机协同会议操作系统级 AI 助手** —— 面向软件开发公司 VP / 售前负责人 / 项目负责人。

VPBuddy 不是传统意义上的 AI 助手,而是运行在会议中的协同系统:

- 人类负责决策与主导
- VPBuddy 负责理解、结构化、生成与演化

在腾讯会议 / 飞书 / 钉钉 / Zoom 中,VPBuddy 实时完成:

- 会议理解与结构化建模
- 需求分析与追问
- 解释材料生成
- Sub-agent 并行推理
- 交互 Demo 与交付物生成
- 企业 / 个人 / 行业知识库调用
- 软件交付资产实时生成

核心定义:

> **会议 = 人机协同过程**  
> **VPBuddy = 会议中的 AI 协同执行系统**

---

## 仓库结构

```
vpbuddy/
├── README.md                                ← 本文件,项目总览
├── LICENSE                                  ← 许可证
├── docs/                                    ← 文档总目录
│   ├── product-spec/                        ← 产品说明书(原始材料)
│   │   ├── README.md                        ← 产品文档索引
│   │   ├── VPBuddy_产品说明书.docx          ← 原始 Word 文档
│   │   ├── VPBuddy_产品说明书.md            ← Markdown 渲染副本(GitHub 友好)
│   │   └── source/
│   │       └── 0620.zip                     ← 2026-06-20 原始材料压缩包备份
│   ├── design/                              ← 系统设计文档(待写)
│   │   └── README.md                        ← 占位说明
│   ├── research/                            ← 调研资料 / 行业参考(待写)
│   │   └── README.md                        ← 占位说明
│   └── decisions/                           ← 架构决策记录 ADR(待写)
│       └── README.md                        ← 占位说明
├── ui-mockups/                              ← UI 原型截图
│   ├── README.md                            ← 截图索引
│   ├── UI01-登录.png
│   ├── UI02-主屏幕.png
│   ├── UI02-主屏幕2.png
│   ├── UI03-解释材料.png
│   ├── UI04-会议时间线(需求演化).png
│   ├── UI05-知识库管理.png
│   ├── UI06-Agent协作详情.png
│   └── UI07-发送任务.png
└── src/                                     ← 最终实现位置(待启动)
    └── README.md                            ← 占位说明
```

## 当前状态

| 模块 | 状态 |
| --- | --- |
| 产品说明书 | ✅ 已有 v1(2026-06-20) |
| UI 原型 | ✅ 已有 8 张截图(2 张主屏幕变体) |
| 系统设计 | ⏳ 目录已预留,待写 |
| 行业调研 | ⏳ 目录已预留,待写 |
| 架构决策记录 (ADR) | ⏳ 目录已预留,待写 |
| 代码实现 | ⏳ 目录已预留,待启动 |

## 写作约定(规划)

- **产品说明书**: 变更先改 `.docx`,再同步 `.md`(以 `.docx` 为准)
- **设计文档**: 放在 `docs/design/`,每篇独立 `.md`,顶部有"状态/作者/更新日期"元信息
- **决策记录**: 放在 `docs/decisions/`,文件名 `NNNN-标题.md`(参考 MADR 模板)
- **UI 截图**: 文件名保持 `UI<编号>-<页面名>.png` 格式,新增时在 `ui-mockups/README.md` 索引

## 相关链接

- **GitHub**: <https://github.com/zhangsheng377/vpbuddy>
- **产品说明书**: [docs/product-spec/VPBuddy_产品说明书.md](docs/product-spec/VPBuddy_产品说明书.md)
- **UI 原型**: [ui-mockups/](ui-mockups/)

## 维护者

张胜东(@zhangsheng377)
