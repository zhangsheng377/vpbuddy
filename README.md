# VPBuddy

> **人机协同会议操作系统级 AI 助手** —— 面向软件开发公司 VP / 售前负责人 / 项目负责人。

> **运行在 Hermes Agent 之上** (2026-06-21 ADR-0009 钉死): VPBuddy = 装在 `~/.hermes/skills/vpbuddy/` 的 skill 集合,**不**独立运行。**部署 = `pip install hermes-agent` + `pip install vpbuddy` + `hermes skills install vpbuddy`**。详见 [ADR-0009](docs/decisions/0009-部署架构-Hermes-runtime.md) + [Hermes 官方文档](https://hermes-agent.nousresearch.com/docs)。

## 启动方式(2026-06-21 修正)

| 命令 | 谁用 | 何时用 |
|---|---|---|
| **`vpbuddy ui`** | VP / 会议参与者 | **开会时的主入口** — 浏览器打开 :8765 |
| **`vpbuddy controller`** | 后台进程 | 7×24 跑,每 30s 轮询生成 6 文档 |
| `vpbuddy transcribe` | 脚本 | 单次音频转写 |
| `vpbuddy setup-gpu` | 部署时 | 装 GPU 模型(本地一次性) |
| `hermes` (TUI) | 开发/调试 | 跑 prompt/查 session,**不是** VPBuddy UI |

**VPBuddy 用户永远从 `vpbuddy ui` 入口进**;`hermes` TUI 是 Hermes 自己的 dev tool,不混用。

VPBuddy 不是传统意义上的 AI 助手,而是运行在会议中的协同系统:

- 人类负责决策与主导
- VPBuddy 负责理解、结构化、生成与演化

VPBuddy 直接接 VP 桌面客户端的麦克风/系统音频(ADR-0004 自接音频流 + Whisper + pyannote),在会议中实时完成:

- 会议理解与结构化建模
- 需求分析与追问
- 解释材料生成
- Sub-agent 并行推理 (Hermes `delegate_task` 5 Agent 真并行)
- 交互 Demo 与交付物生成
- 企业 / 个人 / 行业知识库调用 (Hermes memory 持久化,跨会议连续)
- 软件交付资产实时生成

核心定义:

> **会议 = 人机协同过程**  
> **VPBuddy = 会议中的 AI 协同执行系统**
> **VPBuddy = Hermes Agent 之上的人机协同会议应用层**

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
│   ├── design/                              ← 系统设计文档(总体架构/UI 设计/数据流)
│   │   ├── 总体架构.md                      ← 架构 v1.16(2026-06-20)
│   │   └── README.md
│   ├── research/                            ← 调研资料 / 行业参考
│   │   └── asr-speaker-diarization-survey.md  ← ASR 选型调研 v2
│   │   └── README.md
│   └── decisions/                           ← 架构决策记录 ADR(MADR 模板)
│       ├── 0001-MVP-选型.md
│       ├── 0002-UI-vs-架构冲突-review.md
│       ├── 0003-MVP-Step1-YAGNI-review.md
│       ├── 0004-MVP-Step2-ASR设计.md
│       ├── 0005-ModelScope-替代HF_TOKEN.md
│       └── README.md
├── ui-mockups/                              ← UI 原型截图 + 交互式 HTML
│   ├── README.md                            ← 截图索引
│   ├── v1/, v2/                             ← 设计版本
│   └── UI*.png
├── samples/                                 ← 测试样本音频
│   ├── test_zh_sample.wav                   ← 周华健《明天我要嫁给你了》8MB(44.1kHz stereo)
│   ├── test_zh_16k_mono.wav                 ← 16kHz mono PCM(pyannote 友好)
│   └── README.md
└── src/                                     ← Python 实现
    ├── vpbuddy/                             ← VPBuddy 包
    │   ├── __init__.py
    │   ├── state.py                         ← Step 1: MeetingState
    │   ├── storage.py                       ← Step 1: MeetingStorage
    │   ├── transcript.py                    ← Step 2: TranscriptSegment/DiarizedSegment/Result
    │   ├── whisper_provider.py              ← Step 2: faster-whisper 包装
    │   ├── diarization.py                   ← Step 2: pyannote 包装(ModelScope 镜像)
    │   ├── engine.py                        ← Step 2: 融合(时间窗口对齐)
    │   └── README.md
    └── tests/                               ← 38 个测试
        ├── test_state.py                    ← Step 1(16 tests)
        ├── test_transcript.py               ← Step 2 单元(9 tests)
        ├── test_whisper.py                  ← Step 2 GPU ASR(5 tests)
        ├── test_diarization.py              ← Step 2 GPU 说话人(3 tests)
        └── test_engine.py                   ← Step 2 端到端(5 tests)
```

## 当前状态

| 模块 | 状态 | 进度 |
| --- | --- | --- |
| 产品说明书 | ✅ v1.12(2026-06-21) | 持续迭代 |
| UI 原型 v2 | ✅ Linear Dark HTML(2026-06-20) | Step 4 集成待做 |
| 总体架构 | ✅ v1.16(2026-06-20) | Step 3 启动后更新 |
| ASR 调研 | ✅ v2(2026-06-20) | 5 决策锁定 |
| ADR | ✅ 0001-0005(2026-06-21) | 持续追加 |
| **代码实现** | ✅ **Step 1+2 已完成(2026-06-21)** | Step 3-6 待启动 |

## 🚀 快速开始(5 分钟跑通)

### 0. 准备环境

```bash
# Python 3.11+(Step 2 GPU 推荐 3.12 + CUDA 12.x)
python3 --version

# 有 GPU 最好(Step 2 跑 GPU 推理);没 GPU 也能跑(CPU 慢 10x)
nvidia-smi  # 可选
```

### 1. 装依赖

```bash
# 国内用户(推荐) — 阿里源
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip config set global.trusted-host mirrors.aliyun.com
pip config set global.timeout 300

# 装 VPBuddy 依赖
pip install -r src/requirements.txt

# 装 ASR 引擎(GPU 推理)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121  # CUDA 12.1
# 或 CPU only:
# pip install torch torchaudio

pip install faster-whisper pyannote.audio modelscope
```

### 2. 装模型(国内无 HF 账号,ModelScope 镜像)

```bash
mkdir -p /tmp/pyannote_models

# pyannote 说话人分离(42MB,30 秒)
modelscope download --model pyannote/speaker-diarization-3.1 --local_dir /tmp/pyannote_models/speaker-diarization-3.1
modelscope download --model pyannote/segmentation-3.0 --local_dir /tmp/pyannote_models/segmentation-3.0
modelscope download --model pyannote/wespeaker-voxceleb-resnet34-LM --local_dir /tmp/pyannote_models/wespeaker-voxceleb-resnet34-LM

# faster-whisper(2.9GB,3 分钟,自动从 HF mirror 下载)
export HF_ENDPOINT=https://hf-mirror.com  # 国内镜像
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cuda', compute_type='float16')"  # 触发下载
```

### 3. 跑测试

```bash
# CPU 测试(本机,无 GPU)
PYTHONPATH=src python3 -m pytest src/tests/test_state.py src/tests/test_transcript.py -v
# 期望:25 passed

# 完整测试(GPU 服务器,需 RUN_GPU_INTEGRATION=1)
export PYANNOTE_LOCAL_DIR=/tmp/pyannote_models
export RUN_GPU_INTEGRATION=1
PYTHONPATH=src python3 -m pytest src/tests/ -v
# 期望:38 passed
```

### 4. 跑 Demo(周华健《明天我要嫁给你了》)

```bash
# 准备 16kHz mono 音频
python -c "
import torchaudio
wf, sr = torchaudio.load('samples/test_zh_sample.wav')
if wf.shape[0] > 1:
    wf = wf.mean(dim=0, keepdim=True)
wf = torchaudio.functional.resample(wf, sr, 16000)
torchaudio.save('samples/test_zh_16k_mono.wav', wf, 16000, encoding='PCM_S', bits_per_sample=16)
print('Done')
"

# 端到端 demo
export PYANNOTE_LOCAL_DIR=/tmp/pyannote_models
export HF_ENDPOINT=https://hf-mirror.com
PYTHONPATH=src python3 -c "
from vpbuddy.engine import TranscriptionEngine
eng = TranscriptionEngine.default(model_size='large-v3', device='cuda', compute_type='float16')
result = eng.process('samples/test_zh_16k_mono.wav', language='zh')
print(f'\\n=== {len(result.segments)} segments, {result.num_speakers} speakers, {result.duration_sec:.1f}s ===')
for seg in result.segments[:10]:
    print(f'  [{seg.speaker_id}] {seg.start_sec:6.1f}s {seg.text[:50]}')
"
```

输出示例(2026-06-21 实战):
```
=== 34 segments, 2 speakers, 190.8s ===
  [SPEAKER_00] 0.0s 1 2 1 2
  [SPEAKER_00] 2.9s 三分钟滴答滴答在心中
  [SPEAKER_00] 22.1s 我的眼光闪烁闪烁好空洞
  ...
  [SPEAKER_00] 46.0s 明天我要嫁给你了
  ...
```

## 📋 文档导航

| 我想... | 看什么 |
| --- | --- |
| 了解 VPBuddy 是什么 | [产品说明书](docs/product-spec/VPBuddy_产品说明书.md) |
| 看系统架构 | [总体架构 v1.16](docs/design/总体架构.md) |
| 看 MVP 6 步路线 | [ADR-0001](docs/decisions/0001-MVP-选型.md) |
| 了解 Step 2 ASR 选型 | [ADR-0004](docs/decisions/0004-MVP-Step2-ASR设计.md) + [ASR 调研 v2](docs/research/asr-speaker-diarization-survey.md) |
| 了解国内无 HF 账号方案 | [ADR-0005](docs/decisions/0005-ModelScope-替代HF_TOKEN.md) |
| 看 Step 1+2 代码 | [src/README.md](src/README.md) |
| 看 UI 原型 | [ui-mockups/](ui-mockups/) |
| 跑测试 | [src/README.md §跑测试](src/README.md#跑测试) |

## 写作约定(规划)

- **产品说明书**: 变更先改 `.docx`,再同步 `.md`(以 `.docx` 为准)
- **设计文档**: 放在 `docs/design/`,每篇独立 `.md`,顶部有"状态/作者/更新日期"元信息
- **决策记录**: 放在 `docs/decisions/`,文件名 `NNNN-标题.md`(参考 MADR 模板)
- **UI 截图**: 文件名保持 `UI<编号>-<页面名>.png` 格式,新增时在 `ui-mockups/README.md` 索引
- **代码**: PEP 8,中文 docstring 允许,模块顶部必写"设计原则(ADR-XXXX)"

## 相关链接

- **GitHub**: <https://github.com/zhangsheng377/vpbuddy>
- **产品说明书**: [docs/product-spec/VPBuddy_产品说明书.md](docs/product-spec/VPBuddy_产品说明书.md)
- **UI 原型**: [ui-mockups/](ui-mockups/)
- **Hermes Agent**: <https://hermes-agent.nousresearch.com/docs>

## 维护者

张胜东(@zhangsheng377)
