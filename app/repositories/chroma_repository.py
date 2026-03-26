from __future__ import annotations

import json
import os
import threading
from typing import Any

# Disable Chroma anonymized telemetry by default in this process.
# Prefer setting this in Docker/env as well.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "FALSE")

import chromadb  # noqa: E402

from app.core.config import settings


class ChromaRepository:
    """
    Lazy-initialized Chroma repository.

    Notes:
    - Uses Chroma collection query API for semantic retrieval.
    - Uses full-corpus scan for lexical candidate retrieval.
    - Avoids creating PersistentClient / collection at import time.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._client = None
        self._collection = None

    def _ensure_persist_dir(self) -> None:
        os.makedirs(settings.chroma_path, exist_ok=True)

    def _get_client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._ensure_persist_dir()
                    self._client = chromadb.PersistentClient(path=settings.chroma_path)
        return self._client

    def _get_collection(self):
        if self._collection is None:
            with self._lock:
                if self._collection is None:
                    client = self._get_client()
                    try:
                        self._collection = client.get_or_create_collection(
                            name=settings.chroma_collection,
                        )
                    except TypeError:
                        self._collection = client.get_or_create_collection(
                            name=settings.chroma_collection,
                            metadata={"hnsw:space": "cosine"},
                        )
        return self._collection

    def _flatten_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}

        for k, v in (metadata or {}).items():
            if v is None:
                continue

            if isinstance(v, (str, int, float, bool)):
                flat[k] = v
            else:
                flat[k] = json.dumps(v, ensure_ascii=False)

        return flat

    def upsert(
        self,
        *,
        chunk_id: str,
        document_text: str,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        collection = self._get_collection()
        flat_metadata = self._flatten_metadata(metadata)

        collection.upsert(
            ids=[chunk_id],
            documents=[document_text],
            embeddings=[embedding],
            metadatas=[flat_metadata],
        )

    def query_by_embedding(self, *, embedding: list[float], top_k: int = 5) -> dict:
        collection = self._get_collection()

        return collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

    def query_by_keyword(self, *, query: str, top_k: int = 5) -> dict:
        """
        Full-corpus lexical candidate retrieval.

        This scans the whole collection and ranks documents by simple token overlap.
        Returned shape is aligned with collection.query() as much as possible.
        """
        collection = self._get_collection()
        query = (query or "").strip()
        if not query:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        q_tokens = [t for t in query.lower().split() if t]
        if not q_tokens:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        # Fetch the whole corpus for lexical scoring
        raw = collection.get(include=["documents", "metadatas"])

        ids = raw.get("ids", []) or []
        docs = raw.get("documents", []) or []
        metadatas = raw.get("metadatas", []) or []

        scored: list[tuple[float, int]] = []

        q_set = set(q_tokens)

        for i, doc in enumerate(docs):
            if not doc:
                continue
            d_tokens = [t for t in str(doc).lower().split() if t]
            if not d_tokens:
                continue

            d_set = set(d_tokens)
            overlap = len(q_set & d_set)
            if overlap <= 0:
                continue

            precision = overlap / max(1, len(d_set))
            recall = overlap / max(1, len(q_set))
            f1 = (2 * precision * recall) / max(1e-9, (precision + recall))
            scored.append((f1, i))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_idx = [i for _, i in scored[:top_k]]

        return {
            "ids": [[ids[i] for i in top_idx]],
            "documents": [[docs[i] for i in top_idx]],
            "metadatas": [[metadatas[i] for i in top_idx]],
            "distances": [[1.0 - scored[j][0] for j in range(min(len(top_idx), len(scored)))]],
        }
