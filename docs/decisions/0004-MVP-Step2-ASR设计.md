# ADR-0004: MVP Step 2 — Whisper + pyannote 链路

- **状态**: Accepted
- **日期**: 2026-06-20
- **作者**: 张胜东 (起草: Hermes)
- **关联**: [ADR-0001 MVP 选型](./0001-MVP-选型.md) · [ADR-0003 Step 1 YAGNI](./0003-MVP-Step1-YAGNI-review.md) · [架构 v1.16 §4.1](../design/总体架构.md)

---

## 背景

VPBuddy MVP 6 步拆分:
1. ✅ Step 1: MeetingState + Storage(16 tests passed)
2. 🎯 **Step 2: Whisper + pyannote 自接音频**(本文档)
3. ⏳ Step 3: 后台交付物生成
4. ⏳ Step 4: 4 窗口 UI
5. ⏳ Step 5: 飞书妙记会后校准
6. ⏳ Step 6: 多平台扩展

**Step 2 的核心问题**:
- 飞书/腾讯/钉钉的"实时字幕"是**客户端 UI**,**没有开放流式 API 给开发者**(2026-06-20 调研确认)
- VPBuddy 必须自己接音频 → Whisper 转写 → pyannote 说话人分离
- VP 设备 loopback(系统声音捕获)→ 16kHz mono PCM → 上传到 GPU 服务器

**Step 2 验收标准**(MVP 可用):
- ✅ 给一段 30-60 秒的中文会议音频(2-3 人)→ 输出 speaker-tagged 转写
- ✅ 准确率:WER < 10%(Whisper large-v3 期望 < 5%)
- ✅ 说话人准确率:DER < 25%(pyannote 3.1 在 2-3 人场景期望 11-19%)
- ✅ 处理速度:RTF < 0.5(RTX 3090 Ti,30s 音频应在 15s 内完成)
- ✅ 与 Step 1 MeetingState 解耦:Step 2 输出独立 JSON,不耦合 state.py

---

## 决策:Step 2 模块边界

| 模块 | 文件 | 行数估算 | 职责 |
|---|---|---|---|
| `TranscriptSegment` | `transcript.py` | ~80 | 转写段数据结构(start/end/text/confidence/speaker) |
| `WhisperProvider` | `whisper_provider.py` | ~150 | faster-whisper large-v3 包装,GPU 推理 |
| `PyannoteDiarizer` | `diarization.py` | ~100 | pyannote 3.1 pipeline 包装,说话人分离 |
| `TranscriptionEngine` | `engine.py` | ~200 | Whisper + pyannote 融合,按时间窗口对齐 |
| `LocalAudioCapture` | `audio_capture.py` | ~150 | Linux PulseAudio loopback(可选,Step 2 后置) |

**输出 schema**(JSON):
```json
{
  "audio_path": "/path/to/audio.wav",
  "language": "zh",
  "duration_sec": 32.5,
  "num_speakers": 3,
  "segments": [
    {
      "segment_id": "SEG-0001",
      "start_sec": 0.5,
      "end_sec": 4.2,
      "speaker_id": "SPEAKER_00",
      "speaker_name": null,
      "text": "今天我们讨论一下 VPBuddy 的 MVP 选型",
      "confidence": 0.92,
      "source": "whisper+pyannote"
    }
  ]
}
```

**与 Step 1 的关系**:
- Step 2 输出**独立 JSON 文件**(不写 MeetingState)
- Step 3 再做"transcript → TrackedItem 抽取"(LLM 任务,Step 2 不做)
- 这就是 v1.13 双轨 ASR 中的**第一方案轨**(Whisper 自接)

---

## 决策:音频采集范围

| 选项 | 范围 | 决定 |
|---|---|---|
| **Linux PulseAudio loopback** | GPU 服务器**不接麦克风**(没插),所以这里先不动 | **Step 2.5** |
| 文件输入(file path) | 给定 wav/mp3,直接处理 | ✅ Step 2 用这个 |
| 实时流(streaming) | 麦克风实时采集 + chunk 推送 | **Step 2.5 / Step 4** |

**理由**:
- Step 2 核心是"Whisper + pyannote 跑通",不需要实时采集
- 文件输入可以**离线测试**(已知输入,可比对输出)
- 实时采集是 VP 端的事(笔记本+PulseAudio loopback 抓系统声音),**不在 GPU 服务器上**
- YAGNI:Step 2 不写 audio_capture.py,Step 2.5 或 Step 4 再写

**已知限制**:
- MVP demo 时 VP 需要自己录一段 30-60s 会议音频 → 上传到 GPU 服务器 → 看结果
- 实时性:**MVP 阶段接受"录完整段后才处理"**(offline batch)

---

## YAGNI 决策

### Y1:不抽象 `BaseASRProvider`(基类)

**选项 A(已选)**:写一个具体的 `WhisperProvider` 类,**不写抽象基类**

**选项 B(未选)**:`BaseASRProvider` + `WhisperProvider(X)` + `XylinkAdapter(X)` + `ZoomAdapter(X)`

**理由**:
- MVP 只有 1 个 ASR 实现(Whisper),基类是 0 行收益
- 架构 v1.13 说"ASR 双轨",但**接口一致性靠"文档约定"即可**,不强类型
- 等真有第 2 个实现时,**duck typing** 比继承更灵活
- YAGNI:不要先建抽象

**对比 ADR-0003 决策 3**:同样不引入 ORM,这里也不引入抽象基类。

### Y2:不写 streaming/chunking 接口

**选项 A(已选)**:`transcribe(audio_path) -> List[Segment]`(整文件同步)

**选项 B(未选)**:`stream_transcribe(audio_chunks) -> AsyncIterator[Segment]`

**理由**:
- 实时性不是 MVP 硬约束(架构 §1.2 "无实时硬约束")
- 整文件同步处理更简单,更好测试
- 后期 Step 2.5 加 streaming:**只需在 `WhisperProvider` 里加 `transcribe_stream()` 方法**,接口不破坏
- YAGNI:不解决"还没出现的问题"

### Y3:不引入 VAD(语音活动检测)独立模块

**选项 A(已选)**:faster-whisper 自带 VAD(filter out silence)

**选项 B(未选)**:独立 VAD 模块(silero-vad / pyannote-vad)

**理由**:
- faster-whisper 的 VAD filter 已经够用(内置 silero VAD)
- 独立 VAD 增加依赖,不增加价值(MVP 阶段)
- 真有问题(短句被切):Step 2.5 再加 silero-vad

### Y4:不用 VAD splitter 二次处理

**选项 A(已选)**:`faster-whisper` 默认输出按 utterance 切分 + VAD filter

**选项 B(未选)**:自己写 sentence boundary detection / split by punctuation

**理由**:
- Whisper 的断句已经够好(WER < 5% 时断句错误率也低)
- 二次处理增加延迟,无明显收益
- YAGNI

### Y5:不写 speaker embedding 缓存

**选项 A(已选)**:每次 diarize 重新跑 pipeline(pyannote 3.1 内置 embedding)

**选项 B(未选)**:存 speaker embedding → 跨会议复用(识别"老张"是谁)

**理由**:
- 跨会议 speaker ID 复用是"产品特性"不是"MVP 必需"
- 飞书妙记会后校准(Step 5)才能拿到真实昵称,pyannote 的 SPEAKER_00 是匿名 ID
- YAGNI

### Y6:不用 ONNX 推理(faster-whisper 默认 CTranslate2)

**选项 A(已选)**:`faster-whisper` + CTranslate2(默认)

**选项 B(未选)**:ONNX runtime / TensorRT

**理由**:
- CTranslate2 在 GPU 上已经很快(RTX 3090 Ti large-v3 实时 30 倍)
- ONNX/TensorRT 是优化选项,**MVP 阶段速度已够**
- YAGNI

### Y7:不写 retry / fallback

**选项 A(已选)**:GPU 服务器 crash → 直接报错,人工重启

**选项 B(未选)**:`tenacity` retry / fallback 到 CPU

**理由**:
- MVP demo 是用户手动触发(offline batch),崩了重启就行
- 自动 retry 是 SRE 关注的事,MVP 阶段不重要
- YAGNI

### Y8:不存 raw waveform(只存 segments)

**选项 A(已选)**:输出 JSON 只有 segments(轻量,~10KB/30s 音频)

**选项 B(未选)**:存 waveform + metadata → 后期重跑

**理由**:
- 30s 音频 wav = ~1MB,存原始数据 = 浪费空间
- 重跑 = 重新读 audio file(原文件留着即可)
- YAGNI

### Y9:不集成 HuggingFace token 管理

**选项 A(已选)**:读环境变量 `HF_TOKEN`,找不到直接报错

**选项 B(未选)**:内置 token 管理(写 keyring / 加密存储)

**理由**:
- pyannote 3.1 需要 HF token(模型 gated)
- MVP 用户 = 1 人(张胜东),把 token 写在 `~/.bashrc` 即可
- YAGNI

### Y10:不抽象 ASR / Diarization 配置文件

**选项 A(已选)**:`WhisperProvider(model_size="large-v3", device="cuda")`,参数直接传

**选项 B(未选)**:`config.yaml` + 加载

**理由**:
- 3 个参数,不值得写 config loader
- YAGNI

---

## 测试策略

### 测试数据

**主测试样本**:`samples/test_zh_3speakers.wav`(30-60s 中文会议片段,2-3 人)

**数据来源**(Step 2.5 完成):
- 用户(ZSD)自己录一段 30s 会议(2 人对话)
- 或下载公开数据集(CV-zh / AISHELL 4 / AliMeeting 选一段)
- 或用 pyannote 自带的 test audio

### 测试用例

| 用例 | 输入 | 期望 | 测试类型 |
|---|---|---|---|
| `test_transcript_segment_serialize` | 1 个 segment | JSON 序列化/反序列化 OK | 单元 |
| `test_whisper_load` | (none) | 模型加载成功,GPU 推理 | 集成 |
| `test_whisper_transcribe_zh` | `test_zh_3speakers.wav` | segments 数 ≥ 3,每段有 text | 集成 |
| `test_whisper_confidence_in_range` | 真实输出 | confidence ∈ [0, 1] | 单元 |
| `test_pyannote_load` | HF_TOKEN | pipeline 加载成功 | 集成 |
| `test_pyannote_diarize` | `test_zh_3speakers.wav` | speaker 数 ≥ 2 | 集成 |
| `test_engine_merge` | 同上 | segments 都带 speaker_id | 集成 |
| `test_engine_speaker_assignment` | 同上 | speaker_id 与 pyannote 一致 | 集成 |
| `test_end_to_end_pipeline` | `test_zh_3speakers.wav` | 完整 JSON 输出,所有断言通过 | E2E |
| `test_rtf_under_threshold` | 30s 音频 | RTF < 0.5(RTX 3090 Ti) | 性能 |

**目标**:**9-10 个测试全过**,耗时 < 60s(模型加载 30s + 转写 5s + diarization 20s)

### 跳过的测试(Step 2.5+)

- 实时流(streaming chunk)
- 错误处理(GPU OOM / HF token 过期 / 网络断)
- 多语言混合
- 长音频(> 5min)的 chunked 处理
- VAD 二次校准

---

## 验收指标(Step 2)

| 指标 | 目标 | 测量方法 | 状态 |
|---|---|---|---|
| 测试通过率 | 100% | pytest | ⏳ |
| 测试运行时间 | < 60s | pytest | ⏳ |
| 中文 WER | < 10%(预期 < 5%) | sample + ground truth | ⏳ |
| 说话人 DER | < 25%(预期 11-19%) | pyannote 自带 eval | ⏳ |
| 实时因子 RTF | < 0.5(RTX 3090 Ti) | nvidia-smi + timing | ⏳ |
| 代码行数(纯实现) | < 700 | wc -l | ⏳ |
| 外部依赖 | + faster-whisper + pyannote-audio + soundfile | pip list | ⏳ |

---

## 代码结构

```
src/vpbuddy/
├── __init__.py
├── state.py                # Step 1
├── storage.py              # Step 1
├── transcript.py           # Step 2: TranscriptSegment / DiarizedSegment / TranscriptResult
├── whisper_provider.py     # Step 2: FasterWhisperProvider
├── diarization.py          # Step 2: PyannoteDiarizer
└── engine.py               # Step 2: TranscriptionEngine (merge)

src/tests/
├── test_state.py           # Step 1: 16 tests
├── test_transcript.py      # Step 2: 单元测试 (1-2 tests)
├── test_whisper.py         # Step 2: 集成测试 (2-3 tests)
├── test_diarization.py     # Step 2: 集成测试 (2 tests)
└── test_engine.py          # Step 2: 集成 + E2E (3-4 tests)

samples/
└── test_zh_3speakers.wav   # 30-60s 测试音频(Step 2.5 添加)
```

**总计**: ~600 行实现 + ~300 行测试 = ~900 行

**对比"标准 ASR 服务"**:
- 完整 ASR 服务(WhisperX / Insanely-fast-whisper):3000-5000 行
- 我们省了 ~70% 代码,YAGNI 收益

---

## 已知限制

| 限制 | 影响 | 何时修 |
|---|---|---|
| 无实时流 | 录音结束才开始处理 | Step 2.5/4 加 streaming |
| 无 VAD 二次校准 | 短句/呼吸声可能被切 | 真有问题时 |
| 无 HF token 管理 | token 写在 env,需手动 rotate | 多用户时 |
| 无 retry/fallback | crash 需手动重启 | 上生产时 |
| 无 speaker embedding 缓存 | 每次重新 diarize | 跨会议复用 ID 时 |
| Linux PulseAudio loopback 未实现 | 实时采集暂无 | Step 2.5(在 VP 笔记本上) |
| 飞书/腾讯原生 ASR 未对接 | 第二轨 Step 5 再做 | Step 5 |
| 小鱼易连 API 未对接 | 第三轨需企业合同 | 不做(已知付费墙) |

每条都有**明确的修复触发条件**,不是 YAGNI 偷懒。

---

## 任务清单(今晚)

- [ ] 后台安装 torch + faster-whisper + pyannote(已启动)
- [ ] 准备测试音频样本(30-60s,中文 2-3 人)
- [ ] 写 `transcript.py` (dataclasses)
- [ ] 写 `whisper_provider.py` (faster-whisper 包装)
- [ ] 写 `diarization.py` (pyannote 包装)
- [ ] 写 `engine.py` (融合)
- [ ] 写 4 个 test 文件,9-10 个测试
- [ ] pytest 验证
- [ ] git commit + push
- [ ] 跟张胜东同步验收

---

## 参考

- 架构 v1.16 §4.1: `../design/总体架构.md`
- ADR-0001 MVP 选型: `./0001-MVP-选型.md`
- ADR-0003 Step 1 YAGNI: `./0003-MVP-Step1-YAGNI-review.md`
- ASR 调研 v2: `../research/asr-speaker-diarization-survey.md`
- pyannote 3.1: https://huggingface.co/pyannote/speaker-diarization-3.1
- faster-whisper large-v3: https://huggingface.co/Systran/faster-whisper-large-v3

---

## 变更历史

- 2026-06-20: 起草,10 个 YAGNI 决策 + 模块边界 + 测试策略 + 验收指标
- 2026-06-21: 实施 + 踩坑记录(GPU 服务器 + pyannote 兼容性 + pydantic bug + HuggingFace 镜像)
- 2026-06-21 晚: **重大突破 — ModelScope 镜像替代 HF_TOKEN**,所有 pyannote 测试无需 token 全过

---

## 🔥 关键发现:ModelScope 镜像 + 本地 .bin(2026-06-21 晚)

**问题**:pyannote/speaker-diarization-3.1 是 HF **gated** 模型,需要登录 huggingface.co 同意用户协议 + 拿到 HF_TOKEN。**国内打不开 huggingface.co**,整个流程走不通。

**解决**:用 ModelScope(阿里达摩院,国内 CDN)镜像下载 + 本地 .bin 加载:

```bash
# 一次性下载(用 modelscope SDK,完全国内网络)
pip install modelscope
mkdir -p /tmp/pyannote_models
modelscope download --model pyannote/speaker-diarization-3.1 --local_dir /tmp/pyannote_models/speaker-diarization-3.1
modelscope download --model pyannote/segmentation-3.0 --local_dir /tmp/pyannote_models/segmentation-3.0
modelscope download --model pyannote/wespeaker-voxceleb-resnet34-LM --local_dir /tmp/pyannote_models/wespeaker-voxceleb-resnet34-LM

# 之后用 PyannoteDiarizer(自动从本地加载,无需 HF_TOKEN)
export PYANNOTE_LOCAL_DIR=/tmp/pyannote_models
python -c "from vpbuddy.diarization import PyannoteDiarizer; print(PyannoteDiarizer().get_speaker_turns('audio.wav'))"
```

**技术细节**(踩坑后总结):
| 坑 | 原因 | 解法 |
|---|---|---|
| `Pipeline.from_pretrained("pyannote/...")` 报 403 gated | HF API 鉴权 | 不走 from_pretrained,手动 `OmegaConf` + `instantiate` |
| `hf_hub_download` 报 `use_auth_token` unknown kwarg | pyannote 旧 API 用了 deprecated 参数 | monkey-patch: `use_auth_token` → `token` |
| `Pipeline.instantiate(params)` 报 "only sub-pipeline params" | 顶层 params 里有 `clustering` + `segmentation` 子 dict | 嵌套调用,只传子 dict |
| `clustering="AgglomerativeClustering"` 字符串 | 实际是 `Clustering` 枚举查表 | 直接传字符串 "AgglomerativeClustering" |
| 周华健 44.1kHz/2ch/MP3 报 tensor size mismatch | pyannote 内部 batch 重采样出问题 | 先用 `torchaudio` 转 16kHz mono PCM |

**`PyannoteDiarizer` 改造**(src/vpbuddy/diarization.py):
- 删除 `hf_token` 参数(改用 `local_models_dir` 或 `$PYANNOTE_LOCAL_DIR`)
- 启动时自动 `ensure_pyannote_models()` 调 ModelScope 补齐缺失模型
- 手动 `OmegaConf.create(config.yaml)` + `hydra.utils.instantiate()` 实例化
- `pipeline.instantiate({"clustering": {...}, "segmentation": {...}})` 设置默认参数
- 内部 `pipeline.to("cuda")`

**`TranscriptionEngine.default()` 同步改造**(src/vpbuddy/engine.py):
- `hf_token` 参数改 `pyannote_local_dir`(`hf_token` 留作兼容旧 API 的 deprecation alias)
- `RUN_GPU_INTEGRATION=1` 环境变量启用完整 e2e 测试(平时默认 skip,避免本机无 GPU 时报大量 skip)

**测试结果**(2026-06-21 22:xx,GPU 服务器完整套件):
```
============================ 38 passed in 45.58s ============================
tests/test_state.py        16 PASSED  (Step 1)
tests/test_transcript.py    9 PASSED  (Step 2 单元)
tests/test_whisper.py       5 PASSED  (Step 2 GPU 集成)
tests/test_diarization.py   3 PASSED  (Step 2 GPU 集成,无 HF_TOKEN!)
tests/test_engine.py        5 PASSED  (Step 2 端到端,自动检测 2 speakers)
```

**端到端 demo**(周华健《明天我要嫁给你了》8MB → 16kHz mono):
- 转写:38 segments, "明天我要嫁给你了" 准确识别
- 说话人:40 turns, 主 SPEAKER_00(周华健)+ 副 SPEAKER_01(疑似和声)
- 端到端:34 segs,2 speakers detected,RTF=0.026(38x 实时)

---

## 实施踩坑记录(2026-06-21)

### 1. pydantic v2 `model_dump_json` 不支持 `ensure_ascii` 参数

**症状**(GPU 服务器 pytest):
```
TypeError: BaseModel.model_dump_json() got an unexpected keyword argument 'ensure_ascii'
```

**根因**:`MeetingStorage.save()` 第 35 行 `state.model_dump_json(indent=2, ensure_ascii=False)` —— `ensure_ascii` 是 `json.dumps` 的参数,**不是** pydantic v2 `model_dump_json` 的。

**修复**:
```python
import json
path.write_text(
    json.dumps(
        json.loads(state.model_dump_json(indent=2)),  # pydantic → Python dict
        ensure_ascii=False,  # 保留中文可读
        indent=2,
    ),
    encoding="utf-8",
)
```

**教训**:`model_dump_json` 一次性出 JSON 字符串,改用 `json.dumps(json.loads(...))` 包装层控制 ASCII 行为。

---

### 2. pyannote 3.1 与 numpy 2.x 不兼容

**症状**:
```
AttributeError: module 'numpy' has no attribute 'NaN'
```

**根因**:pyannote.audio 3.1 内部用了 `np.NaN`(numpy 1.x 写法)。numpy 2.0+ 已删除该属性。

**解决路径**(踩坑过程):
| 尝试 | 结果 |
|---|---|
| pyannote.audio 3.1 + numpy 1.26 | scipy 1.13 要求 numpy≥1.23 实际跑 OK,但与 pyannote 3.1 内部期望冲突 |
| pyannote.audio 4.0 + numpy 2.x | 要重装 torch 2.12(超大,网络耗时长) |
| **pyannote.audio 3.3.2 + numpy 2.x** | **✅ 3.x 最后版,兼容 numpy 2.x,无重装 torch** |

**最终决策**:用 `pyannote.audio==3.3.2`,配 numpy≥2.0 + torch 2.5+。不升级到 4.0(避免 6GB torch 重新下载)。

---

### 3. 后台 `pip install` 进程被 SSH 关闭杀掉

**症状**:SSH 连接关闭后,nohup 起的 pip 进程随之死亡。

**根因**:SSH 终止时,内核会发 SIGHUP 给所有子进程,虽然 nohup 忽略了 SIGHUP,但 SSH 客户端关闭 TCP 连接后,`setsid` 未生效的进程会被 init 回收。

**解决**:
```bash
# ❌ 不行(SSH 关就死)
nohup pip install ... &

# ✅ 真正后台(hermes terminal background 模式,独立 session)
pip install ... > /tmp/log 2>&1 & disown
# 或
setsid python -c "..." > /tmp/log 2>&1 < /dev/null &
```

**教训**:
- 长任务用 `hermes terminal background=true` 模式(独立 session,SSH 关闭不影响)
- SSH 内手起后台:**`setsid` + `disown` + 重定向 stdin** 三件套缺一不可

---

### 4. HuggingFace 镜像源(国内网络)

**症状**:`huggingface.co` TCP SYN-SENT 状态卡住,模型下不动。

**网络情报**(用户 2026-06-21 反馈):
- 192.168.10.5(本机,江苏联通):CDN 阻 + 封 Telegram/Google 整段 TCP
- 192.168.10.63(GPU 服务器,局域网):**huggingface.co 直连也被 SYN 卡住**

**解决**:设 `HF_ENDPOINT=https://hf-mirror.com` 走 hf-mirror 镜像(28MB/s,3GB 模型 90s 下完)。

**配置**:`~/.pip/pip.conf`:
```ini
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
timeout = 300
```

**Torch wheel 单独**(清华/阿里都不镜像):
```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

---

### 5. test_engine.py module 级 skip 误伤单元测试

**症状**:没设 `HF_TOKEN` 时,整个 test_engine.py 文件被 skip,2 个 unit test 也不跑。

**根因**:`if not os.environ.get("HF_TOKEN"): pytest.skip(..., allow_module_level=True)` —— module 级 skip。

**修复**:改成 `pytest.mark.skipif` 装饰器,**只 skip 集成测试**:
```python
requires_token = pytest.mark.skipif(
    not os.environ.get("HF_TOKEN"),
    reason="需要 HF_TOKEN",
)

@requires_token
def test_engine_end_to_end_single_speaker(audio_path): ...
@requires_token
def test_engine_end_to_end_auto_detect(audio_path): ...
@requires_token
def test_engine_serialize_to_json(audio_path, tmp_path): ...

# 无装饰器的 unit test 不受影响
def test_engine_assign_speaker_with_empty_turns(): ...
def test_engine_assign_speaker_picks_nearest_midpoint(): ...
```

**教训**:skip 粒度要细,只 skip 真正依赖外部资源的部分,纯逻辑单元测试永远能跑。

---

### 6. NFS git 写入失败(老问题)

**症状**:`git add` 时 `error: unable to write file .git/objects/...`。

**解决**(成熟方案):
1. `/tmp`(ext4)克隆:`git clone --no-local /mnt/nfs_fn/.../vpbuddy /tmp/vpbuddy_work`
2. 在 `/tmp` 改文件 + commit
3. push 到 GitHub(origin)
4. NFS 工作树只放源码(只读性质)

**远程冲突处理**:`git fetch && git rebase origin/main && git push`。rebase 冲突用 `git checkout --theirs/--ours` + `GIT_EDITOR=true git rebase --continue`。

---

## 测试结果(2026-06-21)

### 本机(192.168.10.5,无 GPU)
```
tests/test_state.py        16 passed
tests/test_transcript.py    9 passed
============================ 25 passed, 7 skipped in 0.39s =============================
```

### GPU 服务器(192.168.10.63, RTX 3090 Ti)
```
tests/test_state.py        16 passed (含 pydantic ensure_ascii 修复)
tests/test_transcript.py    9 passed
tests/test_diarization.py   3 skipped (无 HF_TOKEN)
tests/test_engine.py        2 unit passed + 3 integration skipped
tests/test_whisper.py       5 待跑(模型下载中)
============================ 27 passed, 6 skipped =============================
```

### 待补
- [ ] HF_TOKEN 获取后跑 `test_diarization.py` + `test_engine.py` 集成测试
- [ ] `test_whisper.py` 5 个 GPU 集成测试(模型下载完成后)
- [ ] 真实多说话人样本(2-3 人会议,30-60s)
- [ ] Step 2 端到端 demo + RTF 实测