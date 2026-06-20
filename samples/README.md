# 测试样本音频

## Step 2 需要的样本

### 必备:`test_zh_sample.wav`

**用途**:MVP 测试,验证 Whisper + pyannote 端到端 pipeline

**要求**:
- 中文语音
- 16kHz / 16bit / mono PCM(Whisper 自动重采样,但 16k 最优)
- 30-60 秒
- 2-3 人会议风格(对话、问答)

**推荐来源**:
1. **公开数据集**(Step 2.5 下载):
   - AISHELL-4(中文会议,4 人):http://www.aishelltech.com/aishell_4
   - AliMeeting(中文会议,3-5 人):https://github.com/yufan-aslp/AliMeeting
   - MagicData(中文对话):https://www.magicdatatech.cn/kehu/index

2. **自己录**:VP 用手机或电脑录 1 分钟 2 人对话(最实用)

3. **临时占位**:任意中文歌曲 mp3,设置 `num_speakers=1`,验证 pipeline 机制

### 文件命名规范

```
samples/
├── test_zh_sample.wav          # 主测试样本(30-60s 中文会议)
├── test_en_sample.wav          # 英文样本(可选,多语言测试)
└── README.md                   # 本文件
```

### 占位文件(Step 2 MVP)

**当前状态**:暂无真实样本,**集成测试会被 pytest skip**(test_whisper.py / test_diarization.py)

**临时方案**:
- 测试自动 fallback 到 `/home/zsd/` 或 `/mnt/nfs_fn/zsd_server/` 下任何 wav/mp3(>100KB, <30MB)
- 如果你系统里有现成音频文件,测试会用它

### 后期(Step 2.5+)

录真实会议样本后:
- 把文件放进 `samples/` 目录
- 重命名为 `test_zh_sample.wav`
- 跑 `pytest tests/test_engine.py -v` 验收
