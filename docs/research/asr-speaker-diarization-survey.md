# ASR + 说话人识别平台调研

**调研日期**: 2026-06-20
**触发问题**: "asr需要能分辨出不同的说话人吧? 最好是能明确是谁说的什么话。你看看是不是用现成工具更好? 各会议平台有现成的吗?"
**结论摘要**: 4 平台(飞书/腾讯/Zoom/钉钉)+ 1 通用方案(Otter.ai) **全部自带说话人识别的 ASR**,无需 VPBuddy 自实现。

---

## 一、对比总览(实时性 × 说话人 × 价格 × API)

| 平台 | 实时转写 | 说话人识别 | 转写准确率 | 价格 | API 调用方式 | VPBuddy 适配 |
|---|---|---|---|---|---|---|
| **飞书妙记** | ✅ 会中实时字幕 + 浮窗录音 | ✅ **声纹识别;若飞书用户自动关联昵称** | 清晰场景 98%+;5 人内交叉发言准确率 +12% | 基础版 **300 分/月免费**;企业版无限 | 1 万次 API/租户/月;OAuth 2.0 | ⭐⭐⭐⭐⭐ |
| **腾讯会议 AI** | ✅ 边录边转 + AI 字幕 | ✅ "说话人 01/02/03" 标签(单设备 ≤12 人) | 商业/企业版高;17 种语言翻译 | **商业/企业版**才完整;免费版受限 | 录制转写 GET API;**暂不支持 OAuth 2.0** | ⭐⭐⭐⭐ |
| **Zoom AI Companion** | ✅ 实时转写 | ✅(Otter 集成可加 speaker ID) | 英文高 | **仅英语** + 付费账号 | `GET /meetings/{id}/transcript` | ⭐⭐⭐(英文 only) |
| **钉钉 AI 听记** | ✅ 实时转写 + 多语言翻译 | ✅ 高精度说话人分离 | 高 | 含在钉钉会议套餐(100 方云会议室 600 元/月) | 钉钉开放平台 | ⭐⭐⭐ |
| **Otter.ai**(通用第三方) | ✅ live transcription | ✅ speaker ID 主力卖点 | 高 | Basic **免费 300 分/月**(单场 90 分上限);Pro $16.99/月 | REST API;跨平台 | ⭐⭐⭐⭐(跨平台场景) |

---

## 二、各平台详细能力

### 1. 飞书妙记(首选推荐 ⭐⭐⭐⭐⭐)

**核心优势**:
- ✅ **基础版就有 300 分钟/月免费**(个人用户额度)
- ✅ **会中实时字幕不占用语音转文字额度** — 边开会边免费看
- ✅ **若参会者为飞书用户 → 自动关联飞书昵称**(说话人识别最理想)
- ✅ 18+ 主流语言 + 中文方言(普通话/粤语/四川话/上海话)
- ✅ AI 降噪、上下文关联算法修正归属

**API 额度**(基础版):
- 妙记语音转文字:300 分钟/用户/月
- API 调用次数:1 万次/租户/月
- 基础 API 和独立付费接口不计入(身份验证、事件订阅、通讯录等)

**触发方式**:
- 桌面端会议中:工具栏 → 开始录制 → 云录制
- 移动端录音:可缩小为浮窗,实时转写

**企业级**:
- 飞书商业版/企业版:无时长上限(仅受企业存储空间限制)
- 飞书企业版存储:15 TB + 购买人数 × 30 GB

### 2. 腾讯会议 AI 录音 + 转写

**核心优势**:
- ✅ **声纹自动区分**:"说话人 01/02/03" 标签
- ✅ 单设备最多识别 12 名用户
- ✅ 17 种语言翻译(中文/英/日/韩/俄/泰/印尼/越南/马来/菲律宾/葡/土/阿/西/印地/法/德)
- ✅ AI 小助手 Pro 可对录音文件提问
- ✅ 加密链接分享 + 权限可控

**限制**:
- ⚠️ 完整功能需**商业版/企业版**(免费版受限)
- ⚠️ 暂不支持 OAuth 2.0 鉴权(用 SDK Key/Secret)

**API**:
- `GET /v1/meeting/{meeting_id}/record_transcripts/{record_id}` — 查询段落信息
- `GET /v1/meeting/{meeting_id}/record_transcripts/{record_id}/details` — 查询详情(含时间戳)

### 3. Zoom AI Companion

**核心优势**:
- ✅ **built-in 免费**(但需付费账号)
- ✅ 实时转写 + 自动云录制转写
- ✅ Scribe API(独立 API 服务):HuggingFace Open ASR Leaderboard 排名前列
- ✅ Fast Sync + Batch Transcription 两种模式

**限制**:
- ⚠️ **仅支持英语**(English only for automatic transcription)
- ⚠️ 需付费账号(Pro/Business/Enterprise)

**API**:
- `GET /meetings/{meetingId}/transcript`(需 past_meeting)
- Scribe API:`https://api.zoom.us/v2/scribe/...`

### 4. 钉钉 AI 听记

**核心优势**:
- ✅ 实时语音转文字 + 多语言翻译
- ✅ 高精度说话人分离
- ✅ 智能摘要生成 + 重点标记 + 任务提取

**计费**:
- 含在钉钉会议套餐
- 100 方云会议室:600 元/月/个
- 会议时长包:6000 元/年/10 万分钟
- 智能会议室高级版:498 元/年/个

### 5. Otter.ai(通用第三方)

**核心优势**:
- ✅ **跨平台集成**:Zoom/Teams/Google Meet/Webex 全支持
- ✅ 实时转写 + speaker ID
- ✅ Chrome 扩展 / 桌面应用 / 移动 App
- ✅ 自带"AI 销售代理""会议代理""SDR 代理"

**价格**:
- Basic:**免费 300 分/月**,**单场 90 分上限**,终身 3 个文件导入
- Pro:$16.99/月($8.33/月 年付),1,200 分/月
- Business:$30/月($19.99/月 年付),6,000 分/月
- Enterprise:定制

**开源替代**:
- Vexa(开源 meeting bot API,自托管) — 但需自己部署

---

## 三、对 VPBuddy 架构的影响

### v1.12 之前
- §4.1 "自实现 ASR 模块"
- §六 选 ASR 服务(通义/火山/讯飞/Whisper)

### v1.12 之后
- §4.1 拆两条通道:
  - **主通道**:`MeetingAdapter.getTranscript()` — 平台原生 ASR,统一 schema,带说话人标签
  - **副通道**:音频流 — 可选,VPBuddy 自定义分析

### v1.13 候选(本次调研支持)
- **首选飞书妙记**:零成本 + 说话人自动关联昵称 + API 充足
- 其他平台通过 MeetingAdapter 走抽象层(防止锁定单一平台)

---

## 四、关键 Schema 统一

```typescript
interface TranscriptSegment {
  timestamp: number;          // 毫秒(从会议开始计)
  speaker_id: string;          // 平台用户 ID(匿名也 OK)
  speaker_name?: string;       // 平台显示名(飞书/腾讯自动关联,VPBuddy 可标"我自己")
  text: string;                // 转写文本
  confidence?: number;         // 转写置信度
  language?: string;           // zh-CN / en-US 等
  is_final: boolean;           // true=已确认段;false=临时(可能还会改)
}
```

---

## 五、参考链接

- 飞书妙记介绍: <https://www.feishu.cn/content/article/7602953875972263132>
- 飞书妙记规则(2024-12 调整): <https://www.feishu.cn/new-announcement/pricing-adjustment2024>
- 腾讯会议 AI 录音: <https://meeting.tencent.com/news/aily20250611.html>
- 腾讯会议录制转写 API: <https://meeting.tencent.com/support-doc-detail/797/index.html>
- Zoom AI Companion: <https://uis.jhu.edu/zoom/zoom-ai-companion>
- Zoom 转写 API(开发者论坛): <https://devforum.zoom.us/t/api-to-get-an-ai-companion-generated-transcript/142661>
- 钉钉 AI 听记: <https://www.dingtalk.com/qidian/page-IU0rZ8eQ.html>
- Otter.ai 价格: <https://spokenly.app/blog/otter-ai-pricing>

---

---

# v2(2026-06-20 追加):"实时拿到 ASR 文字"的真实方案

**触发问题**: "它显示的实时字幕,你能拿到吗?" + "我们需要能实时拿到 asr 文字的方案"

**关键结论**:
- ❌ **飞书 / 腾讯会议 / 钉钉:实时字幕是客户端 UI,没有开放流式 API 给开发者**
- ✅ **Zoom RTMS SDK:唯一主流平台原生流式 ASR(英文为主)**
- ✅ **自接音频流 + 第三方 ASR:真正通用的中文实时方案**(讯飞 RTASR / WhisperLiveKit)
- ✅ **小鱼易连 WebSocket 协议:最完整的协议参考模板**

---

## 一、平台原生"实时字幕流"曝光度

| 平台 | 实时字幕 UI | **API 是否暴露流式字幕** | 程序能拿流? |
|---|---|---|---|
| **飞书** | ✅ 会中滚动(用户可见) | ❌ **没暴露** | 仅会后妙记完整转写(Webhook → REST) |
| **腾讯会议** | ✅ 会中滚动 | ❌ **没暴露** | 仅会后云录制转写 |
| **Zoom** | ✅ | ✅ **RTMS SDK** | ✅ per-participant audio + transcript over WebSocket |
| **钉钉** | ✅ | ❌ 未明确 | — |
| **小鱼易连** | ✅ | ✅ **WebSocket push_result** | ✅ 流式 |

**关键解释(飞书为例)**:
- 飞书开放平台的 WebSocket 长连接(`lark-oapi` SDK)用于接收 **IM 消息 / 卡片交互 / 会议事件**(如 `meeting.recording_ready`),**不是接收字幕流**
- 飞书妙记的"实时"= 会中 UI 滚动 + 会后完整转写(两件事,**API 只能拿会后那个**)
- `meeting.recording_ready` Webhook → 调用 GET API 拿完整转写(带说话人)= **会后**,不是会中段段推

---

## 二、4 个"实时拿 ASR 文字"方案

### 方案 A: Zoom RTMS SDK ⭐⭐⭐⭐⭐(英文场景首选)

**SDK**: `github.com/zoom/rtms` — C++ SDK + Node.js/Python/Go bindings

**工作流**:
1. 配置 Zoom App Marketplace 订阅 RTMS 生命周期事件(Webhook)
2. 服务端启动 RTMS SDK,加入会议流(per-participant)
3. 通过 WebSocket 实时接收 audio + transcript 段
4. SDK 支持:Audio(原始 PCM) + Video + Transcript(平台 ASR)

**代码示例**(Node.js):
```js
import rtms from "@zoom/rtms";
const client = new rtms.Client();
client.onAudioData((data, timestamp, metadata) => {
  // data: 原始音频字节(PCM)
  // metadata.userName: 说话人
});
client.join({
  meeting_uuid: "xxx",
  rtms_stream_id: "xxx",
  server_urls: "wss://rtms.zoom.us",
});
```

**限制**:
- ⚠️ **英文为主**(46 种语言是 AI Companion 翻译能力,不是 ASR)
- ⚠️ 需付费账号
- ✅ Node.js 22+ / Python 3.10+ / darwin-arm64 / linux-x64

### 方案 B: 自接音频流 + 讯飞 RTASR ⭐⭐⭐⭐(中文场景首选)

**协议**: `wss://rtasr.xfyun.cn/v1/ws?appid=xx&ts=yy&signa=zz`
- 鉴权:HMAC-SHA1(已弃) 或 HMAC-SHA256(新)
- 握手 → 流式推音频 → 流式收结果

**推送数据**(麦克风采集的 PCM 音频):
```
二进制帧:每帧 ~40ms PCM(16kHz,16bit,单声道)
```

**接收结果**(JSON):
```json
{
  "action": "result",
  "code": "0",
  "data": "{...中间结果...}",
  "desc": "success",
  "sid": "rta0000000e@ch..."
}
```

**价格**:
- 免费包:24 小时(15 天有效期,1 路并发)
- 新用户礼包:最高 50 小时(免费,1 年)
- 套餐:¥9.9/小时起;¥4.9/小时(3000 小时套餐)
- 并发套餐:1 万-2 万元/路/年

**关键问题**: **怎么拿到会议音频?** → VPBuddy 跑在 VP 设备上,接 VP **系统的扬声器回放**(loopback) 或 VP 的麦克风
- **macOS/iOS**: `AVAudioEngine` + `AVAudioSession` 输出 tap
- **Windows**: WASAPI loopback
- **Linux**: PulseAudio monitor source

**说话人识别**: ⚠️ 讯飞 RTASR 不带说话人分离 —— 需要自接 `pyannote-audio`(开源声纹聚类,准确率 80-90%)

### 方案 C: 自接音频流 + WhisperLiveKit ⭐⭐⭐⭐(完全开源)

**优势**:
- 完全开源(MIT 协议)
- 中文 + 英文 + 多语种
- 本地运行,无平台锁定
- 价格: **¥0**(只需 GPU 服务器,4090 一块够)

**架构**:
- VP 设备 loopback → WhisperLiveKit 服务(自托管 GPU)→ WebSocket 流式转写
- 说话人识别:pyannote-audio(同方案 B)

**限制**:
- ⚠️ 需要 GPU 服务器(MiniMax/4090/3090 级别)
- ⚠️ 延迟稍高(本地推理 100-500ms)
- ⚠️ 说话人识别准确度看声纹质量

### 方案 D: 小鱼易连 WebSocket 协议 ⭐⭐⭐(协议参考)

**完整协议**:
```
握手 URL: ws://host:port/recv/asr/result/v1?appid=xx&ts=yy&signature=zz
   - 签名:HMAC_SHA256(appid + ts, secret) → base64 → urlencode

握手成功后:
  ↓
开始: action=begin, sid=ch312c0e3f63609f0900, meetingId=...
  ↓
段段推: action=push_result
  data: {
    "callNumber": "+86-10506",   ← 说话人号码(平台唯一)
    "dn": "测试员",              ← 说话人姓名
    "callUri": "240438186@DESK",
    "pid": 65664,
    "seId": "jy2i2kb623ikj...",  ← 声纹 ID
    "srcLang": "zh",
    "src": "好的领导",           ← 转写文本(实时段段推!)
    "targetLang": "en",
    "target": "Good leadership.",
    "startTime": 1772447728343,  ← 段开始毫秒
    "endTime": 1772447730743,    ← 段结束毫秒
    "seqNo": 1,
    "isActive": true,
    "isEnd": false
  }
  ↓
结束: action=end
```

**价值**: 即使不用小鱼易连,**这个协议是 VPBuddy 自接 ASR 的内部协议模板** —— VPBuddy 也可以用同样的 push_result 协议把转写段喂给后端。

---

## 三、推荐方案:v1.13 双轨混合

| 场景 | 实时转写来源 | 说话人识别 |
|---|---|---|
| **飞书 / 腾讯 / 钉钉(中文为主)** | 自接音频流 + 讯飞 RTASR 或 WhisperLiveKit | pyannote-audio 声纹聚类 |
| **Zoom(英文为主)** | Zoom RTMS SDK | 平台原生(per-participant) |
| **Otter.ai / 通用第三方** | Otter live transcription API | Otter 原生 speaker ID |
| **小鱼易连(参考协议)** | 平台原生 WebSocket push_result | callNumber/seId |

---

## 四、对 VPBuddy 架构的影响(v1.13 候选)

### 新增模块(§4.1 会议接入层)

```
MeetingAdapter
├── PlatformASRAdapter       (平台原生,飞书/Zoom 等)
├── LocalAudioCapture         (新!) — VP 设备 loopback / 麦克风
├── ASRProvider               (新!) — 讯飞 RTASR / WhisperLiveKit
├── Diarization               (新!) — pyannote-audio 声纹聚类
└── RealtimeSegmentStream     (新!) — 统一 push_result 协议
```

### v1.13 修改清单

| § | v1.12 | v1.13 |
|---|---|---|
| §3.1 数据流 | "平台原生 ASR" 单通道 | **双通道**:平台原生(后处理)+ 自接音频流(实时) |
| §4.1 接入层 | 仅 MeetingAdapter.getTranscript() | **+3 模块**:LocalAudioCapture + ASRProvider + Diarization |
| §六 技术选型 | 飞书默认 | **+ 自接音频流(讯飞/WhisperLiveKit) + pyannote** |
| §7.1 风险 | 平台 ASR 说话人识别错 | **+ 设备 loopback 权限 + pyannote 准确度 + 中英文混合** |
| §九 版本表 | v1.12 | **+ v1.13** |

### 关键约束(VP 设备)

- ✅ **VP 必须用桌面客户端**(不是手机/iPad)
- ✅ **VP 必须给 VPBuddy 麦克风/系统音频权限**
- ✅ **OS 支持**:
  - macOS: `AVAudioEngine` 输出 tap
  - Windows: WASAPI loopback
  - Linux: PulseAudio monitor source

### 说话人识别双源融合

- **飞书用户自动关联昵称**(会后 REST 转写带)
- **pyannote 声纹聚类**(自接音频流,带 speaker_id 0/1/2)
- **VPBuddy 内部合并**:用 callNumber 或声纹 ID 做 key,跨源对齐

---

## 五、待验证(下次)

- ⏳ pyannote-audio 中文声纹准确度(实测 80-90% 准确率是否可接受?)
- ⏳ 飞书会议 VP 设备 loopback 实际延迟(讯飞 RTASR 全链路 < 2s?)
- ⏳ pyannote + 飞书 REST 说话人融合的归一化逻辑
- ⏳ WhisperLiveKit GPU 服务器成本 vs 讯飞 API 成本(MVP 阶段哪个合适?)

---

## 六、参考链接

**平台官方文档**:
- 飞书会议妙记介绍: <https://www.feishu.cn/content/article/7602953875972263132>
- 飞书开放平台事件订阅: <https://open.feishu.cn/document/event-subscription-guide/callback-subscription/callback-overview>
- 腾讯会议转写 API: <https://meeting.tencent.com/support-doc-detail/797/index.html>
- 腾讯会议 Webhook: <https://meeting.tencent.com/support/topic/589>
- Zoom RTMS SDK GitHub: <https://github.com/zoom/rtms>
- Zoom RTMS 文档: <https://developers.zoom.us/docs/rtms/sdk>
- Zoom AI Companion 转写 API 论坛: <https://devforum.zoom.us/t/api-to-get-an-ai-companion-generated-transcript/142661>

**第三方 ASR**:
- 讯飞 RTASR WebAPI: <https://www.xfyun.cn/doc/asr/rtasr/API.html>
- 讯飞实时语音转写(标准版): <https://www.xfyun.cn/service/lfasr>
- 小鱼易连实时转写回调协议: <https://openapi.xylink.com/common/meeting/doc/ai_realtime_transcription_callback>

**开源**:
- WhisperLiveKit: 见 GitHub 多个 fork
- pyannote-audio: <https://github.com/pyannote/pyannote-audio>

**WhisperLiveKit + 飞书集成参考**: <https://adg.csdn.net/697076b9437a6b40336a5e02.html>

