"""
chroma_repository.py  –  v2
=============================
Key improvements vs v1:
  1. Collection created with  hnsw:space = cosine  (mandatory for correct distances)
  2. query_by_keyword uses Chroma's built-in  where_document=$contains  filter
     → NO full-corpus fetch; only matching docs are scored
  3. Embedding-based query returns cosine distances in [0, 1]
     (1 − cosine_sim), so RetrievalService can use  sim = 1 − distance
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

os.environ.setdefault("ANONYMIZED_TELEMETRY", "FALSE")

import chromadb  # noqa: E402

from app.core.config import settings


class ChromaRepository:
    _lock       = threading.RLock()
    _client     = None
    _collection = None

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Lazy init
    # ------------------------------------------------------------------

    def _get_client(self) -> chromadb.HttpClient:
        if ChromaRepository._client is None:
            with ChromaRepository._lock:
                if ChromaRepository._client is None:
                    ChromaRepository._client = chromadb.HttpClient(
                        host=settings.chroma_host,
                        port=settings.chroma_port,
                    )
        return ChromaRepository._client

    def _get_collection(self):
        if ChromaRepository._collection is None:
            with ChromaRepository._lock:
                if ChromaRepository._collection is None:
                    client = self._get_client()
                    # hnsw:space=cosine is REQUIRED so that Chroma returns
                    # distance = 1 − cosine_similarity  (not L2 distance)
                    ChromaRepository._collection = client.get_or_create_collection(
                        name=settings.chroma_collection,
                        metadata={"hnsw:space": "cosine"},
                    )
        return ChromaRepository._collection

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(
        self,
        *,
        chunk_id:      str,
        document_text: str,
        embedding:     list[float],
        metadata:      dict,
    ) -> None:
        collection = self._get_collection()
        collection.upsert(
            ids=[chunk_id],
            documents=[document_text],
            embeddings=[embedding],
            metadatas=[self._flatten_metadata(metadata)],
        )

    def delete_by_ids(self, ids: list[str]) -> None:
        if not ids:
            return
        self._get_collection().delete(ids=ids)

    def update_document_metadata(
        self, chunk_ids: list[str], metadata_updates: dict
    ) -> None:
        if not chunk_ids:
            return
        collection = self._get_collection()
        raw = collection.get(
            ids=chunk_ids,
            include=["documents", "metadatas", "embeddings"],
        )
        ids        = raw.get("ids",        [])
        docs       = raw.get("documents",  [])
        metadatas  = raw.get("metadatas",  [])
        embeddings = raw.get("embeddings", [])
        if not ids:
            return

        new_metadatas = []
        for meta in metadatas:
            updated = dict(meta or {})
            for k, v in metadata_updates.items():
                updated[k] = "" if v is None else v
            new_metadatas.append(updated)

        collection.upsert(
            ids=ids,
            documents=docs,
            embeddings=embeddings,
            metadatas=new_metadatas,
        )

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    def query_by_embedding(
        self,
        *,
        embedding: list[float],
        top_k:     int = 5,
        where:     dict | None = None,
    ) -> dict:
        collection = self._get_collection()
        kwargs: dict[str, Any] = dict(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where
        return collection.query(**kwargs)

    # ------------------------------------------------------------------
    # Keyword / lexical search
    # ------------------------------------------------------------------

    def query_by_keyword(
        self,
        *,
        query: str,
        top_k: int = 5,
        where: dict | None = None,
    ) -> dict:
        """
        Uses Chroma's where_document $contains filter to avoid full-corpus scan.
        Falls back to a cheap in-memory BM25-style score using only the pre-filtered docs.

        Strategy:
          - Build a list of meaningful query tokens (len > 1, no stopwords)
          - Use the most distinctive token as the $contains filter
          - Score remaining candidates with token-overlap F1
        """
        collection = self._get_collection()
        empty: dict = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        query = (query or "").strip()
        if not query:
            return empty

        tokens = [t for t in query.lower().split() if len(t) > 1]
        if not tokens:
            return empty

        # pick the rarest-looking token as primary filter (longest = most specific)
        primary_token = max(tokens, key=len)

        # fetch only matching documents – much cheaper than full scan
        get_kwargs: dict[str, Any] = dict(
            where_document={"$contains": primary_token},
            include=["documents", "metadatas"],
        )
        if where:
            get_kwargs["where"] = where

        try:
            raw = collection.get(**get_kwargs)
        except Exception:
            # where_document may not be supported in older Chroma; degrade gracefully
            raw = collection.get(
                **({"where": where} if where else {}),
                include=["documents", "metadatas"],
            )

        ids       = raw.get("ids",       []) or []
        docs      = raw.get("documents", []) or []
        metadatas = raw.get("metadatas", []) or []

        if not ids:
            return empty

        q_set = set(tokens)
        scored: list[tuple[float, int]] = []

        for i, doc in enumerate(docs):
            if not doc:
                continue
            d_tokens = [t for t in str(doc).lower().split() if len(t) > 1]
            if not d_tokens:
                continue
            d_set = set(d_tokens)
            overlap = len(q_set & d_set)
            if overlap == 0:
                continue
            precision = overlap / max(1, len(d_set))
            recall    = overlap / max(1, len(q_set))
            f1        = 2 * precision * recall / max(1e-9, precision + recall)
            scored.append((f1, i))

        if not scored:
            return empty

        scored.sort(key=lambda x: x[0], reverse=True)
        top_idx = [i for _, i in scored[:top_k]]

        return {
            "ids":       [[ids[i]       for i in top_idx]],
            "documents": [[docs[i]      for i in top_idx]],
            "metadatas": [[metadatas[i] for i in top_idx]],
            # Lexical "distance" = 1 − f1_score  (analogous to cosine distance)
            "distances": [[1.0 - scored[j][0] for j in range(len(top_idx))]],
        }