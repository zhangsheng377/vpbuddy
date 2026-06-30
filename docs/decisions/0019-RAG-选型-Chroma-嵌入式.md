# 0019. RAG 框架选型 — Chroma 嵌入式 + sentence-transformers

- **状态**: 已接受
- **日期**: 2026-07-01
- **作者**: 张胜东 (起草: Hermes)
- **替代**: [ADR-0015](./0015-RAG-sqlite-vec本地知识库.md) (superseded)
- **依赖**: 无

## 背景

旧 KB 实现 (`src/vpbuddy/knowledge_base.py`, ADR-0015) 有 3 个问题:

1. **6 docs 自动入库无意义**: 6 sub-session 写完的 doc 是"内部中间产物", 6 个 doc 加起来约 6KB/meeting, 量小且全是重复句式, 跨会议检索的"命中率"实际是 6×N docs 之间互查, 不是用户真实想要的"上次会议定的X"
2. **手写 sqlite-vec + sentence-transformers 解耦不彻底**: 切分逻辑 (chunking)、embedding 选型、metadata schema、持久化都自己管, 以后换 RAG 系统要重写
3. **会议隔离缺失**: `kb.search()` 默认不传 `meeting_id` 过滤, UI 查"所有会议"是默认, 没按会议分桶

2026-07-01 张胜东拍板: **知识库改为"用户主动维护的资产"** (上传文件 → 入库), **KB 内容来源 = 用户上传** (不再自动 ingest 6 docs), **KB 检索需要会议隔离** (默认仅当前会议), **切分逻辑跟项目解耦** (交给成熟 RAG 框架)。

所以这次选型**不是单纯换 vector DB**, 是把"切分 + embedding + 存储 + 检索"整个 pipeline 换成成熟方案, 以后想升级只换框架, 不动业务代码。

## 决策

**RAG = Chroma 嵌入式 (in-process) + sentence-transformers (默认 embedding function)**, 全部本地, 单文件, pip install 一步。

### 候选评估

| 方案 | 安装 | 模式 | 体积 (估) | 中文 | 解耦 | 评分 |
|------|------|------|----------|------|------|------|
| **Chroma 嵌入式** (选) | `pip install chromadb` | in-process (同 Python 进程) | ~80MB (含 pydantic + onnxruntime + hnswlib + chroma-hnswlib) | ✓ 换 `paraphrase-multilingual-MiniLM-L12-v2` 即支持 | ✓ 完整 RAG 抽象 (add/query/update/delete), 切分走 framework 默认 | ⭐⭐⭐⭐⭐ |
| LanceDB 嵌入式 | `pip install lancedb` | in-process | ~50MB | ✓ 同样换 embedding | △ 列存好, 但 RAG 抽象薄, 切分要自己接 | ⭐⭐⭐ |
| Qdrant client + server | `pip install qdrant-client` + `docker run qdrant/qdrant` | 独立服务 (HTTP/REST) | 服务镜像 ~200MB | ✓ | ✗ 违反"单文件零运维"原则 | ⭐⭐ |
| llama-index + 任意后端 | `pip install llama-index` (~150MB) | in-process | ~150MB (含核心 + 默认 embedding) | ✓ | ✓ 完整 RAG 抽象 (比 Chroma 更全), 但体积大, 默认带 OpenAI | ⭐⭐⭐ |
| 维持手写 sqlite-vec | (无需) | in-process | 0 (已写好) | ✓ | ✗ 切分 + 持久化都自管, 升级要重写 | ⭐ |

### 为什么选 Chroma

1. **零运维 = VPBuddy 哲学一致**: ADR-0009 决定"全本地单文件", Chroma 嵌入式就是同 Python 进程, **没有 docker / service / 端口**, 跟 sqlite 一个级别
2. **`pip install chromadb` 一步到位**: 跟旧 `pip install sqlite-vec` 一样简单, 不破坏现有部署
3. **embedding 维度兼容旧 KB (384 维)**: Chroma 默认 `all-MiniLM-L6-v2` = 384 维, VPBuddy 旧 KB 用 `paraphrase-multilingual-MiniLM-L12-v2` = 384 维, **无缝切** (换 model 名, 不用改 schema)
4. **切分逻辑解耦**: Chroma 提供 `documents=["text1", "text2"]` 直接传字符串, 框架内部默认按字符切 (SentenceTransformerEmbeddingFunction 不做切分, VPBuddy 上传文本就是已切好的), 后续想接 markdown/pdf 切分, 加个 `from chromadb.utils import embedding_functions` + 自定义 splitter 即可, 业务代码不动
5. **持久化默认 SQLite**: 旧 `data/knowledge.db` 直接替换为 Chroma 自动创建的 `data/chroma/`, 单文件夹, 备份 = `tar czf`
6. **未来切换成本低**: VPBuddy → Chroma 抽象层只有 `kb.add()` / `kb.search()` / `kb.delete()` 3 个方法, 以后想换 LanceDB / Qdrant 只需重写 1 个文件 `src/vpbuddy/rag_backend.py`

### 拒绝的方案

#### 拒绝: 维持手写 sqlite-vec
- 切分逻辑跟项目耦合, 升级要重写 chunking + embedding + 持久化
- 用户明确要"解耦切分", 自写方案违反这条

#### 拒绝: Qdrant / Milvus / Weaviate (standalone server)
- 跟 ADR-0009 "全本地单进程" 矛盾
- 引入 Docker / 独立服务 / 端口, 增加部署复杂度
- VPBuddy 知识库规模 (用户上传 N 个文件) 远不到 standalone server 的 scale 阈值

#### 拒绝: LanceDB
- 嵌入式 + 列存, 性能好
- 但 RAG 抽象薄, 切分要自己接 (跟"解耦"目标弱)
- Chroma 抽象更全, 业务代码更短

#### 拒绝: llama-index (默认 + 任意后端)
- 完整 RAG 抽象 (跟 Chroma 一样), 含 query engine / agents
- 但默认带 OpenAI 依赖 (~150MB), 需要瘦身装
- Chroma 已经够用, 不需要 llama-index 那些高级特性 (VPBuddy 知识库只是 "add + search", 不是 agentic RAG)

## 关键设计

### 抽象层 `src/vpbuddy/rag_backend.py`

```python
class RAGBackend(Protocol):
    def add(self, ids: list[str], documents: list[str], metadatas: list[dict]) -> None: ...
    def query(self, query_text: str, top_k: int = 5, where: dict | None = None) -> list[dict]: ...
    def delete(self, ids: list[str]) -> None: ...
    def count(self, where: dict | None = None) -> int: ...
```

实现 1: `ChromaRAG` (本期, 选) — 直接 wrap chromadb PersistentClient
实现 2 (未来): `LanceDBRAG` / `QdrantRAG` / `MockRAG` (测试用) — 同样 3 个方法

VPBuddy 业务代码 (sub_session_controller / ui_server / agent 工具) 只调 `RAGBackend` 接口, 不直接 import chromadb。

### Chroma 配置 (一期, ADR-0020 实现细节)

```python
import chromadb
from chromadb.utils import embedding_functions

client = chromadb.PersistentClient(path=str(DATA_DIR / "chroma"))
collection = client.get_or_create_collection(
    name="vpbuddy_kb",
    embedding_function=embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2",  # 跟旧 KB 一致, 384 维
        device="cpu",
    ),
    metadata={"hnsw:space": "cosine"},
)
```

### Metadata schema (每条 doc)

```python
{
    "meeting_id": "PHASE3_MEETING_REAL",   # 必填, 会议隔离用
    "source": "upload:customer_intro.pdf",  # 来源 (upload:<filename> / paste / future agent)
    "uploaded_at": "2026-07-01T...",        # ISO 8601
    "chunk_index": 0,                       # 同一文件内第几段
}
```

`meeting_id` 必填, **默认 query 加 `where={"meeting_id": current_meeting}` 过滤**。

### API 层 `src/vpbuddy/ui_server.py` 改动

| 端点 | 行为 |
|------|------|
| `POST /api/kb/upload` | multipart: meeting_id + file → RAG.add() → 返回 doc_id |
| `POST /api/kb/search` | body: {query, top_k, meeting_id?} → RAG.query(where=...) |
| `GET /api/kb/list` | ?meeting_id= → RAG.count() + 列 ids+metadata |
| `DELETE /api/kb/{doc_id}` | → RAG.delete() |

### 迁移路径 (废弃旧 KB, ADR-0020 § 实施步骤)

1. 写 `src/vpbuddy/rag_backend.py` (ChromaRAG 实现)
2. 写 `src/vpbuddy/kb_api.py` (API 端点, wrap RAGBackend)
3. 删 `src/vpbuddy/knowledge_base.py` 全部 (手写 sqlite-vec)
4. 删 `sub_session_controller.py:520-580` (6 docs 自动 ingest 逻辑)
5. 清旧 `data/knowledge.db` 文件 (user opt-in via CLI `vpbuddy kb migrate`)
6. UI 改"知识库"页: 搜索框 + 文件上传按钮 + 会议过滤下拉

## 性能预期 (跟旧 KB 对比)

| 指标 | 旧 (手写 sqlite-vec) | 新 (Chroma) |
|------|---------------------|-------------|
| 启动加载 | <100ms (sqlite 直开) | ~1s (chroma hnswlib 初始化) |
| 1 doc embedding | ~50ms (CPU, 470MB model) | ~30-50ms (同 model, 框架 cache 更优) |
| 1 doc add (含 embedding) | ~80ms | ~80ms |
| query (1K docs) | <100ms (含 embedding) | ~150ms (Chroma 框架开销) |
| 持久化文件 | 单 .db (~50MB/1K docs) | 单文件夹 (~80MB/1K docs, sqlite + bin) |
| 备份 | `cp db` | `tar czf chroma/` |
| NFS 兼容 | ✓ (sqlite 直写) | ✓ (sqlite 底层, 但 hnswlib 文件锁需测试) |

**结论**: 性能略降 (启动 +1s, query +50ms), 但换**解耦 + 自动切分框架 + 以后升级方便**, 值得。

## 关联

- ADR-0015 (Superseded by) — 旧 sqlite-vec 实现
- ADR-0020 — 知识库方案废弃 + 文件上传 + 会议隔离 (本期实施)
- ADR-0025 — agent 调 KB 检索 (RAGBackend 暴露给 agent)
- `src/vpbuddy/rag_backend.py` (新建)
- `src/vpbuddy/kb_api.py` (新建)
- `pyproject.toml` — `chromadb>=0.5.0` 加到 `[project.dependencies]`
