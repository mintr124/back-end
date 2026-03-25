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
    - Uses Chroma 1.0+ `configuration` dict for index settings when available.
    - Falls back to legacy metadata-based HNSW config for backward compatibility.
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
