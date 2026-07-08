"""RAG backend abstraction layer (Chroma embedded, ADR-0019)"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Auto-computed project root. P1#1 (2026-07-04)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Default data directory
DATA_DIR = Path(os.environ.get("VPBUDDY_DATA_DIR", PROJECT_ROOT / "data"))


# ── 类型别名 ──
Metadata = dict[str, Any]
SearchResult = list[dict[str, Any]]


def _detect_device() -> str:
    """检测最佳 embedding 设备: 优先 GPU, fallback CPU.

    可通过 VPBUDDY_EMBEDDING_DEVICE 环境变量强制指定.
    """
    forced = os.environ.get("VPBUDDY_EMBEDDING_DEVICE", "")
    if forced:
        return forced
    try:
        import torch
        if torch.cuda.is_available():
            logger.info("ChromaRAG: CUDA 可用, 使用 GPU 进行 embedding")
            return "cuda"
    except ImportError:
        pass
    return "cpu"


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

        persist_dir = Path(path) if path else Path(os.environ.get("VPBUDDY_KB_DIR", DATA_DIR / "chroma"))
        persist_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ChromaRAG init: path=%s model=%s", persist_dir, model_name)

        _client = chromadb.PersistentClient(path=str(persist_dir))
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_name,
            device=_detect_device(),
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

    def list_docs(
        self,
        where: dict[str, str] | None = None,
        limit: int = 1000,
    ) -> SearchResult:
        """列出文档及元数据 (按 where 条件过滤)."""
        raw = self._collection.get(where=where, limit=limit)
        ids = raw.get("ids") or []
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        results: SearchResult = []
        for i in range(len(ids)):
            results.append({
                "id": ids[i] if i < len(ids) else "",
                "document": docs[i] if i < len(docs) else "",
                "distance": 0.0,
                "metadata": metas[i] if i < len(metas) else {},
            })
        return results

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
