from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any

from app.repositories.chroma_repository import ChromaRepository
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[0-9A-Za-z?-?_]+", re.UNICODE)


class RetrievalService:
    def __init__(
        self,
        candidate_multiplier: int = 4,
        semantic_weight: float = 0.72,
        keyword_weight: float = 0.28,
        minimum_score: float = 0.0,
    ):
        self.repo = ChromaRepository()
        self.candidate_multiplier = max(1, candidate_multiplier)
        self.semantic_weight = semantic_weight
        self.keyword_weight = keyword_weight
        self.minimum_score = minimum_score

    def _tokenize(self, text: str) -> list[str]:
        return _WORD_RE.findall((text or "").lower())

    def _distance_to_similarity(self, distance: Any) -> float:
        """
        Chroma distance c? th? kh?c theo metric.
        Chuy?n v? score ??n ?i?u: distance nh? -> similarity l?n.
        """
        try:
            d = float(distance)
            if not math.isfinite(d):
                return 0.0
            if d < 0:
                d = 0.0
            return 1.0 / (1.0 + d)
        except Exception:
            return 0.0

    def _keyword_score(self, query: str, document: str) -> float:
        q_tokens = self._tokenize(query)
        d_tokens = self._tokenize(document)

        if not q_tokens or not d_tokens:
            return 0.0

        q_counter = Counter(q_tokens)
        d_counter = Counter(d_tokens)

        overlap = sum(min(q_counter[t], d_counter[t]) for t in q_counter.keys())
        precision = overlap / max(1, len(d_tokens))
        recall = overlap / max(1, len(q_tokens))

        f1 = (2 * precision * recall) / max(1e-9, (precision + recall))

        # th??ng nh? n?u query xu?t hi?n nguy?n c?m
        exact_phrase_bonus = 0.0
        q_phrase = " ".join(q_tokens[:12]).strip()
        d_norm = " ".join(d_tokens)
        if q_phrase and q_phrase in d_norm:
            exact_phrase_bonus = 0.20

        return min(1.0, f1 + exact_phrase_bonus)

    def _combine_score(self, semantic_score: float, keyword_score: float) -> float:
        return (
            self.semantic_weight * semantic_score
            + self.keyword_weight * keyword_score
        )

    def _rerank(self, query: str, raw: dict) -> list[dict]:
        results: list[dict] = []

        ids = raw.get("ids", [[]])[0] if raw.get("ids") else []
        docs = raw.get("documents", [[]])[0] if raw.get("documents") else []
        metadatas = raw.get("metadatas", [[]])[0] if raw.get("metadatas") else []
        distances = raw.get("distances", [[]])[0] if raw.get("distances") else []

        for i, chunk_id in enumerate(ids):
            metadata = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
            document_text = docs[i] if i < len(docs) else ""
            distance = distances[i] if i < len(distances) else None

            semantic_score = self._distance_to_similarity(distance)
            keyword_score = self._keyword_score(query, document_text)
            final_score = self._combine_score(semantic_score, keyword_score)

            if final_score < self.minimum_score:
                continue

            results.append(
                {
                    "chunk_id": chunk_id,
                    "document_text": document_text,
                    "metadata": metadata,
                    "distance": distance,
                    "semantic_score": round(semantic_score, 6),
                    "keyword_score": round(keyword_score, 6),
                    "score": round(final_score, 6),
                }
            )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def retrieve(self, *, query: str, user=None, top_k: int = 5) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []

        if not embedding_service.is_configured():
            raise RuntimeError("Embedding service is not configured")

        emb = embedding_service.embed(query)

        candidate_k = max(top_k * self.candidate_multiplier, top_k)
        raw = self.repo.query_by_embedding(embedding=emb, top_k=candidate_k)

        if not raw or not raw.get("ids") or all(len(x) == 0 for x in raw.get("ids", [])):
            logger.warning("Retrieval returned no ids for query=%s; raw=%s", query, raw)
            return []

        ranked = self._rerank(query, raw)
        return ranked[:top_k]


retrieval_service = RetrievalService()
