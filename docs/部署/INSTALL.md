# VPBuddy 安装指南

> **v1.0 (2026-06-22 ADR-0009 落地)**: 3 个角色,3 个一键脚本,从 0 到跑通端到端。
>
> **核心原则**:VPBuddy 不自研 LLM 框架,跑在 Hermes Agent 之上 (ADR-0009 §0.3 不变量)。**先装 hermes-agent,再装 vpbuddy**。

---

## 目录

- [角色速查表](#角色速查表)
- [角色 A — 生产 GPU 服务器](#角色-a--生产-gpu-服务器)
- [角色 B — VP 桌面客户端](#角色-b--vp-桌面客户端)
- [角色 C — 开发者](#角色-c--开发者)
- [故障排查](#故障排查)
- [关联文档](#关联文档)

---

## 角色速查表

| 角色 | 谁 | 跑什么 | 脚本 | 耗时 | GPU |
|---|---|---|---|---|---|
| **A. 生产 GPU 服务器** | 运维 / 部署工程师 | 后台 controller + 7×24 + 转写 | `install-gpu-server.sh` | 5-15 min | ✅ NVIDIA |
| **B. VP 桌面客户端** | VP / 会议参与者 | `vpbuddy ui` (投屏) + 音频采集 | `install-client.sh` | 2-5 min | ❌ 用云 LLM |
| **C. 开发者** | 程序员 | 改代码 + 跑测试 | `install-dev.sh` | 3-5 min | ❌ / ✅ 都行 |

---

## 角色 A — 生产 GPU 服务器

### 一键安装

```bash
sudo bash scripts/install-gpu-server.sh
```

**会做的事**:

| 步骤 | 内容 | 预计耗时 |
|---|---|---|
| 0 | 装系统包(ffmpeg / portaudio / build-essential) | 30s |
| 1 | 检查 NVIDIA driver,缺则自动装 `nvidia-driver-535` | 1-2 min(已装则 5s) |
| 2 | 装 Miniconda 到 `~/miniconda3` | 30s(已装则跳过) |
| 3 | 创建 conda env `vpbuddy-gpu` (Python 3.11) | 10s |
| 4 | `pip install hermes-agent` + `pip install -e ".[gpu,audio]"` | 1-2 min |
| 5 | 写 `~/.hermes/config.yaml` + `.env` 模板 | 1s |
| 6 | 下 pyannote + faster-whisper + sentence-transformers 模型 | 3-5 min(国内 ModelScope/HF mirror) |

### 验证(5 个命令)

```bash
# 1. vpbuddy CLI 可用
vpbuddy version
# 期望:vpbuddy 0.2.0

# 2. hermes 可用
hermes --version
# 期望:0.16.x

# 3. LLM API 通
hermes chat "用一句话介绍你自己"
# 期望:正常返回(~3-5s)

# 4. 跑测试
conda activate vpbuddy-gpu
PYTHONPATH=/home/zsd/vpbuddy/src python3 -m pytest /home/zsd/vpbuddy/src/tests/ -v
# 期望:78 passed + 2 new (sub_session) = 80 passed in ~45s

# 5. 启动 UI
vpbuddy ui --port 8765 &
sleep 2
curl -s http://localhost:8765/ | head -5
# 期望:HTML shell + "VPBuddy" 标题
```

### 跳过模型(--no-models)

如果只想装代码,不要模型(后续手动 `vpbuddy setup-gpu`):

```bash
sudo bash scripts/install-gpu-server.sh --no-models
```

### 故障:重启 NVIDIA driver

如果脚本第 1 步装完 driver 后让你重启:

```bash
sudo reboot
# 重启后再跑一次:
sudo bash scripts/install-gpu-server.sh
```

---

## 角色 B — VP 桌面客户端

### 一键安装

```bash
bash scripts/install-client.sh
```

**会做的事**:

| 步骤 | 内容 | 预计耗时 |
|---|---|---|
| 1 | 装系统包(macOS: brew / Linux: apt) | 30s |
| 2 | 创建 venv `~/.vpbuddy-venv` | 5s |
| 3 | `pip install hermes-agent` + `pip install -e ".[audio]"` | 1 min |
| 4 | 装 KB 依赖:`sqlite-vec` + `sentence-transformers` | 30s |
| 5 | 预下载 KB embedding 模型 (256MB,首次启动要) | 30s-2min |
| 6 | 写 `~/.hermes/config.yaml` + `.env` 模板 | 1s |

### 验证(5 个命令)

```bash
# 1. 激活 venv
source ~/.vpbuddy-venv/bin/activate

# 2. vpbuddy CLI
vpbuddy version
# 期望:vpbuddy 0.2.0

# 3. KB 模型就绪(检查 ~/.cache/huggingface/hub)
ls ~/.cache/huggingface/hub/ | grep paraphrase-multilingual
# 期望:看到模型目录

# 4. LLM API 通
hermes chat "你好"
# 期望:正常返回

# 5. 启动 UI
vpbuddy ui --port 8765 &
sleep 2
open http://localhost:8765  # macOS
# 浏览器看到 VPBuddy 4 窗口 shell
```

### 关键:VP 桌面客户端独立运营(2026-06-22)

> ⚠️ **VPBuddy 真正运行在 VP 自己机器上(被部署端),不在我们 zsd / GPU 服务器上**。
> 我们 zsd/GPU 是**开发+测试环境**,跑仿真测试,不代表真运行时。
> 所以 `install-client.sh` 必须装完整 hermes-agent + AIAgent + KB + VPBuddy 客户端。
> VP 机器不需要连我们的任何服务,自己跑自己。

### macOS 注意

- 需要 Homebrew: <https://brew.sh>
- 装 Xcode Command Line Tools: `xcode-select --install`
- 第一次跑 `vpbuddy ui` 可能要授权麦克风权限

### Linux 桌面

需要 PulseAudio / PipeWire 给音频采集用,默认都装好了。

---

## 角色 C — 开发者

### 一键配置

```bash
bash scripts/install-dev.sh
```

**会做的事**:

| 步骤 | 内容 | 预计耗时 |
|---|---|---|
| 1 | 检测 conda(没有就报错) | 1s |
| 2 | `git clone https://github.com/zhangsheng377/vpbuddy.git` | 10s(已 clone 跳过) |
| 3 | 创建 conda env `vpbuddy-dev` (Python 3.11) | 10s |
| 4 | `pip install -e ".[dev,gpu]"` (editable + 测试/lint/GPU) | 2 min |
| 5 | 跑 pytest 验证(没 GPU 65 passed,有 GPU 80 passed) | 30-60s |

### 关键铁律(2026-06-22)

> ⚠️ **绝不**在系统 Python / Mac 默认 venv 里 `pip install -e .` — 会污染其他项目的依赖。
> **所有开发** = 单独的 conda env(`vpbuddy-dev`),editable install 在那里。

### 日常用法

```bash
# 1. 激活 conda env
conda activate vpbuddy-dev
cd ~/vpbuddy

# 2. 改代码...

# 3. 跑测试
PYTHONPATH=src python3 -m pytest src/tests/ -v

# 4. 跑 controller(dry-run 不调 LLM)
PYTHONPATH=src python3 -m vpbuddy.sub_session_controller --once --dry-run

# 5. 启动 UI
vpbuddy ui --port 8765
```

### 推代码

```bash
git add -A
git commit -m "feat: ..."
git push origin main
```

### GPU 服务器同步(2026-06-22 起推荐)

不再在本机 Mac commit + push 后让 GPU 服务器 pull。**直接 rsync 到 GPU 服务器 commit + push**:

```bash
# 从 GPU 服务器主动 rsync 本机代码
ssh zsd@192.168.10.63 "rsync -avz --exclude='.git' --exclude='venv' --exclude='__pycache__' /home/zsd/vpbuddy/ /home/zsd/vpbuddy-src/"

# 在 GPU 服务器上
ssh zsd@192.168.10.63
cd ~/vpbuddy
source ~/miniconda3/etc/profile.d/conda.sh
conda activate vpbuddy-gpu
pip install -e ~/vpbuddy-src
git add -A && git commit -m "feat: ..." && git push origin main
```

---

## 故障排查

### `vpbuddy: command not found`

`vpbuddy` CLI 来自 pip 安装(`pip install vpbuddy` 或 `pip install -e .`)。检查:

```bash
which vpbuddy
pip show vpbuddy
```

如果 `which` 找不到,需要:

```bash
# 检查 pip 装在哪
python3 -m pip show vpbuddy

# 检查 PATH
echo $PATH

# 装但没在 PATH → 重装(激活正确的 venv / conda env)
pip install -e /path/to/vpbuddy
```

### `hermes: command not found`

`hermes` CLI 在装 hermes-agent 后应该可用:

```bash
pip install hermes-agent
which hermes
hermes --version
```

如果还是找不到,检查:

```bash
python3 -m hermes_cli --version  # 应该能跑
# 说明 hermes 模块在,只是没生成 entry point,可能要:
pip install --force-reinstall hermes-agent
```

### `pytest` 卡死(卡 5+ 分钟无输出)

大概率是 sentence-transformers / huggingface_hub 在等网络:

```bash
# 设离线 + 看是不是 cache 没下完
HF_HUB_OFFLINE=1 python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"
```

**修复**:见 [§踩坑 §19 GPU pytest 卡 53 分钟](./踩坑记录.md#19)。**`src/tests/conftest.py` 顶部应已默认 `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`**。

### `vpbuddy ui` 启动后浏览器空白

检查:

```bash
# 1. 进程在跑?
ps aux | grep vpbuddy | grep -v grep

# 2. 端口通?
curl -v http://localhost:8765/

# 3. UI 文件存在?
ls -la /home/zsd/vpbuddy/ui/
# 应该有 index.html
```

### GPU 模型找不到

```bash
# 1. 检查模型目录
ls -la /home/USER/pyannote_models/

# 2. 检查环境变量
echo $PYANNOTE_LOCAL_DIR

# 3. 重下
vpbuddy setup-gpu
```

### LLM API 不通(401 / 403 / timeout)

```bash
# 1. 检查 .env
cat ~/.hermes/.env
# 应该有 MINIMAX_API_KEY=xxx 或 OPENROUTER_API_KEY=xxx

# 2. 直接测 API
curl -s -H "Authorization: Bearer $MINIMAX_API_KEY" https://api.minimaxi.com/v1/models

# 3. hermes 直连测
hermes chat "hi"
```

### Mac 麦克风权限被拒

```bash
# macOS 系统偏好设置 → 安全性与隐私 → 麦克风 → 勾选 Terminal / iTerm
# 重启 Terminal 后再跑 vpbuddy ui
```

---

## 关联文档

- [ADR-0009 部署架构 = Hermes runtime](../decisions/0009-部署架构-Hermes-runtime.md) — 为什么必须先装 Hermes
- [gpu 服务器部署(原始)](./gpu服务器部署.md) — 老版部署(参考)
- [踩坑记录](./踩坑记录.md) — §19 GPU pytest 卡 53min 等
- [Hermes 官方文档](https://hermes-agent.nousresearch.com/docs) — `hermes chat` / `hermes setup` / `hermes skills` 命令参考
- [README.md](../../README.md) — 项目总览