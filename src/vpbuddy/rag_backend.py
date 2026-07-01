"""RAG 后端抽象层 — Chroma 嵌入式实现 (ADR-0019)

提供统一的 add / query / delete / count 接口, 业务代码只调此接口。
本期实现: ChromaRAG (wrap chromadb.PersistentClient)
未来替换: 重写同类, 不修改业务代码。

用法:
    rag = get_rag()
    rag.add(ids=["doc1"], documents=["Hello world"], metadatas=[{"meeting_id": "mtg1"}])
    results = rag.query("hello", top_k=5, where={"meeting_id": "mtg1"})
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认数据目录
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", "/home/zsd/vpbuddy/data"))

# ── 类型别名 ──
Metadata = dict[str, Any]
SearchResult = list[dict[str, Any]]


class RAGBackend:
    """RAG 后端抽象接口 (Protocol)."""

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[Metadata] | None = None,
    ) -> None:
        """批量插入文档 (自动 embedding)."""
        ...

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        where: dict[str, str] | None = None,
    ) -> SearchResult:
        """检索, 返回 [{id, metadata, distance, document}, ...]."""
        ...

    def delete(self, ids: list[str]) -> None:
        """按 ID 删除文档."""
        ...

    def count(self, where: dict[str, str] | None = None) -> int:
        """统计文档数 (可选过滤)."""
        ...


class ChromaRAG(RAGBackend):
    """Chroma 嵌入式实现 (ADR-0019).

    配置:
        path: Chroma 持久化目录 (默认 data/chroma)
        collection_name: 集合名 (默认 vpbuddy_kb)
        model_name: embedding 模型 (默认 paraphrase-multilingual-MiniLM-L12-v2, 384 维)
    """

    def __init__(
        self,
        path: str | Path | None = None,
        collection_name: str = "vpbuddy_kb",
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
    ) -> None:
        import chromadb
        from chromadb.utils import embedding_functions

        persist_dir = Path(path) if path else (DATA_DIR / "chroma")
        persist_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ChromaRAG init: path=%s model=%s", persist_dir, model_name)

        _client = chromadb.PersistentClient(path=str(persist_dir))
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_name,
            device="cpu",
        )
        self._collection = _client.get_or_create_collection(
            name=collection_name,
            embedding_function=_ef,
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[Metadata] | None = None,
    ) -> None:
        if not ids:
            return
        self._collection.add(ids=ids, documents=documents, metadatas=metadatas)
        logger.debug("ChromaRAG added %d docs", len(ids))

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        where: dict[str, str] | None = None,
    ) -> SearchResult:
        if not query_text.strip():
            return []

        raw = self._collection.query(
            query_texts=[query_text],
            n_results=top_k,
            where=where,
        )

        results: SearchResult = []
        ids = raw.get("ids") or [[]]
        dists = raw.get("distances") or [[]]
        docs = raw.get("documents") or [[]]
        metas = raw.get("metadatas") or [[]]
        ids_0 = ids[0] if ids else []
        dists_0 = dists[0] if dists else []
        docs_0 = docs[0] if docs else []
        metas_0 = metas[0] if metas else []

        for i in range(len(ids_0)):
            results.append({
                "id": ids_0[i] if i < len(ids_0) else "",
                "document": docs_0[i] if i < len(docs_0) else "",
                "distance": float(dists_0[i]) if i < len(dists_0) else 0.0,
                "metadata": metas_0[i] if i < len(metas_0) else {},
            })

        return results

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        self._collection.delete(ids=ids)
        logger.debug("ChromaRAG deleted %d docs", len(ids))

    def count(self, where: dict[str, str] | None = None) -> int:
        return self._collection.count()


# ── 全局单例 ──
_rag: ChromaRAG | None = None


def get_rag() -> ChromaRAG:
    """获取全局 RAG 实例 (惰性初始化)."""
    global _rag
    if _rag is None:
        _rag = ChromaRAG()
    return _rag


def reset_rag() -> None:
    """重置全局 RAG (测试用)."""
    global _rag
    _rag = None
