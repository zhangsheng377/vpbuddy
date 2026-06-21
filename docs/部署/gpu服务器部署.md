# GPU 服务器部署指南

> **v1.14 重大修订 (2026-06-21 ADR-0009)**: VPBuddy 不再是独立 Python 包 — 部署目标服务器 = **装 Hermes Agent + 装 vpbuddy skill**。5 分钟起。详见 [ADR-0009](../decisions/0009-部署架构-Hermes-runtime.md)。

> **说明**: 本文档说清"在新 GPU 服务器上从零部署,5 分钟跑通端到端"。Hermes 装好后,VPBuddy 自然可用;GPU 模型可选(只用云 LLM 不需要 GPU)。

---

## 0. 前置要求(2026-06-21 ADR-0009 落地)

| 项 | 要求 | 备注 |
|---|---|---|
| **Hermes Agent** | `pip install hermes-agent>=0.16.0,<1.0` | **必须** — VPBuddy = Hermes skill,无 Hermes 不可用 |
| **LLM API key** | 至少 1 个 (MiniMax / OpenRouter / OpenAI) | 配到 `~/.hermes/.env`,VPBuddy 通过 Hermes 间接调用 |
| Python | 3.11+ | Hermes 装时自动选 |
| GPU (可选) | NVIDIA ≥ 8GB VRAM | **仅当**用本地 Whisper/pyannote 时需要;云 LLM 不需要 |
| CUDA (可选) | ≥ 12.1 | 同上 |
| 内存 | ≥ 16GB | Hermes 启动 ~500MB + 加载本地模型 ~6GB |
| 磁盘 | ≥ 10GB | 5GB Hermes + 3GB 模型 + 缓存 |
| 系统 | Linux (Ubuntu/CentOS/Arch) / macOS | macOS 不支持 CUDA(无本地模型) |
| 网络 | 出海 OR 国内镜像 | LLM API + 模型下载 |

**关键不变量** (ADR-0009 §0.3):
- VPBuddy **不**直接调 LLM API → 必须经 Hermes
- VPBuddy **不**自己管 session → 必须用 Hermes session
- VPBuddy **不**自己实现 5 Agent 并行 → 必须用 Hermes `delegate_task`

---

## 1. 一键部署(5 分钟)

```bash
# 1. 装 Hermes(目标服务器一次)
pip install hermes-agent
hermes setup  # 交互式配 LLM API key + 选 provider

# 2. 装 VPBuddy skill
pip install vpbuddy
hermes skills install vpbuddy

# 3. (可选) 装 GPU 模型 — 仅当用本地 Whisper/pyannote 时
vpbuddy setup-gpu

# 4. 启动 VPBuddy UI (用户实际用的入口)
vpbuddy ui  # 起 http server 在 :8765
# 浏览器打开 http://localhost:8765
```

预计耗时: **5-10 分钟**(主要在 pip 装包 + 配 LLM key;GPU 模型下载另算)。

### 多种启动方式(2026-06-21 ADR-0009 修正 — Hermes 是 runtime 不是 UI)

**VPBuddy = Python 软件包**,**Hermes = LLM runtime**。两者关系:

| 入口 | 命令 | 谁用 | 何时用 |
|---|---|---|---|
| **VPBuddy UI** (默认) | `vpbuddy ui` | VP / 会议参与者 | 开会时投屏,主界面 |
| **VPBuddy controller** | `vpbuddy controller` | 后台 7×24 跑 | 后台轮询,生成 6 文档 |
| **Hermes TUI** (开发) | `hermes` | 开发/调试 | 跑 prompt/单次对话/查 session |
| **Hermes CLI** (脚本) | `hermes chat "..."` | 脚本/自动化 | 一次性问 LLM |
| **Hermes gateway** (后台) | `hermes gateway` | cron/Feishu/微信 daemon | 7×24 接收消息 |

**不要在生产里直接用 `hermes` TUI 当 VPBuddy 用户界面** — TUI 是 Hermes 自己的 dev tool,VPBuddy 用户的 UI 永远是 `vpbuddy ui`(浏览器 :8765)。

---

## 2. 旧版部署(已知过时,仅作历史 — 2026-06-21 之前的方式)

> **⚠️ 弃用警告**: 以下是 2026-06-21 ADR-0009 之前的部署方式 — 不用 `pip install hermes-agent`,只用 `git clone` + `pip install -r requirements.txt`。**仍可工作但不推荐**,因为无法使用 VPBuddy 全部能力(尤其 `delegate_task` 5 Agent 并行)。

```bash
# 弃用:手动装
git clone https://github.com/BZ-coding/financial-data-service.git vpbuddy  # 或你的 fork
cd vpbuddy
bash scripts/setup_gpu.sh  # 仅装 GPU 模型,不装 Hermes
PYTHONPATH=src python -c "from vpbuddy.state import MeetingState, Platform; ..."
```

**为什么不推荐**:
- 无法使用 5 Agent 真并行(`controller.py` 手编循环)
- 没有 Hermes session 历史
- 没有 cron 7×24 任务
- 没有 skill 自动生成/复用
- 没有跨会议连续(VP 第二天开会记不得昨天)

---

## 3. 部署到不同服务器

| 场景 | 步骤 |
|---|---|
| **用户私有服务器** | 5 分钟部署 (§1) |
| **客户机房/隔离网络** | 5 分钟部署 (§1) + LLM API 走内网代理或本地 LLM |
| **云 (AWS / 阿里云 / 腾讯云)** | 起 ECS/BCC → 5 分钟部署 (§1) |
| **macOS 本机(开发)** | 同上;GPU 模型跳过(用 `device=cpu` 跑 Whisper small) |

---

## 4. 验证部署

```bash
# 1. Hermes 是否装好
hermes --version
# 期望: 0.16.x 或更新

# 2. VPBuddy skill 是否注册
hermes skills list | grep vpbuddy
# 期望: vpbuddy (✓ enabled)

# 3. LLM API 是否通
hermes chat "你好,你是 Hermes 的哪个版本?"
# 期望: 正常返回

# 4. VPBuddy 是否可触发
hermes chat "用 vpbuddy 开一个会议 demo"
# 期望: 触发 vpbuddy skill,启动会议流程

# 5. (可选) GPU 模型是否就绪
python -c "from vpbuddy.whisper_provider import WhisperProvider; w = WhisperProvider(); print('OK')"
# 期望: 加载模型成功,无 CUDA error
```

---

## 5. 切换模型 / 添加模型(可选)

所有模型清单在 `scripts/download_gpu_models.py` 的 `MODELSPECS` 列表里。

要换模型:改 `MODELSPECS` 然后重跑 `python scripts/download_gpu_models.py`。

要换 LLM:改 `~/.hermes/config.yaml` 的 `model.default` + 配对应 API key。

---

## 6. 常见问题

| Q | A |
|---|---|
| 装 VPBuddy 必须装 Hermes 吗? | **是**(ADR-0009 决策),不可绕过 |
| 不想用 delegate_task,能用旧 controller.py 吗? | 能跑,但不推荐(丢失 5 Agent 并行 + 跨 session 能力) |
| Hermes 升级会破坏 VPBuddy 吗? | 风险存在,需看 [Hermes changelog](https://hermes-agent.nousresearch.com/docs);pyproject 锁 `hermes-agent<1.0` 防大版本变更 |
| 能否跳过 GPU,用云 ASR(阿里云/腾讯云)? | 能,VPBuddy 抽象出 ASR provider interface,后续可接云 ASR(见 ADR-0004 替代方案) |
| 跨会议连续怎么工作的? | 同 `session_id` 跨多次对话,Hermes 原生支持 |
| Mac 本机能跑吗? | 能(无 GPU 用 cpu Whisper small),但开发用,生产建议 Linux |

---

## 7. 关联文档

- [ADR-0009 部署架构 = Hermes runtime](../decisions/0009-部署架构-Hermes-runtime.md)
- [ADR-0001 §6 决策 1 LLM 框架 = Hermes](../decisions/0001-MVP-选型.md)
- [ADR-0004 Step 2 ASR 设计](../decisions/0004-MVP-Step2-ASR设计.md)
- [总体架构 v1.18 §0 运行时基础](../design/总体架构.md)
- [踩坑记录 §18 Hermes 部署差异](./踩坑记录.md)
- [Hermes 官方文档](https://hermes-agent.nousresearch.com/docs)
