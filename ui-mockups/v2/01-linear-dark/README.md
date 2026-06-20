# VPBuddy UI v2 — Linear Dark 风格

## 这是什么

VPBuddy UI 重做版(v1.16),适配 `docs/design/总体架构.md` v1.16。

**风格**:Linear.app(深色 + 极致精密 + Inter Variable + 半透明白边)

## 关键变化(vs v1 UI)

| # | v1 UI 问题 | v1.16 修复 |
|---|---|---|
| 1 | 6 张 UI 都有"投屏"按钮 | **全部删除**(v1.10 已删,UI 落后) |
| 2 | Agent 数量不一致(5 vs 6,名字不同) | **固定 5 个**:产品/架构/UI/数据/风险 |
| 3 | "AI 建议提问"合并了"疑问窗口+预准备内容" | **2 个独立窗口**(疑问 + 预准备) |
| 4 | 6 项交付物清单不一致 | **固定 6 项**:交互Demo/需求清单/技术架构/任务拆解/API设计/风险分析 |
| 5 | UI01 命名为"登录",实际是"加入会议" | **改名"加入会议"** |
| 6 | 状态按钮(已生成/生成中)混乱 | **统一**:done / generating / idle 三态 |

## 设计哲学(Linear Dark)

- **深色原生**:不是 dark theme applied to light,是 darkness as the native medium
- **半透明白边**:`rgba(255,255,255,0.08)` 而非实色
- **Inter Variable 510 weight**:Linear 标志性的"between-weight"——比 500 轻,比 400 重
- **content-density**:信息密度高,VP 一次能扫很多内容

## 3 个 Screen

1. **加入会议页** —— 平台选择 + ASR 源选择(v1.13 双轨 ASR 可见)
2. **主屏幕(三栏 + 底部 Agent 横条)** —— 5 Agent + 6 项交付物 + 4 窗口
3. **会议时间线** —— 6 列表格,每行 1 个事件(客户发言 → 累积 → 触发 → 交付物变化)

## 本地查看

```bash
# 直接在浏览器打开
open ui-mockups/v2/01-linear-dark/index.html
```

## 下一步

- 用户挑这个风格 → 转 Vercel Light 风格出第 2 个 HTML 做对比
- 选完后 → 用 `claude-design` 精修主屏幕(加交互细节)
- 推广 → 把旧 UI 截图移到 `ui-mockups/v1-archive/`
