# PHASE2_AUDIO_REAL API 设计

最后更新: 2026-06-21
session_id: meeting:PHASE2_AUDIO_REAL:api
目标: VPBuddy GPU pipeline 端到端测试 (SenseVoice ASR + campplus 说话人 + state 累积 + 子 session 6 docs)

---

## 1. 设计原则

| 原则 | 说明 |
|---|---|
| **契约优先** | 定义模块间数据/调用契约,实现方式灵活 |
| **三轨实现** | 同名接口可在 Python function / NFS file / HTTP 三种实现之间切换,标注在 `impl` 字段 |
| **YAGNI** | 只为已有 FEAT/REQ 服务;QUE-DFB5A0 的校准 API 是预案,Phase 3 才实现 |
| **单一可信源** | MeetingState 由主 session 写,子 session 只读 + 增量 patch |
| **幂等** | 重跑 pipeline / 重触发子 session 不破坏累积(PATCH 而非 PUT 整块替换) |

---

## 2. 数据模型 (核心 Schema)

```yaml
openapi: 3.0.3
info:
  title: VPBuddy PHASE2_AUDIO_REAL
  version: 0.2.0

components:
  schemas:

    # ---------- 原子片段 ----------
    TimestampedSegment:
      type: object
      description: 单句带时间戳的 ASR 输出
      required: [text, start, end]
      properties:
        text:    { type: string,  example: "明天我要嫁给你了" }
        start:   { type: number,   description: "秒,相对音频起点", example: 12.34 }
        end:     { type: number,   example: 14.56 }
        confidence: { type: number, minimum: 0, maximum: 1, example: 0.95 }
      # 字段说明:
      # text — SenseVoice 直接产出,中文歌词场景完整
      # start/end — 句首/句尾,精度 0.01s;campplus 融合时按中点最近匹配
      # confidence — Phase 2 当前未使用,Phase 3 引入阈值过滤时启用

    SpeakerTurn:
      type: object
      description: campplus 聚类产出的单 turn
      required: [speaker, start, end]
      properties:
        speaker: { type: string, pattern: "^SPEAKER_[0-9]{2}$", example: "SPEAKER_00" }
        start:   { type: number,  example: 0.0 }
        end:     { type: number,  example: 209.0 }
      # 字段说明:
      # speaker — campplus 自动编号,人类不可读;需经 speaker_map 映射
      # start/end — 整段音频的连续区间,可能跨多句

    DiarizedSegment:
      type: object
      description: ASR 句子 + 说话人标签的融合单元(融合层产出)
      required: [text, start, end, speaker]
      properties:
        text:    { type: string }
        start:   { type: number }
        end:     { type: number }
        speaker: { type: string, pattern: "^SPEAKER_[0-9]{2}$" }
        speaker_label: { type: string, example: "王心凌" }
      # 字段说明:
      # speaker_label — 经 speaker_map 翻译后的人类可读名;若未映射则回退到 SPEAKER_XX
      # 中点最近融合策略:每条 TimestampedSegment 与 SpeakerTurn 按 (start+end)/2 最近距离配对

    # ---------- MeetingState 累积项 ----------
    Requirement:
      type: object
      required: [id, text, priority, source]
      properties:
        id:       { type: string, example: "REQ-D25868" }
        text:     { type: string }
        priority: { enum: [low, medium, high, critical] }
        source:   { type: string, description: "谁提出的(用户/ASR/会议)", example: "ASR 转写 / 2026-06-21" }
        quote:    { type: string, description: "原话摘录" }
        clarify:  { type: string, description: "澄清/解读" }

    Goal:
      type: object
      required: [id, text]
      properties:
        id:       { type: string, example: "GOAL-18CA7F" }
        text:     { type: string }
        priority: { enum: [low, medium, high] }
        status:   { enum: [pending, in_progress, done, cancelled] }

    Feature:
      type: object
      required: [id, text]
      properties:
        id:       { type: string, example: "FEAT-136180" }
        text:     { type: string }
        status:   { enum: [pending, confirmed, rejected] }

    Risk:
      type: object
      required: [id, text, severity]
      properties:
        id:       { type: string, example: "RISK-1E352E" }
        text:     { type: string }
        severity: { enum: [low, medium, high] }
        status:   { enum: [open, mitigated, accepted] }

    OpenQuestion:
      type: object
      required: [id, text]
      properties:
        id:       { type: string, example: "QUE-DFB5A0" }
        text:     { type: string }
        priority: { enum: [low, medium, high] }

    SpeakerMap:
      type: object
      description: SPEAKER_XX → 人类可读标签的映射
      additionalProperties:
        type: string
        example: { "SPEAKER_00": "王心凌", "SPEAKER_01": "和声1" }
      # 字段说明:
      # 键必须匹配 ^SPEAKER_[0-9]{2}$
      # 值在歌曲场景是 王心凌/和声/主唱/独白/气口;真实会议场景是人名或角色
      # PATCH 合并语义:传入键覆盖原值;若值=null 则删除该映射

    MeetingState:
      type: object
      description: 会议单一可信源(NFS JSON)
      required: [meeting_id, updated_at, pipeline_status]
      properties:
        meeting_id:    { type: string, example: "PHASE2_AUDIO_REAL" }
        updated_at:    { type: string, format: date-time }
        platform:      { type: string, example: "feishu" }
        pipeline_status:
          enum: [idle, uploading, asr_running, diarization_running, fusing, ready, failed]
        requirements:  { type: array, items: { $ref: "#/components/schemas/Requirement" } }
        goals:         { type: array, items: { $ref: "#/components/schemas/Goal" } }
        features:      { type: array, items: { $ref: "#/components/schemas/Feature" } }
        risks:         { type: array, items: { $ref: "#/components/schemas/Risk" } }
        open_questions:{ type: array, items: { $ref: "#/components/schemas/OpenQuestion" } }
        speaker_map:   { $ref: "#/components/schemas/SpeakerMap" }
        transcript:
          type: array
          items: { $ref: "#/components/schemas/DiarizedSegment" }
        pipeline_metrics:
          type: object
          description: GPU pipeline 实测指标(Phase 2 已填)
          properties:
            asr_seconds:        { type: number, example: 0.5 }
            diarization_seconds:{ type: number, example: 1.5 }
            audio_seconds:      { type: number, example: 209.0 }
            rtf:                { type: number, example: 0.002, description: "总耗时/音频时长" }
            device:             { type: string,  example: "RTX 3090 Ti" }
            dtype:              { type: string,  example: "float16" }

    # ---------- 子 session ----------
    DocKind:
      type: string
      enum: [req, arch, tasks, api, risk, demo]
      description: 6 类交付物

    SubSessionResult:
      type: object
      required: [kind, session_id, output_path, status]
      properties:
        kind:        { $ref: "#/components/schemas/DocKind" }
        session_id:  { type: string, example: "meeting:PHASE2_AUDIO_REAL:api" }
        output_path: { type: string, example: "/home/zsd/vpbuddy/docs/PHASE2_AUDIO_REAL/api.md" }
        status:      { enum: [pending, running, done, failed] }
        last_change: { type: string, description: "本 session 相对上一版的增量" }

    # ---------- 校准(预案,Phase 3 实现) ----------
    SpeakerCalibration:
      type: object
      description: campplus 校准参数,响应 QUE-DFB5A0
      properties:
        threshold: { type: number, minimum: 0, maximum: 1, example: 0.704, description: "合并阈值,越高越严格" }
        merge_consecutive: { type: boolean, example: true, description: "合并同说话人的相邻 turn" }
        min_turn_seconds: { type: number, example: 1.0, description: "短于此值的 turn 归并到上下文" }
        speaker_hints:
          type: array
          description: 先验说话人(注册过的声纹 embedding)
          items:
            type: object
            properties:
              name: { type: string, example: "张胜东" }
              embedding_path: { type: string, example: "/var/lib/vpbuddy/embeddings/zsd.npy" }
      # 字段说明:
      # threshold — campplus 余弦相似度阈值,默认 0.704;歌曲场景需降低避免重复段被切开
      # merge_consecutive — 处理"主唱A/B"这种同人不同 turn 的误分
      # speaker_hints — 真实会议场景下,用注册过的声纹做有监督聚类
```

---

## 3. 端点列表

VPBuddy 当前是**本地单用户工具**,所有接口用 Python function / NFS file 直连。
下表用 OpenAPI 风格描述契约,标注实际实现方式(impl),Phase 3 再视情况升级到 HTTP。

### 3.1 Pipeline 控制

| Method | Path | 描述 | impl |
|---|---|---|---|
| POST | `/v1/meetings/{mid}/audio` | 上传/注册音频路径 | Python: `audio_loader.load(path)` |
| POST | `/v1/meetings/{mid}/pipeline/run` | 触发完整 pipeline(ASR + campplus + 融合) | Python: `pipeline.run(mid)` |
| GET  | `/v1/meetings/{mid}/pipeline/status` | 查询 pipeline 进度 | Python: `pipeline.status(mid)` |

### 3.2 结果查询

| Method | Path | 描述 | impl |
|---|---|---|---|
| GET | `/v1/meetings/{mid}/transcript` | 获取融合后的 DiarizedSegment[] | Python: `state.get(mid).transcript` |
| GET | `/v1/meetings/{mid}/speakers` | 获取 speaker_map | Python: `state.get(mid).speaker_map` |
| GET | `/v1/meetings/{mid}/state` | 获取完整 MeetingState | NFS: `{nfs_root}/{mid}/state.json` |

### 3.3 MeetingState 累积(PATCH 语义)

| Method | Path | 描述 | impl |
|---|---|---|---|
| PATCH | `/v1/meetings/{mid}/state/requirements` | 追加/更新需求项 | Python: `state.append_requirement(mid, req)` |
| PATCH | `/v1/meetings/{mid}/state/goals` | 同上(goal) | Python: `state.append_goal(mid, goal)` |
| PATCH | `/v1/meetings/{mid}/state/features` | 同上(feature) | Python: `state.append_feature(mid, feat)` |
| PATCH | `/v1/meetings/{mid}/state/risks` | 同上(risk) | Python: `state.append_risk(mid, risk)` |
| PATCH | `/v1/meetings/{mid}/state/open_questions` | 同上(open_question) | Python: `state.append_question(mid, q)` |
| PATCH | `/v1/meetings/{mid}/speakers/{spkid}` | 更新说话人映射(合并/重命名) | Python: `state.set_speaker(mid, spkid, label)` |

### 3.4 子 session 触发(本会议核心)

| Method | Path | 描述 | impl |
|---|---|---|---|
| POST | `/v1/meetings/{mid}/subsessions/{kind}/run` | 触发 6 类交付物之一 | Python: `sub_session_controller.trigger(mid, kind)` |
| GET  | `/v1/meetings/{mid}/subsessions/{kind}` | 获取子 session 产出 | NFS: `{nfs_root}/{mid}/docs/{kind}.md` |

### 3.5 校准(预案,Phase 3)

| Method | Path | 描述 | impl |
|---|---|---|---|
| PUT | `/v1/meetings/{mid}/calibration` | 设置 campplus 校准参数 | Python: `calibration.set(mid, cfg)` |
| GET | `/v1/meetings/{mid}/calibration` | 读取当前校准 | Python: `calibration.get(mid)` |

---

## 4. 关键端点详细定义

### 4.1 POST /v1/meetings/{mid}/pipeline/run

```yaml
requestBody:
  required: false
  content:
    application/json:
      schema:
        type: object
        properties:
          force:    { type: boolean, default: false, description: "true 时即使 transcript 已存在也重跑" }
          steps:    { type: array, items: { enum: [asr, diarization, fuse, accumulate] } }
          calibration: { $ref: "#/components/schemas/SpeakerCalibration" }
responses:
  202:
    description: 接受,异步执行
    content:
      application/json:
        schema:
          type: object
          properties:
            job_id:    { type: string }
            status:    { enum: [asr_running, diarization_running, fusing] }
  409:
    description: pipeline 已在跑;等待当前 job 结束
```

**为何这样设计**:
- `force=true` 应对 Phase 2 测试场景:同一音频可能因模型升级重跑,需绕过幂等保护
- `steps[]` 让 caller 可只跑 diarization(对话题切换后只更新说话人而不重跑 ASR)
- `calibration` 内联,避免一次调用变两次(在重新 pipeline 时一并应用)

### 4.2 PATCH /v1/meetings/{mid}/state/requirements

```yaml
requestBody:
  required: true
  content:
    application/json:
      schema:
        oneOf:
          - $ref: "#/components/schemas/Requirement"
          - type: array
            items: { $ref: "#/components/schemas/Requirement" }
responses:
  200:
    description: 成功合并(按 id 去重,新项追加,同 id 字段更新)
    content:
      application/json:
        schema:
          type: object
          properties:
            added:    { type: array, items: { type: string } }
            updated:  { type: array, items: { type: string } }
            total:    { type: integer }
  422:
    description: id 格式不合法(必须 ^[A-Z]+-[0-9A-F]{6}$)
```

**为何 PATCH 而非 PUT**:
- 子 session 累积时不能影响其他子 session 已写入的项
- PUT 整块替换会丢失并发写入;PATCH 按 id 合并是安全的
- 字段级合并:id 存在则逐字段 diff,id 不存在则追加

### 4.3 POST /v1/meetings/{mid}/subsessions/{kind}/run

```yaml
parameters:
  - name: kind
    in: path
    required: true
    schema: { $ref: "#/components/schemas/DocKind" }
requestBody:
  required: false
  content:
    application/json:
      schema:
        type: object
        properties:
          session_id: { type: string, description: "固定复用模式,Phase 2 用 meeting:{mid}:{kind}" }
          include_state_diff: { type: boolean, default: true, description: "prompt 中是否包含累积 diff" }
responses:
  202:
    description: 已入队
    content:
      application/json:
        schema:
          $ref: "#/components/schemas/SubSessionResult"
  404:
    description: kind 非法(必须为 6 个 DocKind 之一)
```

**session_id 固定复用**:本会议所有 6 个子 session 用 `meeting:PHASE2_AUDIO_REAL:{kind}`,以便 Hermes session_search 复用历史决策(见 arch.md §2)。

### 4.4 PATCH /v1/meetings/{mid}/speakers/{spkid}

```yaml
parameters:
  - name: spkid
    in: path
    required: true
    schema: { type: string, pattern: "^SPEAKER_[0-9]{2}$" }
requestBody:
  required: true
  content:
    application/json:
      schema:
        type: object
        properties:
          label:    { type: string, nullable: true, description: "null 表示删除该映射" }
          merge_into: { type: string, pattern: "^SPEAKER_[0-9]{2}$", description: "把 spkid 合并到目标(用于合并主唱A/B)" }
responses:
  200:
    description: 映射已更新/合并
  409:
    description: 循环合并(spkid→A→spkid)
```

**merge_into 语义**:针对 RISK-1E352E(歌曲 8 类实际 1 人),提供"反向合并"能力,把 SPEAKER_02/04(主唱A/B)合并到 SPEAKER_00(王心凌),让后续 DiarizedSegment 统一显示正确标签。

---

## 5. 错误码

| 状态码 | 含义 | 触发场景 | 客户端处理建议 |
|---|---|---|---|
| **400** | 请求体 schema 非法 | JSON 解析失败、字段类型错 | 检查 payload,不重试 |
| **401** | 未授权(Phase 3 启用) | 缺 token 或 token 过期 | 刷新 token 后重试 |
| **403** | 跨用户访问 | mid 不属于当前用户(Phase 3) | 报错,不重试 |
| **404** | meeting 不存在 | mid 拼错或 state.json 未初始化 | 创建 meeting 后重试 |
| **409** | 状态冲突 | pipeline 已在跑 / 循环合并 / DocKind 已 done 不允许 rerun | 等当前 job 结束,或检查循环 |
| **413** | 音频过大 | 上传 > 2GB(暂定阈值) | 切片后重试 |
| **422** | 字段语义校验失败 | id 格式错、enum 值不在范围内 | 按 schema 修正字段 |
| **429** | 触发频率超限 | 1 分钟内触发同 kind 子 session > 3 次 | 等 60s 后重试 |
| **500** | pipeline 内部错误 | GPU OOM / 模型加载失败 / NFS 写失败 | 查日志,Phase 2 测试场景可手动重跑 |
| **503** | GPU 不可用 | 192.168.10.63 关机或 WOL 未触发 | 等 WOL 30s 后重试 |

---

## 6. 当前实现映射 (Phase 2 实际状态)

| 接口 | 实现位置 | 状态 |
|---|---|---|
| `pipeline.run(mid)` | `vpbuddy/pipeline.py` | ✅ done (RTF 0.002) |
| `state.append_*` | `vpbuddy/state.py` | ✅ done (5 类累积项) |
| `state.set_speaker` | `vpbuddy/state.py` | ✅ done (song 8 类映射) |
| `sub_session_controller.trigger` | `vpbuddy/sub_session_controller.py` | ✅ done (6 kind) |
| `calibration.{get,set}` | — | ⏸ 预案,响应 QUE-DFB5A0;T-006 完成后再实现 |

---

## 7. 与其他子 session doc 的引用关系

- **架构模块边界**: 见 [arch.md §2 关键模块](../PHASE2_AUDIO_REAL/arch.md) — 本文件的端点对应 arch.md 的"接口"列
- **需求来源**: REQ-D25868/REQ-8B57B4 来自 [req.md](../PHASE2_AUDIO_REAL/req.md),决定了 transcript schema 必须能容纳歌词重复段
- **风险**: RISK-1E352E(8 类误分)→ 推动 §4.4 PATCH /speakers/{spkid} 的 merge_into 设计
- **开放问题**: QUE-DFB5A0 → 推动 §3.5 校准端点(预案)
- **任务**: 见 [tasks.md T-005/T-006](../PHASE2_AUDIO_REAL/tasks.md),校准 API 在 T-006 中实现

---

## 8. YAGNI 清单(本会议不做)

- ❌ HTTP server / FastAPI(本地单用户,NFS 文件够用)
- ❌ WebSocket pipeline 进度推送(轮询 GET status 即可,频率低)
- ❌ SDK / 客户端库(6 个子 session 直接调 Python)
- ❌ 多租户鉴权(401/403 占位,Phase 3 启用)
- ❌ 说话人 embedding 持久化(QUE-DFB5A0 解决后再设计)
- ❌ UML 类图(OpenAPI schema 描述数据形状足够)
