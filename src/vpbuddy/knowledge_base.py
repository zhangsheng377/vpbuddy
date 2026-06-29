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
        # 2026-06-30: WAL 模式 + 锁 + timeout — 修多线程 6 docs trigger database is locked
        # 张胜东反馈: 6 AIAgent 同时调 add_document 抢锁失败 3 次
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,  # 多线程 (6 docs trigger 同时写)
            timeout=30,               # 等锁最多 30s
        )
        self._conn.execute("PRAGMA journal_mode=WAL")  # WAL 模式读不阻塞写
        self._conn.execute("PRAGMA busy_timeout=30000")  # 30s busy timeout
        # 2026-06-30: 写操作串行化锁 — sqlite3 单连接多线程不安全
        # 6 docs 触发 → 6 个 thread 同时 add_document, 加锁串行写
        self._write_lock = __import__('threading').RLock()
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

        # 2026-06-30: 写锁 — 6 docs 触发并发写 KB 时, 串行化避免 database is locked
        # 注意: encoding (model.encode) 不在锁内, 锁只包 SQL 写, 否则 GPU/内存浪费
        with self._write_lock:
            # 1. upsert 元数据(同 meeting+kind 覆盖)
            # ⚠️ Pydantic/SQLite: INSERT OR REPLACE 会复用 rowid
            #    必须先查老 doc_id 再删 vec, 不能 lastrowid (REPLACE 时拿到的是新 id, 旧 vec 残留)
            old_row = self._conn.execute(
                "SELECT id FROM documents WHERE meeting_id = ? AND doc_kind = ?",
                (meeting_id, doc_kind),
            ).fetchone()
            old_doc_id = old_row[0] if old_row else None
            if old_doc_id is not None:
                self._conn.execute("DELETE FROM vec_documents WHERE id = ?", (old_doc_id,))
                self._conn.execute("DELETE FROM documents WHERE id = ?", (old_doc_id,))
                self._conn.commit()

            cur = self._conn.execute(
                "INSERT INTO documents (meeting_id, doc_kind, content) VALUES (?, ?, ?)",
                (meeting_id, doc_kind, content),
            )
            doc_id = cur.lastrowid

            # 2. 算 embedding(整文档平均, 简单粗暴) — 在锁内复用同一事务
            model = self._get_model()
            vec = model.encode(content, normalize_embeddings=True).tolist()
            vec_blob = sqlite_vec.serialize_float32(vec)

            # 3. 插入新 vec (用真新 doc_id, 老 vec 已删)
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


# === 便利函数(单例,但 sqlite3 connection 不能跨线程,每线程一份) ===
import threading as _threading
_default_kb = None
_default_kb_lock = _threading.Lock()


def get_kb() -> KnowledgeBase:
    """获取当前线程的 KB 实例(每个线程独立连接,避免 SQLite thread error)

    第一次调用慢(sentence-transformers 冷加载 40s),后续命中缓存。
    """
    global _default_kb
    tid = _threading.get_ident()
    with _default_kb_lock:
        if _default_kb is None or getattr(_default_kb, "_thread_id", None) != tid:
            _default_kb = KnowledgeBase()
            _default_kb._thread_id = tid  # type: ignore[attr-defined]
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
