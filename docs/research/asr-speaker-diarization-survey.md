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

## 六、待调研(下一步)

- ⏳ **实时推送机制**:Webhook / WebSocket / 长轮询?(各平台实时获取转写的具体方式 + 延迟)
- ⏳ **飞书 Webhook 事件订阅**详细 schema
- ⏳ **腾讯会议 OpenAPI** 实时通道
- ⏳ **Zoom WebSocket** 流式输出
- ⏳ **钉钉 Stream API** 推送模式

(下次调研更新)
