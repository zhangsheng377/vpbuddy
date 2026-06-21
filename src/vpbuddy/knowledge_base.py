"""知识库 — 跨会议 RAG 检索

设计(ADR-0006 扩展):
- 所有写出的 doc(6 kind × N meetings)都自动存进向量库
- 跨会议检索:给 query → top_k 最相关文档(带 meeting_id/doc_kind 锚点)
- Embedding 模型: paraphrase-multilingual-MiniLM-L12-v2(已下载,~470MB,支持中英)
- 存储: sqlite-vec(NFS 兼容,无需新组件)

典型用法:
    kb = KnowledgeBase()  # 默认 /home/zsd/vpbuddy/data/knowledge.db
    kb.add_document("MTG01", "req", "...内容...")  # 写文档时自动调
    results = kb.search("SSO 走哪个 IdP?", top_k=3)
    for r in results:
        print(f"[{r['meeting_id']}/{r['doc_kind']}] {r['snippet']}")
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional

# sqlite-vec 是 SQLite 扩展,需要手动 load
try:
    import sqlite_vec
    SQLITE_VEC_LOADED = True
except ImportError:
    SQLITE_VEC_LOADED = False


# 默认数据库路径
DEFAULT_DB = Path(os.environ.get("VPBUDDY_KB_DB", "/home/zsd/vpbuddy/data/knowledge.db"))
DEFAULT_EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# multilingual-MiniLM-L12-v2 维度
EMBED_DIM = 384


class KnowledgeBase:
    """跨会议向量知识库"""

    def __init__(self, db_path: Path | str = DEFAULT_DB, model_name: str = DEFAULT_EMBED_MODEL):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._model = None  # 懒加载
        self._model_name = model_name
        self._init_db()

    def _init_db(self) -> None:
        """初始化表 + vec0 虚拟表"""
        if not SQLITE_VEC_LOADED:
            raise RuntimeError("sqlite-vec not installed. pip install sqlite-vec")
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

        # 元数据表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id TEXT NOT NULL,
                doc_kind TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(meeting_id, doc_kind)
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_meeting ON documents(meeting_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_kind ON documents(doc_kind)")

        # 向量表(vec0 虚拟表)
        self._conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(
                id INTEGER PRIMARY KEY,
                embedding FLOAT[{EMBED_DIM}]
            )
        """)
        self._conn.commit()

    def _get_model(self):
        """懒加载 embedding 模型(首次用时加载,后续复用)"""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def add_document(self, meeting_id: str, doc_kind: str, content: str) -> int:
        """存一个文档进知识库(自动 embedding)

        Returns:
            文档 id(用于跨表关联)
        """
        if not content or not content.strip():
            return -1

        # 1. upsert 元数据(同 meeting+kind 覆盖)
        cur = self._conn.execute(
            "INSERT OR REPLACE INTO documents (meeting_id, doc_kind, content) VALUES (?, ?, ?)",
            (meeting_id, doc_kind, content),
        )
        doc_id = cur.lastrowid

        # 2. 算 embedding(整文档平均, 简单粗暴)
        model = self._get_model()
        vec = model.encode(content, normalize_embeddings=True).tolist()
        vec_blob = sqlite_vec.serialize_float32(vec)

        # 3. upsert 向量(先删旧的, 再插新的)
        self._conn.execute("DELETE FROM vec_documents WHERE id = ?", (doc_id,))
        self._conn.execute(
            "INSERT INTO vec_documents (id, embedding) VALUES (?, ?)",
            (doc_id, vec_blob),
        )
        self._conn.commit()
        return doc_id

    def search(self, query: str, top_k: int = 5,
               meeting_id: Optional[str] = None) -> List[Dict]:
        """跨会议检索

        Args:
            query: 检索关键词
            top_k: 返回 top K
            meeting_id: 限定某会议(None = 跨会议)

        Returns:
            [{"id", "meeting_id", "doc_kind", "snippet", "distance"}, ...]
        """
        if not query or not query.strip():
            return []

        # 1. embedding
        model = self._get_model()
        query_vec = model.encode(query, normalize_embeddings=True).tolist()
        query_blob = sqlite_vec.serialize_float32(query_vec)

        # 2. 查最近 K 个
        # vec0 用 distance 函数(cosine/L2)
        # vec0 KNN 必须用 k = ? 约束(不是 LIMIT)
        # 多取一些(×10),在 Python 端过滤 meeting_id 后再截 top_k
        fetch_k = top_k * 10 if meeting_id else top_k
        rows = self._conn.execute("""
            SELECT
                v.id,
                v.distance,
                d.meeting_id,
                d.doc_kind,
                d.content
            FROM vec_documents v
            JOIN documents d ON d.id = v.id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
        """, (query_blob, fetch_k)).fetchall()

        results = []
        for row_id, dist, mid, kind, content in rows:
            if meeting_id and mid != meeting_id:
                continue
            results.append({
                "id": row_id,
                "meeting_id": mid,
                "doc_kind": kind,
                "snippet": content[:300] + ("..." if len(content) > 300 else ""),
                "full_content": content,
                "distance": dist,
            })
            if len(results) >= top_k:
                break
        return results

    def list_documents(self, meeting_id: Optional[str] = None) -> List[Dict]:
        """列出所有已存文档(调试用)"""
        if meeting_id:
            rows = self._conn.execute(
                "SELECT id, meeting_id, doc_kind, length(content), created_at FROM documents WHERE meeting_id = ? ORDER BY id",
                (meeting_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, meeting_id, doc_kind, length(content), created_at FROM documents ORDER BY id"
            ).fetchall()
        return [
            {"id": r[0], "meeting_id": r[1], "doc_kind": r[2], "size": r[3], "created_at": r[4]}
            for r in rows
        ]

    def delete_meeting(self, meeting_id: str) -> int:
        """删除某会议所有文档(慎用)"""
        doc_ids = [r[0] for r in self._conn.execute(
            "SELECT id FROM documents WHERE meeting_id = ?", (meeting_id,)
        ).fetchall()]
        if not doc_ids:
            return 0
        for did in doc_ids:
            self._conn.execute("DELETE FROM vec_documents WHERE id = ?", (did,))
        self._conn.execute("DELETE FROM documents WHERE meeting_id = ?", (meeting_id,))
        self._conn.commit()
        return len(doc_ids)

    def close(self) -> None:
        if self._conn:
            self._conn.close()


# === 便利函数(单例) ===
_default_kb: Optional[KnowledgeBase] = None


def get_kb() -> KnowledgeBase:
    global _default_kb
    if _default_kb is None:
        _default_kb = KnowledgeBase()
    return _default_kb


if __name__ == "__main__":
    # 简单 self-test
    print("=== KnowledgeBase self-test ===")
    kb = KnowledgeBase(db_path="/tmp/vpbuddy_kb_test.db")
    print(f"DB: {kb.db_path}")

    # 存 3 篇
    kb.add_document("MTG01", "req", "支持 SSO 登录,对接企业 AD 域控")
    kb.add_document("MTG01", "api", "POST /api/v1/auth/login 返回 JWT, 过期时间 1h")
    kb.add_document("MTG02", "req", "微信扫码登录,降低注册门槛")

    # 检索
    print("\nQuery: '单点登录'")
    for r in kb.search("单点登录", top_k=3):
        print(f"  [{r['meeting_id']}/{r['doc_kind']}] dist={r['distance']:.4f}  {r['snippet'][:80]}")

    print("\nQuery: '微信'")
    for r in kb.search("微信", top_k=3):
        print(f"  [{r['meeting_id']}/{r['doc_kind']}] dist={r['distance']:.4f}  {r['snippet'][:80]}")

    print(f"\nTotal docs: {len(kb.list_documents())}")
    kb.close()
