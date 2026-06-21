# PHASE2_AUDIO_REAL 需求清单

最后更新: 2026-06-21T05:28:13.259331+00:00
session_id: meeting:PHASE2_AUDIO_REAL:req

## 累积摘要
- 平台: feishu
- 状态: 首次创建

## 目标 (1 条)
### GOAL-18CA7F VPBuddy GPU pipeline 端到端测试
- **优先级**: high
- **状态**: pending
- **来源**: 张胜东 / 2026-06-21
- **描述**: SenseVoice ASR + campplus 说话人 + state 累积 + 子 session 6 docs

## 需求 (2 条)
### REQ-D25868 歌曲《明天我要嫁给你》副歌内容识别
- **优先级**: low
- **状态**: pending
- **来源**: ASR 转写 / 2026-06-21
- **原话**: 副歌反复强调"明天我要嫁给你了"
- **澄清**: 表达对婚姻的期待与紧张 (注:此条源于测试音频为歌曲,非真实需求)

### REQ-8B57B4 歌曲结构识别
- **优先级**: low
- **状态**: pending
- **来源**: ASR 转写 / 2026-06-21
- **原话**: 开头数拍 → 主歌抒情 → 副歌反复 → 独白收尾
- **澄清**: 体现端到端 pipeline 对完整音乐结构的解析能力 (注:此条源于测试音频为歌曲,非真实需求)

## 功能 (2 条)
### FEAT-136180 真 GPU 推理 (RTX 3090 Ti + cuda float16)
- **优先级**: high
- **状态**: confirmed
- **来源**: 测试结果 / 2026-06-21
- **描述**: SenseVoice 0.5s/209s, campplus 1.5s, RTF 0.002 (500x 实时)

### FEAT-8D3315 完整转写输出
- **优先级**: high
- **状态**: confirmed
- **来源**: 测试结果 / 2026-06-21
- **描述**: 46 个 timestamped 句子, 8 个说话人聚类, 完整中文歌词

## 风险 (1 条)
### RISK-1E352E campplus 把歌曲重复段/和声误分成多个说话人
- **优先级**: medium
- **状态**: pending
- **来源**: 测试结果 / 2026-06-21
- **描述**: 实际 1 人演唱, 聚出 8 类;主唱占 60%

## 开放问题 (1 条)
### QUE-DFB5A0 真实多人会议怎么校准说话人?
- **优先级**: high
- **状态**: pending
- **来源**: 张胜东 / 2026-06-21
- **描述**: campplus threshold 0.704, 但歌曲有重复段需上下文合并

## 说话人映射 (8 个)
- SPEAKER_00 → 王心凌
- SPEAKER_01 → 和声1
- SPEAKER_02 → 主唱A
- SPEAKER_03 → 和声2
- SPEAKER_04 → 主唱B
- SPEAKER_05 → 独白
- SPEAKER_06 → 气口
- SPEAKER_07 → 气口2