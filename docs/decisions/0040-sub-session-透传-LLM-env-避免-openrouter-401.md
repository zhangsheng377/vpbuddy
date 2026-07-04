# ADR-0040 — sub_session_controller 透传 LLM env 给 AIAgent (避免 openrouter 401)

**状态**: **已接受** (2026-07-04)
**日期**: 2026-07-04
**作者**: Hermes (起草) / 张胜东 (决策: 同意)
**替代**: 无
**依赖**: ADR-0009 (Hermes 运行时) / ADR-0029 (6 sub-session 合并 2 batch) / ADR-0038 (公网 GPU 服务器部署)

## Context

2026-07-04 张胜东在 GPU 服务器 (47.100.182.3) 上跑 vpbuddy e2e, 上传 30s 中文需求语音, 期待 6 文档生成。结果:
- ASR 真转写成功 (funasr paraformer-zh, 14 segments, 1 speaker, ~30s)
- State 真保存 (1 risks 抽取)
- **6 文档全部 status=pending, 永不生成**

根因排查 (按铁律 1 真命令验证):
1. GPU 上 `/data/vpbuddy/venv/lib/python3.11/site-packages/run_agent.py` 是 29 行 stub, 调 OpenAI API 直连 MiniMax endpoint — 我之前为了快速测试装上的
2. 张胜东指出: "你应该在 GPU 服务器上安装完整的 hermes" — **正确**, stub 不是 hermes-agent
3. 尝试 1: `pip install hermes-agent==0.18.0` 失败 (GPU setuptools 59.6.0 太旧, pyproject 要求 `setuptools>=77`)
4. 尝试 2: scp dev `/home/zsd/.hermes/hermes-agent/` 完整目录到 GPU `/opt/hermes-agent/`, `pip install -e .` — 成功 (setuptools 升级到 82.0.1)
5. 尝试 3: 张胜东又指出: "既然升级了 setuptools, 为什么不用 pip install hermes-agent? 官方不是 curl install.sh 吗?" — 查 PyPI, hermes-agent **0.18.0 已经在 PyPI** (`hermes_agent-0.18.0-py3-none-any.whl`, 9.25MB)
6. 卸载 editable, `pip install --force-reinstall hermes-agent==0.18.0` from PyPI — 成功
7. `from run_agent import AIAgent` ✓, 显式传 `base_url='https://api.minimax.chat/v1'` + `api_key=sk-...` → LLM 调用成功 (3.69s 返回 "我是 Hermes 助手")

8. **新问题**: vpbuddy `sub_session_controller.py` 调用 `_AIAgent(...)` 时**没传 base_url + api_key**, hermes 内部默认走 `~/.hermes/auth.json` 的 openrouter credential pool → MiniMax-M3 被路由到 openrouter, 但 openrouter **不认识** 我们的 MiniMax API key → HTTP 401 "User not found"
9. 修改 `sub_session_controller.py`: 显式把 `OPENAI_BASE_URL` + `OPENAI_API_KEY` (或 `MINIMAX_API_KEY`) env vars 透传给 `_AIAgent(base_url=..., api_key=...)`, 强制走 MiniMax 直连 endpoint

## Decision

### 1. GPU 上装 hermes-agent 用 PyPI wheel (不再 editable install)

```bash
# GPU 上: 升级 setuptools → 装 PyPI wheel
/data/vpbuddy/venv/bin/pip install --upgrade 'setuptools>=77,<83'   # 59.6.0 → 82.0.1
/data/vpbuddy/venv/bin/pip uninstall -y hermes-agent 2>/dev/null     # 卸 editable
/data/vpbuddy/venv/bin/pip install --force-reinstall --no-deps hermes-agent==0.18.0
```

不推荐 editable install 路径 (`/opt/hermes-agent/`), 因为:
- editable .pth 文件仍指向 `/opt/hermes-agent/`, 路径一旦被 mv/rm, hermes-agent 整体 broken
- PyPI wheel 是 flat layout, `runtime_provider.py` 等顶层文件就在 `site-packages/` 顶层, 无 namespace 冲突

### 2. 启动 GPU server 时设 env vars

```bash
# /tmp/_vpbuddy_env
OPENAI_BASE_URL="https://api.minimax.chat/v1"
OPENAI_API_KEY="sk-cp-..."     # MiniMax LLM API key
MINIMAX_API_KEY="sk-cp-..."    # 别名, hermes 也读
HERMES_API_KEY="sk-cp-..."     # 别名
VPBUDDY_LLM_MODEL="MiniMax-M3"
VPBUDDY_FALLBACK=1
VPBUDDY_DATA_DIR="/data/vpbuddy/server/data/meetings"  # ⚠️ GPU server 默认路径
```

**重要**: GPU 上 `storage.py` 默认 `data_dir = PROJECT_ROOT / "data" / "meetings"` = `/data/vpbuddy/server/data/meetings` (跟 dev 上 `/home/zsd/vpbuddy/data/meetings` 不同! 这是为什么 sub_session_controller 找不到 state.json 的根因)

启动 server:
```bash
set -a; . /tmp/_vpbuddy_env; set +a
export VPBUDDY_DATA_DIR=/data/vpbuddy/server/data/meetings
cd /data/vpbuddy/server
nohup /data/vpbuddy/venv/bin/python -m vpbuddy.ui_server --port 8765 > /tmp/vpbuddy_server.log 2>&1 &
```

### 3. vpbuddy code 修改 — `sub_session_controller.py` `_get_or_create_agent`

```python
# Before (broken — hermes 默认 openrouter)
_AGENT_CACHE[sid] = _AIAgent(
    session_id=sid,
    enabled_toolsets=toolsets,
    platform="subagent",
    quiet_mode=True,
    max_iterations=30,
    model=os.environ.get("VPBUDDY_LLM_MODEL", "MiniMax-M3"),
    ephemeral_system_prompt="...",
)

# After (fixed — 透传 MiniMax endpoint)
_AGENT_CACHE[sid] = _AIAgent(
    session_id=sid,
    enabled_toolsets=toolsets,
    platform="subagent",
    quiet_mode=True,
    max_iterations=30,
    model=os.environ.get("VPBUDDY_LLM_MODEL", "MiniMax-M3"),
    base_url=os.environ.get("OPENAI_BASE_URL"),  # 直连 MiniMax endpoint
    api_key=os.environ.get("OPENAI_API_KEY") or os.environ.get("MINIMAX_API_KEY"),
    ephemeral_system_prompt="...",
)
```

## Consequences

### 正面

- 6 doc (req/arch/tasks/api/risk/demo) 真的能由 hermes LLM 生成 (e2e 测试 `UPLOAD_20260704_161045_767e728d` 已经验证 batch_docs LLM 6 次调用成功, risk.md 460 bytes 中文风险评估真生成)
- 端到端 latency 链路打通: TTS → upload → ASR (~30s) → 6 doc (~2 min 含 LLM 多次迭代)
- 显式传 base_url + api_key 优先级最高, 不受 hermes 默认 credential pool 干扰

### 负面

- vpbuddy code 现在 hard-code 假设 hermes 支持 `base_url` + `api_key` 参数 (hermes 0.18.0+ 才支持)
- 启动 GPU server 必须设 4 个 env vars (`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MINIMAX_API_KEY` / `HERMES_API_KEY`), 否则 fallback openrouter → 401
- demo 任务的 LLM 迭代较慢 (180s 内只跑完 9 步, 可能 timeout), 后续需要调高 demo 的 max_iterations timeout 或者简化 demo prompt

### 后续 (TODO)

- 简化 demo prompt, 让 LLM 在 30s 内完成 (而不是 180s)
- ADR-0029 batch_docs (5 doc) 的 controller `trigger_sub_session` timeout 改成 600s (现 180s 太短)
- vpbuddy 启动脚本 (systemd / pm2 / supervisor) 应该自动注入 env vars, 不依赖人工 source /tmp/_vpbuddy_env
- ADR-0039 默认 GPU URL 切公网 47.100.182.3 应该把 env vars 配到 systemd EnvironmentFile

## 验证

```bash
# GPU 上验证 AIAgent 真用 MiniMax
python -c "
from run_agent import AIAgent
import os
agent = AIAgent(
    session_id='test',
    base_url=os.environ.get('OPENAI_BASE_URL'),
    api_key=os.environ.get('OPENAI_API_KEY'),
    model='MiniMax-M3',
    enabled_toolsets=[],
    quiet_mode=True,
)
print('agent.base_url:', agent.base_url)
print('agent.api_key[:30]:', agent.api_key[:30])
r = agent.chat('用 10 字以内回答: 你是谁')
print(r[:200])
"
# 应输出: agent.base_url: https://api.minimax.chat/v1
#         agent.api_key[:30]: sk-cp-9kYBvYNkjlwpOA3TMa41-RQs
#         我是 Hermes 助手
```

## 参考

- hermes-agent 0.18.0 PyPI: https://pypi.org/project/hermes-agent/
- MiniMax API: https://api.minimax.chat/v1 (OpenAI 兼容)
- 张胜东纠正 1: "你应该在 gpu 服务器上安装 hermes"
- 张胜东纠正 2: "你都升级 setuptools 了, 那为什么不直接 pip install hermes-agent? 官方不是 curl install.sh | bash 吗?"