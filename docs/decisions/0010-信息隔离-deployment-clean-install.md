# ADR-0010:信息隔离 — Deployment Clean Install

**日期**:2026-06-22
**状态**:✅ Accepted
**作者**:张胜东 + Hermes
**关联**:ADR-0009 部署架构 / [踩坑记录 §20 信息隔离](../部署/踩坑记录.md#20)

---

## Context

VPBuddy 部署链路(`ADR-0009`)**铁律:VPBuddy 必须运行在 Hermes Agent 之上**(ADR-0009 §0.3 不变量 + ADR-0001 §6 决策 1)。

- 一次会议 = 一个 Hermes session (`meeting:{mid}`)  
- 6 种子文档 = 6 个子 session (`meeting:{mid}:{kind}`)  
- 真并发 = `ThreadPoolExecutor(3)` + in-process `from run_agent import AIAgent` (ADR-0009 §决策 选项 C)  
- LLM API key = 由 `~/.hermes/.env` 通过 env var 注入,**VPBuddy 不自己调 LLM HTTP**

`ADR-0009` 定义两个角色:

| 角色 | 机器 | 跑什么 | 谁配 API key |
|---|---|---|---|
| **A** GPU 服务器 | 192.168.10.63 (zsd) | vpbuddy controller + 6 文档生成 + KB(GPU 加速 ASR + 说话人分离) | 张胜东(我们) |
| **B** VP 桌面客户端 | VP 自带 Mac/笔记本 | vpbuddy ui + 音频采集 + Hermes 进程内 LLM | VP(每个 VP 自己) |

**问题**:之前的开发模式是"开发机 `~/.hermes/` 全量 scp 到 GPU 服务器"。这导致:

1. **真实 API key 跨机传输** — 任何中间节点(NAS、日志、备份)都可能截获
2. **开发机的 MEMORY/USER 偏好泄露** — VP 客户端不应该看到"张腾予/李丹"这类私人信息
3. **VP 客户端之间不隔离** — A VP 看到 B VP 的 settings
4. **install 脚本含真实 key 风险** — 一旦脚本被推到 GitHub 就是 commit history 泄露

具体泄露事件:
- 2026-06-22 22:34,张胜东发现:`192.168.10.63:~/.hermes/.env` 包含本机 `MINIMAX_CN_API_KEY`/`OPENROUTER_API_KEY`/`XIAOMI_API_KEY`/`FEISHU_APP_SECRET`/`HASS_TOKEN`/`WEIXIN_TOKEN`,全部来自我之前的 scp 同步。

### 关键澄清(2026-06-22 22:48 由张胜东纠错)

Hermes 在 GPU 端**已经装好**(pip install hermes-agent 0.16.0,/home/zsd/hermes-agent-src),`from run_agent import AIAgent` 真 import 成功,VPBuddy controller 已经在 in-process 调 AIAgent(只 subprocess fallback 到 `hermes chat`)。我(hermes)之前的描述"GPU 服务器根本没装 hermes-agent"是**错误**的——`pip list | grep hermes-agent` 显示 0.16.0 已在 vpbuddy-gpu conda env 里。问题从来不是"要不要装 hermes",而是"`~/.hermes/.env` 的真 key 怎么管理"。

## Decision

**零信任部署原则**:每个角色机器的 `~/.hermes/` 必须从**干净模板**起,真实 API key **只能**由用户在机器上**手动 vim 填**。

### 三条铁律

```
1. config.yaml / .env 都用占位符,真实 key 由用户手动 vim 填
2. install 脚本绝不包含真实 API key
3. install 脚本绝不覆盖用户已存在的 ~/.hermes/config.yaml 或 .env
```

### 模板规范

**`~/.hermes/config.yaml`** (干净版):
- `api_key: ${ENV_VAR_NAME}` — 引用环境变量
- 包含完整 provider 配置(mini_max / openrouter),base_url 公开
- **不包含**任何真实 key

**`~/.hermes/.env`** (干净版):
- `MINIMAX_CN_API_KEY=YOUR_M...n` — 占位符
- `OPENROUTER_API_KEY=YOUR_O...n` — 占位符
- 其他集成(飞书/HASS/微信)全部 `# 注释` 状态
- **不包含**任何真实 key

### install 脚本守卫

```bash
# 不覆盖已存在
if [[ ! -f "$HOME/.hermes/config.yaml" ]]; then
    # 创建干净模板
fi

if [[ ! -f "$HOME/.hermes/.env" ]]; then
    # 创建干净模板
fi

# 已存在则跳过
echo "✅ ~/.hermes/.env 已存在(不动用户填好的 key)"
```

### 用户填 key 流程

```bash
ssh zsd@192.168.10.63  # 或 VP 在自己机器上
vim ~/.hermes/.env
# 把 MINIMAX_CN_API_KEY=YOUR_M...n 替换成真 key
chmod 600 ~/.hermes/.env  # 双保险

# 验证
hermes chat "ping"  # 应该正常回话
```

## Consequences

### ✅ 好处

1. **每台机器独立** — 开发机、GPU 服务器、VP 客户端互不干扰
2. **泄露半径最小** — 即使一台机器的 `~/.hermes/` 泄漏,不会牵连其他
3. **VP 隐私** — VP A 看不到 VP B 的 settings/HASS token
4. **install 脚本可以安全推 GitHub** — 不含真实 key,任何 commit history 安全
5. **备份清晰** — `~/.hermes_clean_backup_TIMESTAMP/` 目录明确,不混淆

### ⚠️ 代价

1. **首次部署多一步** — 用户必须手动 vim 填 key(2 分钟)
2. **每台新机器都要填** — 不能"复制粘贴一个开发机的"
3. **CI/CD 复杂** — 自动化测试不能简单 scp 一个 .env,要 mock 或用 CI secret

## 实施 (2026-06-22 22:30 完成)

### 已做的清理

| 操作 | 状态 |
|---|---|
| 备份 GPU 端旧 `~/.hermes/` → 本机 `~/.hermes_backups/gpu_192.168.10.63_20260622_223413/` | ✅ |
| 备份 GPU 端旧 `~/.hermes/` → GPU 端 `~/.hermes_clean_backup_20260622_223413/` | ✅ |
| 删除 GPU 端旧 `~/.hermes/config.yaml` + `.env` | ✅ |
| GPU 端重建干净 `config.yaml` (1595B, 仅占位符) | ✅ |
| GPU 端重建干净 `.env` (1062B, 仅占位符) | ✅ |
| 权限 600 | ✅ |
| `scripts/install-gpu-server.sh` 加信息隔离铁律 + 守卫 | ✅ |
| `scripts/install-client.sh` 加信息隔离铁律 + 守卫 | ✅ |

### 还要做的(等用户手动)

- 用户 ssh 到 GPU 端,手动 `vim ~/.hermes/.env` 填真 key
- 验证:`hermes chat "ping"` 或 `vpbuddy controller --dry-run`

## 验证命令

```bash
# 1. 确认 install 脚本不含真实 key
grep -E "MINIMAX_CN_API_KEY=|OPENROUTER_API_KEY=" scripts/install-*.sh
# 应该全部是占位符 YOUR_M...n / YOUR_O...n

# 2. 确认 GPU 端 .env 是占位符
ssh zsd@192.168.10.63 "grep -v '^#' ~/.hermes/.env | grep -v '^$' | head"
# 应该看到 MINIMAX_CN_API_KEY=YOUR_M...n

# 3. 确认 600 权限
ssh zsd@192.168.10.63 "ls -la ~/.hermes/.env"
# 应该 -rw------- zsd zsd

# 4. 跑 install 脚本 dry-run 不破坏现有 .env
bash scripts/install-gpu-server.sh --dry-run
# 应该报 "✅ ~/.hermes/.env 已存在(不动用户填好的 key)"
```

## 关联文档

- [ADR-0009 部署架构 = Hermes runtime](./0009-部署架构-Hermes-runtime.md) — 角色 A/B 定义
- [踩坑记录 §20 信息隔离](../部署/踩坑记录.md#20) — 本次事件完整复盘
- [INSTALL.md §安全说明](../部署/INSTALL.md#安全信息隔离) — 用户视角的填 key 步骤
- [scripts/install-gpu-server.sh](../../scripts/install-gpu-server.sh) — 守卫逻辑
- [scripts/install-client.sh](../../scripts/install-client.sh) — 守卫逻辑
