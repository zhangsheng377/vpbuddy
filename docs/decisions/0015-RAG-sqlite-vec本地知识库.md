# 0012. RAG 接入 — sqlite-vec + sentence-transformers (本地知识库)

- **状态**: 已接受
- **日期**: 2026-06-23
- **作者**: 张胜东 (起草: Hermes)
- **替代**: 无 (新增)
- **依赖**: [ADR-0004](../0004-MVP-Step2-ASR设计.md) (funasr 选型), [ADR-0009](../0009-部署架构-Hermes-runtime.md)

## 背景

VPBuddy 5 doc agent 写完 6 个文档后, VP 需要**跨会议**检索 ("上次会议定的 watchdog 怎么处理?" / "VPBuddy 进度"). 张胜东 2026-06-23 确认: **没单独部署 RAG 系统, 复用本地 sqlite-vec + sentence-transformers**.

## 决策

**RAG = sqlite-vec (向量索引) + sentence-transformers (embedding 模型) + SQLite (元数据)**, 全部本地, 单文件, 零运维.

### 组件

| 组件 | 用途 | 大小 |
|------|------|------|
| `sqlite-vec` PyPI 包 | SQLite 扩展, 存 384 维 float32 向量 + vec0 KNN | ~50KB 编译后 |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | embedding 模型, 384 维, 中英双语 | ~470MB |
| SQLite (Python 自带) | 存元数据 (meeting_id, doc_kind, content, created_at) | 0 依赖 |

### 存储 schema

```sql
-- 元数据 (UNIQUE on meeting_id + doc_kind)
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id TEXT NOT NULL,
  doc_kind TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(meeting_id, doc_kind)
);

-- 向量 (vec0 虚拟表)
CREATE VIRTUAL TABLE vec_documents USING vec0(
  id INTEGER PRIMARY KEY,
  embedding FLOAT[384]
);
```

### 数据流

```
6 sub-session 写完 doc.md →
  KB.add_document(meeting_id, doc_kind, content):
    1. encode(content) → 384-d float32 (model on GPU 端 CPU 推理, ~50ms)
    2. UPSERT documents 表 (text + metadata)
    3. UPSERT vec_documents 表 (doc.id ↔ vec blob)

Web UI 搜 "watchdog" →
  KB.search(query, top_k=5):
    1. encode(query) → 384-d float32
    2. sqlite-vec KNN MATCH vec_blob AND k=20
    3. JOIN documents (filter meeting_id if specified, fetch_k=10×top_k)
    4. 按 distance 升序截 top_k
    5. Return {id, meeting_id, doc_kind, snippet, distance}
```

### 关键修复 (commit `44a701a`)

**Bug**: `INSERT OR REPLACE` + `lastrowid` 组合产生 **2422 个 orphan vec rows**. SQLite `INSERT OR REPLACE` 复用 rowid (e.g. 旧 doc 删后 `lastrowid` 返新 id 14, 但旧 vec row id=14 残留指向**不存在**的 documents.id). KNN JOIN 静默返 0 results.

**修法** (src/vpbuddy/knowledge_base.py:88-108):
```python
# 修前: INSERT OR REPLACE 复用 rowid, DELETE 用 lastrowid (新 id, 旧 vec 残留)
# 修后: 先查老 doc_id → 删老 vec + 老 doc → INSERT 新 doc → INSERT 新 vec
old_row = self._conn.execute(
    "SELECT id FROM documents WHERE meeting_id = ? AND doc_kind = ?",
    (meeting_id, doc_kind),
).fetchone()
old_doc_id = old_row[0] if old_row else None
if old_doc_id is not None:
    self._conn.execute("DELETE FROM vec_documents WHERE id = ?", (old_doc_id,))
    self._conn.execute("DELETE FROM documents WHERE id = ?", (old_doc_id,))
cur = self._conn.execute(
    "INSERT INTO documents (meeting_id, doc_kind, content) VALUES (?, ?, ?)",
    (meeting_id, doc_kind, content),
)
doc_id = cur.lastrowid
self._conn.execute(
    "INSERT INTO vec_documents (id, embedding) VALUES (?, ?)",
    (doc_id, vec_blob),
)
```

**验证**: 清理 2422 orphan → 重 ingest 6 E2E docs → 搜 "watchdog" 返 5 命中 ✅

### 性能特征

| 阶段 | 实测 |
|------|------|
| 单 doc embedding (470MB model, CPU 推理) | ~50ms |
| 单 query embedding | ~30ms |
| vec0 KNN top=20 (67 个 doc) | <5ms |
| JOIN + Python 端 sort + 截 top_k | <2ms |
| **总检索 latency** | **<100ms** (含 model 推理) |

**规模上限** (粗估):
- 10000 docs = 10K × 384 × 4 bytes = 15MB vec0 表 + 50MB documents → KNN 仍 <50ms
- 100000 docs = 150MB vec0 + 500MB documents → KNN ~200ms (要分片/降维)

VPBuddy MVP 阶段 (几十会议 × 6 doc = 几百 docs) 完全在毫秒级.

## 拒绝的方案

### 拒绝: Qdrant / Milvus / Weaviate 单独服务
- 跟 ADR-0009 "VPBuddy = 完全本地"矛盾 (额外进程, 额外端口)
- 零运维 vs N 步部署
- sqlite-vec 单文件备份 = `cp` 即可

### 拒绝: pgvector
- 需要 PostgreSQL 进程, 跟 sqlite-vec 一样能存向量, 但部署成本高
- VPBuddy 已经用 SQLite, 多一个 DB 引擎没必要

### 拒绝: 自己写倒排索引 (BM25)
- 中文分词 + 倒排实现量大
- embedding 检索对自然语言 (含 typo, 同义) 鲁棒
- 两者 hybrid 留给 v2.0

## 关联

- src/vpbuddy/knowledge_base.py — RAG 实现
- src/vpbuddy/ui_server.py:88-110 — `/api/kb/search` + `/api/kb/status` 端点
- ADR-0011 — VPBuddy hermes skill 描述 KB 角色
- commit `44a701a` — orphan vec 修复
