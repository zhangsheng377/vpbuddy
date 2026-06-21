# ADR-0008: ADR-0001 决策 1 (飞书第一平台) Superseded

- **状态**: Accepted (2026-06-21)
- **作者**: 张胜东 (起草: Hermes)
- **替代**: ADR-0001 决策 1 部分(原"飞书作为第一平台");其他 4 决策(6 步/单租户/MVP 私有/本地存储)**继续有效**
- **Superseded by**: [ADR-0004 MVP Step 2 — Whisper + pyannote 自接音频](./0004-MVP-Step2-ASR设计.md)

---

## 背景

ADR-0001 (2026-06-20 Accepted) 决策 1 写:**第一平台 = 飞书**(理由: 妙记 300 分钟/月免费 + 自动说话人识别 + API 完整)。

但 ADR-0004 (同 2026-06-20 Accepted,**晚于 ADR-0001 起草**) 明确指出:

> 飞书/腾讯/钉钉的"实时字幕"是**客户端 UI**,**没有开放流式 API 给开发者**(2026-06-20 调研确认)

并确立了真实架构:
- **数据源 = VP 桌面客户端麦克风/系统音频 loopback → 16kHz mono PCM → GPU 服务器**
- **链路 = WhisperProvider + PyannoteDiarizer**(本地代码,纯自实现)
- **说话人识别 = pyannote 3.1**(本地模型,不依赖会议平台关联用户昵称)

## 真实架构(2026-06-21 用户确认)

| 项 | 原 ADR-0001 决策 1 | 实际真实架构 |
|---|---|---|
| 第一平台/数据源 | 飞书(会议平台作为数据源) | **VP 桌面客户端麦克风/系统音频**(loopback 抓 PCM) |
| 说话人识别 | 飞书用户关联(自动) | **pyannote 3.1**(本地模型) |
| 飞书妙记校准 | Step 5 计划拉妙记回校准 | **不需要**(用户确认:不接飞书 input/output) |
| 输出到飞书 | 未明确,易误解 | **不需要**(6 份文档本地存储,不推飞书) |

## 决策

### 1. ADR-0001 决策 1 → Superseded by ADR-0004

**原**: "第一平台 = 飞书"
**新**: "第一平台/数据源 = VP 桌面客户端 loopback 音频(ADR-0004 自接)"

ADR-0001 决策 2-5 (6 步拆分 / 单租户 / MVP 私有 / 本地存储) **继续有效**,**不**受本 ADR 影响。

### 2. ADR-0004 Step 5 → Superseded(整段删除)

**原**: "Step 5: 飞书妙记会后校准 + 双源融合"(ADR-0004 §MVP 6 步拆分表)
**新**: **MVP 6 步 → 5 步**。Step 5 整段删除,说话人校准改人工/stt_map 填入。

### 3. 代码现状(全部删除)

下列代码作为历史/YAGNI 价值低(飞书特定逻辑,跟 ADR-0004 自接架构无关),**全部删除**:

| 文件 | 处理 |
|---|---|
| `src/vpbuddy/miaoji_calibration.py` (317 行) | **删** |
| `src/tests/test_miaoji_calibration.py` (5 测试) | **删** |
| `src/vpbuddy/platforms.py` FeishuAdapter | **删**(其他 3 平台 adapter 保留作 YAGNI) |
| `src/vpbuddy/state.py` `Platform.FEISHU` | **改**为 `Platform.LOCAL` (默认),`ZOOM` 改名 `WECOM` |
| `src/tests/test_platforms.py` feishu 测试 (3 个) | **删** |
| `src/tests/test_state.py` Platform.FEISHU | **改** Platform.LOCAL |
| `src/tests/test_sub_session.py` Platform.FEISHU | **改** Platform.LOCAL |
| `src/vpbuddy/README.md` 飞书引用 (3 处) | **改**为 LOCAL + Superseded 标记 |

**执行日期**: 2026-06-21 commit `5048936` (8 files, +29/-515 行)

**验证**: GPU pytest 75 passed, 3 skipped(从 89 减 14 个飞书相关测试,符合预期)

**其他 3 平台 adapter** (Tencent/DingTalk/WeCom): 保留作 YAGNI,**未来真有多平台客户时再启用**(低优先级)。

### 4. 文档同步

- `docs/decisions/0001-MVP-选型.md` 决策 1 部分 → 加 Superseded 标记 + 指向本 ADR
- `docs/decisions/0004-MVP-Step2-ASR设计.md` §MVP 6 步表 → Step 5 标 superseded
- `docs/部署/踩坑记录.md` → 加 §17 记录本次决策演进
- 代码注释 (`transcript.py`/`engine.py`/`__init__.py`) → 改"飞书妙记校准"为"人工/stt_map 填入"

### 5. 产品说明书 v1.12(待用户决策)

**当前最新版 v1.11 (2026-06-20)** 仍说"用平台原生 ASR/转写",也需更新。
**不**在本 ADR 范围,等用户单独决策。

## 后果

### 正面影响
- 代码 + 文档 + 真实架构一致,不再有"代码已自接 Whisper 但文档说飞书第一平台"的矛盾
- 飞书依赖全部 dormant,未来真需要时低成本复用
- MVP 6 步 → 5 步,删一个没必要做的 step

### 负面影响
- ADR-0001 决策 1 的"妙记 300 分钟免费"红利不享受(本来就没享受,因为 loopback 走本地)
- 代码里飞书 adapter 测试用例继续占用 14 个 test slot(可接受)

## 变更历史

- 2026-06-21: 起草 + Accepted(由用户口头确认"我们直接用麦克风,不需要飞书"驱动)