# 0025. agent 网络搜索 + KB 检索工具

- **状态**: 已接受
- **日期**: 2026-07-01
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (新增)
- **依赖**: [ADR-0019](./0019-RAG-选型-Chroma-嵌入式.md) (RAG 选型) · [ADR-0020](./0020-知识库-废弃旧库-文件上传-会议隔离.md) (KB API) · [ADR-0006](./0006-MVP-Step3-子session架构.md) (子 session 架构)

## 背景

2026-07-01 张胜东需求: "各个 agent 都是能进行网络搜索的吗？也是能主动调用知识库进行搜索的吗？"

**当前状态**:
- 6 doc agent + demo agent + chat agent 都跑 LLM, 走 `sub_session_controller.py` → `engine.py`, **没有工具调用** (纯生成式, 凭训练知识答)
- KB 检索只在 UI 端走 `/api/kb/search` (用户手动查), **agent 不能主动查**
- 没有网络搜索工具, agent 不知道"今天 AAPL 收盘价" / "2026 7 月最新行业报告"

**目标**: 6 doc agent + demo agent + chat agent 都能:
1. **网络搜索**: 公开 web 搜索 (不依赖商业 API key, 走 DuckDuckGo / Brave Search free tier / SearXNG self-host)
2. **KB 检索**: 调 `rag.query(where={"meeting_id": current})` 拉当前会议用户上传的材料

## 决策

### 1. 工具架构 — 统一 tool registry

**新建 `src/vpbuddy/agent_tools.py`**:

```python
class AgentTool(Protocol):
    name: str
    description: str
    schema: dict  # OpenAI function calling 格式
    
    async def execute(self, **kwargs) -> dict: ...


# 工具注册表 (单例, agent 启动时取)
TOOL_REGISTRY: dict[str, AgentTool] = {}


def register_tool(tool: AgentTool):
    TOOL_REGISTRY[tool.name] = tool


def get_tools_for_agent(agent_kind: str) -> list[dict]:
    """返回 OpenAI function calling 格式的工具列表"""
    if agent_kind == "doc":  # 6 doc agent
        return [web_search_tool.schema, kb_search_tool.schema, kb_upload_tool.schema]
    elif agent_kind == "demo":  # demo agent
        return [web_search_tool.schema, kb_search_tool.schema]
    elif agent_kind == "chat":  # chat agent
        return [web_search_tool.schema, kb_search_tool.schema]
    raise ValueError(f"unknown agent_kind: {agent_kind}")
```

### 2. web_search 工具

**实现**: `src/vpbuddy/tools/web_search.py`

```python
from duckduckgo_search import DDGS

class WebSearchTool:
    name = "web_search"
    description = "公开网络搜索, 返回前 N 条结果 (title, url, snippet)"
    schema = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web for information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                    "region": {"type": "string", "default": "zh-cn", "description": "wt-wt / us-en / zh-cn"},
                },
                "required": ["query"],
            },
        },
    }
    
    async def execute(self, query: str, max_results: int = 5, region: str = "zh-cn") -> dict:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region=region, max_results=max_results))
            return {
                "ok": True,
                "results": [{"title": r["title"], "url": r["href"], "snippet": r["body"]} for r in results],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
```

**依赖**: `duckduckgo-search>=6.0.0` (pip 装, 无 API key)

**回退**: 如果 DDG 失败 (rate limit / 网络), 返回空 + agent 提示"网络搜索暂不可用"

### 3. kb_search 工具

**实现**: `src/vpbuddy/tools/kb_search.py`

```python
from ..rag_backend import RAGBackend

class KBSearchTool:
    name = "kb_search"
    description = "检索当前会议用户上传的知识库资料"
    schema = {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": "Search the user's uploaded knowledge base for the current meeting",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                    "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
    }
    
    def __init__(self):
        self._rag = RAGBackend()  # chromadb persistent client
    
    async def execute(self, query: str, top_k: int = 5, meeting_id: str = None) -> dict:
        if not meeting_id:
            return {"ok": False, "error": "meeting_id required"}
        try:
            results = self._rag.query(
                query_text=query,
                top_k=top_k,
                where={"meeting_id": meeting_id},  # 强制会议隔离
            )
            return {
                "ok": True,
                "results": [
                    {"id": r["id"], "source": r["metadata"].get("source"),
                     "snippet": r["document"][:500], "distance": r["distance"]}
                    for r in results
                ],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
```

### 4. agent prompt 集成 (function calling 流程)

**改 `src/vpbuddy/sub_session_controller.py` (6 doc agent 调用处)**:

```python
async def run_doc_agent(meeting_id: str, kind: str, transcript: str):
    # 1. 准备工具
    tools = get_tools_for_agent("doc")
    
    # 2. 调 LLM (带 tools 参数)
    response = await llm.chat(
        model="qwen2.5:7b",
        messages=[
            {"role": "system", "content": DOC_AGENT_PROMPTS[kind]},
            {"role": "user", "content": f"会议 transcript: {transcript[:8000]}\n\n请生成 {kind} 文档。"},
        ],
        tools=tools,  # OpenAI 格式
    )
    
    # 3. 如果 LLM 调工具, 循环执行直到 finish
    while response.finish_reason == "tool_calls":
        for tool_call in response.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            tool = TOOL_REGISTRY[tool_name]
            
            if tool_name == "kb_search":
                tool_args["meeting_id"] = meeting_id  # 自动注入
            
            result = await tool.execute(**tool_args)
            
            # 把工具结果塞回 messages
            response = await llm.chat(
                model="qwen2.5:7b",
                messages=messages + [
                    response.message,
                    {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result, ensure_ascii=False)},
                ],
                tools=tools,
            )
    
    # 4. 写文件
    doc_content = response.content
    write_doc(meeting_id, kind, doc_content)
```

**注意**:
- qwen2.5:7b 支持 function calling (OpenAI 兼容)
- ollama 调 `chat` API 带 `tools` 字段即可 (v0.5+ 已支持)
- 工具结果走 standard `role: tool` 消息

### 5. 边界: 网络搜索 / KB 检索都是只读

- **网络搜索**: 只读, 不写, 不发 (符合 ADR-0009 总体架构 §AI 主动行为边界"展示范围仅限 VPBuddy 内部")
- **KB 检索**: 只读, agent 不主动写 KB (写 KB 走 ADR-0020 显式 `POST /api/kb/upload`)
- agent 不能主动外发邮件 / 调第三方 API / 触发会议投屏

### 6. 频率限制 (避免 agent 反复调工具刷屏)

- 单次 agent run: 最多 3 次工具调用 (LLM 觉得不够再加, 但限制总数)
- 单会议生命周期: web_search 最多 30 次 / 小时, kb_search 最多 100 次 / 小时 (rate limit by meeting_id)

实现: `src/vpbuddy/tool_rate_limit.py` (Redis-free, 内存 dict + TTL)

## 实施步骤

1. **装包**: `pip install duckduckgo-search` 加 `pyproject.toml`
2. **新建 `src/vpbuddy/agent_tools.py`**: TOOL_REGISTRY + register_tool + get_tools_for_agent
3. **新建 `src/vpbuddy/tools/__init__.py`**: 工具包
4. **新建 `src/vpbuddy/tools/web_search.py`**: WebSearchTool
5. **新建 `src/vpbuddy/tools/kb_search.py`**: KBSearchTool (调 RAGBackend)
6. **新建 `src/vpbuddy/tool_rate_limit.py`**: 频率限制 (内存 dict + TTL)
7. **改 `src/vpbuddy/sub_session_controller.py`**:
   - 6 doc agent 调 `run_doc_agent` 改: 带 tools, 循环 tool_calls
   - 注入 `meeting_id` 到 kb_search 调用
8. **改 demo agent / chat agent (推断位置)**: 同样支持 tools
9. **改 `src/vpbuddy/llm.py` (推断)**: 确认支持 OpenAI 格式 tools 参数
10. **测试**:
    - `tests/test_web_search_tool.py` — DDG 真实查询 (mock 也行, 优先 mock)
    - `tests/test_kb_search_tool.py` — 上传文件 → kb_search 拉回
    - `tests/test_tool_rate_limit.py` — 频率限制
    - `tests/test_agent_with_tools.py` — mock LLM 模拟调工具的循环
11. **同步更新**: `docs/design/总体架构.md` v1.21 加 "agent 工具" 章节, `docs/product-spec/VPBuddy_产品说明书.md` v2.0 加 "agent 能力" 章节

## 风险与回滚

| 风险 | 缓解 |
|------|------|
| DDG rate limit / IP 被封 (高频查询) | 频率限制 + 失败 fallback 空结果 (不报错, agent 走训练知识) |
| LLM 反复调工具 (死循环 / 浪费 token) | 单次 run 限制 3 次工具调用, 强制 finish |
| ollama + qwen2.5 function calling 不稳 (工具调用格式错误) | 加 retry (LLM 返错格式 → 把错信息喂回去让它重试, 最多 2 次) |
| KB 检索拉回的内容含敏感数据 (用户上传了客户合同) | KB 工具只对当前 meeting_id 过滤, 跨会议不查; agent 行为在 VPBuddy 内部展示, 不外发 |

## 关联

- ADR-0006 — 子 session 架构 (6 doc agent 入口)
- ADR-0009 — 部署架构 (总体, AI 主动行为边界)
- ADR-0019 — RAG 选型 (RAGBackend 是 KBSearchTool 后端)
- ADR-0020 — KB API (KBSearchTool 调 rag.query)
- `src/vpbuddy/agent_tools.py` (新建)
- `src/vpbuddy/tools/web_search.py` (新建)
- `src/vpbuddy/tools/kb_search.py` (新建)
- `src/vpbuddy/tool_rate_limit.py` (新建)
- `src/vpbuddy/sub_session_controller.py` (改, function calling 循环)
- `pyproject.toml` (加 `duckduckgo-search`)
