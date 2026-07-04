# VPBuddy 项目环境与服务器信息

> 最后更新: 2026-07-04 (v2 — 环境安装 + ASR 实测)

---

## 1. 项目地址

- **GitHub**: https://github.com/zhangsheng377/vpbuddy/
- **最新版本**: v0.8.5（2026-07-04）
- **描述**: 本地优先的会议操作系统级 AI 助手

---

## 2. 本机 (Windows 开发机)

| 项目 | 信息 |
|------|------|
| 操作系统 | Windows |
| 角色 | 客户端端到端测试 + 代码编写 |
| 本地项目路径 | `C:\Users\43587\Desktop\codes\vpbuddy\` |
| 客户端技术 | Tauri 2.6+ (Rust) - 编译产物为 .exe |
|  | cargo (如果环境有) 可以编译/测试客户端 |
| 服务端组件 | Python 后端，在本机运行 `vpbuddy ui` 启动 Web UI |

### 2.1 快速命令

```powershell
# 启动 (克隆后)
cd C:\Users\43587\Desktop\codes\vpbuddy
pip install -e .
vpbuddy ui                 # 启动 Web UI (port 8765)
vpbuddy controller --start # 启动后台 controller
```

### 2.2 客户端编译 (Rust/Tauri)

```powershell
cd vpbuddy-client
cargo build --release       # 编译客户端
cargo test --lib            # 运行 Rust 单元测试 (当前 17 个)
```

---

## 3. Linux 开发服务器

| 项目 | 信息 |
|------|------|
| IP 地址 | `192.168.10.5` |
| 用户 | `zsd` |
| 密码 | `292929` |
| SSH 端口 | 22 (默认) |
| 角色 | Linux 端开发/编译/测试 |
| 项目路径 | 需寻找（之前应该已有 `vpbuddy` 目录） |

### 3.1 快速连接

```bash
ssh zsd@192.168.10.5
# 密码: 292929
```

### 3.2 注意事项

- 用于 cargo/Rust 编译（Linux 环境下 Tauri 编译、测试等）
- 之前的开发文件应该已经存在，需要查找确认

---

## 4. 公网 GPU 服务器

| 项目 | 信息 |
|------|------|
| IP 地址 | `47.100.182.3` |
| 用户 | `root` |
| 密码 | `bUIZcWZfI1h0smfn` |
| SSH 端口 | `16159` |
| 角色 | VP Buddy 服务端部署 (GPU 加速) |
| 内部端口 | `8765` |
| 公网端口 | `28765` (映射到内网 8765) |

### 4.1 快速连接

```bash
ssh -p 16159 root@47.100.182.3
# 密码: bUIZcWZfI1h0smfn
```

### 4.2 服务访问

服务端 Web UI 通过公网访问:
```
http://47.100.182.3:28765
```

### 4.3 注意事项

- GPU 加速运行 ASR (funasr paraformer-zh) 和 embedding
- 部署了 hermes-agent + vpbuddy
- 客户端默认连接此公网地址 (v0.8.5 新行为)

---

## 5. 架构概览

```
┌─────────────────────────────────────────────────────┐
│ VP 桌面客户端 (Ubuntu / macOS / Windows)              │
├─────────────────────────────────────────────────────┤
│ Audio loopback (PipeWire / WASAPI / BlackHole)      │
│ ↓                                                    │
│ ASR (funasr paraformer-zh)                          │
│ ↓                                                    │
│ MeetingState (5 类事实累积)                           │
│ ↓                                                    │
│ 6 × sub_session (in-process AIAgent)                │
│ ┌────┬────┬────┬────┬────┬────┐                     │
│ │req │arch│tasks│api │risk│demo│                     │
│ └────┴────┴────┴────┴────┴────┘                     │
│ ↓                                                    │
│ Knowledge Base (Chroma + sentence-transformers)      │
│ ↓                                                    │
│ Web UI (FastAPI + Vanilla JS, port 8765)            │
└─────────────────────────────────────────────────────┘
         │
         ▼ (可选)
┌────────────────────────┐
│ GPU 服务器 (CUDA)       │
│ ASR/Embedding 加速      │
└────────────────────────┘
```

---

## 6. 环境安装状态

### 6.1 Windows 本机 (2026-07-04)

| 组件 | 状态 | 版本/路径 |
|------|------|-----------|
| Python | ✅ | 3.10.11 (项目需 3.11+, 安装包已下载待安装) |
| Node.js | ✅ | 22.16.0 |
| npm | ✅ | 10.9.4 |
| Rust/cargo | ✅ | 1.96.1 (stable-x86_64-pc-windows-gnu) |
| 安装路径 | | `$env:USERPROFILE\.rustup\toolchains\stable-x86_64-pc-windows-gnu\bin\` |
| 客户端编译 | ⚠️ | 需通过 GitHub CI (SOLO 沙箱环境限制无法执行子进程文件写入) |

### 6.2 Linux 开发服务器 (192.168.10.5)

| 组件 | 状态 | 版本/路径 |
|------|------|-----------|
| Python | ✅ | 3.12.3 (系统自带) |
| Rust/cargo | ✅ | 在 `~/.cargo/bin/` 已安装 (已添加 `~/.profile`) |
| Tauri 依赖 | ✅ | WebKit2GTK + libgtk-3 已安装 (Ubuntu 24.04) |
| 项目路径 | | `/home/zsd/vpbuddy/`（已同步到 v0.8.5） |

### 6.3 GPU 服务器 (47.100.182.3)

| 组件 | 状态 | 详情 |
|------|------|------|
| GPU | ✅ | NVIDIA GeForce RTX 3090 (24GB) |
| 服务进程 | ✅ | `ui_server` (port 8765) + `sub_session_controller` |
| Web UI | ✅ | `http://47.100.182.3:28765` |
| 公网端口 | | 28765 → 内网 8765 |

---

## 7. ASR 流水线延迟实测 (2026-07-04)

### 7.1 测试方法

在 GPU 服务器（RTX 3090）上直接执行 `gpu_transcribe.process()`，分步计时。
音频: 真实中文语音 `test_zh_16k_mono.wav`，截取前 10 秒。

### 7.2 三步流水线实际测量

```
步骤                          耗时(第1次)  耗时(第2次)
──────────────────────────────────────────────────────
[1] Audio load (torchaudio)    3.5s        3.5s*
[2] funasr 一站式推理           49.1s       30.4s
    ├─ ASR (paraformer-zh)     < 0.5s      < 0.5s
    ├─ VAD (fsmn-vad)          (含在30s内)  (含在30s内)
    ├─ 标点 (ct-punc)           (含在30s内)  (含在30s内)
    └─ 说话人 (cam++)           (含在30s内)  (含在30s内)
[3] 格式化为 transcript.json    < 0.1s      < 0.1s
──────────────────────────────────────────────────────
总计                           ~50s        ~30s
识别结果: "嗯，" + "对对。" (2 segments, 1 speaker)

* audio load 含 torchaudio 加载 + 重采样到 16kHz
  第 1 次 funasr 含 cam++ 模型依赖安装，第 2 次只有模型加载
```

### 7.3 处理流程真伪对比

| 你猜的流程 | 实际代码里的流程 |
|-----------|----------------|
| ① ASR 语音→文字 | ① funasr 一站式: `model.generate()` — 同时做 **ASR + VAD + 标点 + 说话人识别** |
| ② 说话人识别 | ❌ 没有单独步骤 — cam++ 说话人模型在 funasr 内部一起跑 |
| ③ 小模型改写 | ② `_run_asr_clean()` — **可选的** ollama `qwen3:8b` 后处理，不在主路径里 |

### 7.4 延迟根因分析

关键发现: **funasr AutoModel 没有被缓存为单例**。每次 `transcribe()` 调用都重新创建模型实例，加载 4 个模型（ASR+VAD+punc+spk）占据了 90%+ 的时间。真实 GPU 推理时间 < 0.5s/10s 音频（RTF ≈ 0.05）。

```
10s 音频在 RTX 3090 上的时间分配:
┌─────────────────────────────────────────────┐
│ 模型加载 (AutoModel init)        ~25-29s     │ ████████████████████████████
│ 实际推理 (model.generate)         ~0.3-0.5s  │ ▏
│ VAD 内部缓冲等待                   ~1-2s      │ ▏
│ 后处理格式化                       < 0.1s     │
└─────────────────────────────────────────────┘
```

### 7.5 客户端真实体验

```
P0 修复后 (2026-07-04) 实测:

客户端:
  T+0.0s   用户开始说话 (10s 测试音频)
  T+0.2s   上传完成
  T+5.8s   ASR 返回结果
           └─ 其中 ~35s 模型加载已在服务器启动时完成 (warmup_models)
           └─ 实际推理仅 ~0.5s

对比:
  修复前:  T+29.6s  首字
  修复后:  T+5.76s  首字 (含上传 + 网络往返)
  提升:    5.1x
```

### 7.6 优化方向

1. **缓存 funasr AutoModel 为进程级单例**: 避免每次转写都重新加载模型，预估可节省 25-29s
2. **减小 batch_size_s**: 当前 60s 批处理窗口可缩短至 15s
3. **VAD 超时参数**: `fsmn-vad` 的阈值调低，减少静音等待
4. **流式 ASR**: 考虑使用 SenseVoiceSmall 支持流式推理

详见 ISSUES.md (待补充为新的问题项)。

---

## 8. 关键文档索引

| 文档 | 链接 |
|------|------|
| README | [README.md](./README.md) |
| 总体架构 | [docs/design/总体架构.md](./docs/design/总体架构.md) |
| 产品需求 | [docs/product-spec/](./docs/product-spec/) |
| 决策记录(ADR) | [docs/decisions/](./docs/decisions/) |
| 安装指南 | [docs/部署/INSTALL.md](./docs/部署/INSTALL.md) |
| 模型切换 | [docs/部署/MODEL_SWAP.md](./docs/部署/MODEL_SWAP.md) |
| 踩坑记录 | [docs/部署/踩坑记录.md](./docs/部署/踩坑记录.md) |
| 用户手册 | [docs/用户手册.md](./docs/用户手册.md) |
