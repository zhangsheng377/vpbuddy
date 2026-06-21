# PHASE2_AUDIO_REAL 任务列表

- 会议: PHASE2_AUDIO_REAL
- session_id: meeting:PHASE2_AUDIO_REAL:tasks
- 最后更新: 2026-06-21 (本次维护: **无变化 (第 2 次核对)** — 累积 timestamp 2026-06-21T05:28:13+00:00 与上一版一致, 无新增 REQ/RISK/QUE, 无用户指令变更, T-001~T-007 已穷尽当前累积交付物; 映射核对: 2 REQ→T-002, 1 GOAL→全部, 2 FEAT→T-001/T-002/T-003, 1 RISK→T-005, 1 QUE→T-006, 8 speaker→T-003)
- 目标: VPBuddy GPU pipeline 端到端测试 (SenseVoice + campplus + state 累积 + 6 docs)

---

## ✅ 已完成

### T-001 真 GPU 推理 pipeline 跑通
- **负责人**: VPBuddy 主 session
- **工期**: 1 天
- **依赖**: -
- **状态**: done
- **验收标准**: RTX 3090 Ti + cuda float16; SenseVoice 0.5s/209s, campplus 1.5s, RTF 0.002 (500x 实时)

### T-002 ASR 转写输出
- **负责人**: ASR 子 session
- **工期**: 0.5 天
- **依赖**: T-001
- **状态**: done
- **验收标准**: 46 个 timestamped 句子, 完整中文歌词, 歌曲结构清晰(开头数拍→主歌抒情→副歌反复→独白收尾)

### T-003 说话人聚类输出
- **负责人**: speaker 子 session
- **工期**: 0.5 天
- **依赖**: T-001
- **状态**: done
- **验收标准**: campplus 聚出 8 类, SPEAKER_00~07 映射 王心凌/和声1/主唱A/和声2/主唱B/独白/气口/气口2

---

## 🚧 进行中

### T-004 子 session 文档交付 (6 份)
- **负责人**: 6 个子 session 并行 (本 session 是其中之一)
- **工期**: 1 天
- **依赖**: T-001, T-002, T-003
- **状态**: in_progress
- **验收标准**: 6 份 docs 落地到 `docs/PHASE2_AUDIO_REAL/`, 每份有明确交付物, 互引形成链路; 主 session 定义具体拆分

---

## 📋 待办

### T-005 说话人聚类伪影分析 (RISK-1E352E)
- **负责人**: speaker 子 session
- **工期**: 0.5 天
- **依赖**: T-003
- **状态**: pending
- **验收标准**: 解释 1 人唱的歌被聚出 8 类的成因(重复段+和声+气口); 明确这是歌曲特性, 非 campplus bug; 不影响多人会议场景结论

### T-006 真实多人会议说话人校准方案 (QUE-DFB5A0)
- **负责人**: speaker 子 session + VPBuddy 主
- **工期**: 1 天
- **依赖**: T-005
- **状态**: pending
- **验收标准**: 回答 campplus threshold 0.704 在真实会议(2-5 人) 的适用性; 给出校准流程(阈值/上下文合并/说话人注册/人工校验); 写入 speaker 子 session doc

### T-007 Phase 2 准入决策
- **负责人**: VPBuddy 主 session
- **工期**: 0.5 天
- **依赖**: T-005, T-006
- **状态**: pending
- **验收标准**: 一份决策文档, 给出 "GPU pipeline 是否进入生产" 的明确结论 + Phase 3 待办清单