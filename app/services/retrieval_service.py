from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any
from app.fga.adapter import fga_adapter

from app.repositories.chroma_repository import ChromaRepository
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[0-9A-Za-z?-?_]+", re.UNICODE)

# TODO: check permission retrieval for chunk
class RetrievalService:
    def __init__(
        self,
        candidate_multiplier: int = 4,
        semantic_weight: float = 0.72,
        keyword_weight: float = 0.28,
        minimum_score: float = 0.3,
        lexical_candidate_k: int = 50,
    ):
        self.repo = ChromaRepository()
        self.candidate_multiplier = max(1, candidate_multiplier)
        self.semantic_weight = semantic_weight
        self.keyword_weight = keyword_weight
        self.minimum_score = minimum_score
        self.lexical_candidate_k = lexical_candidate_k

    def _tokenize(self, text: str) -> list[str]:
        return _WORD_RE.findall((text or "").lower())

    def _distance_to_similarity(self, distance: Any) -> float:
        """
        Chuyển distance của Chroma sang similarity.
        distance càng nhỏ thì similarity càng lớn.
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

        exact_phrase_bonus = 0.0
        q_phrase = " ".join(q_tokens[:12]).strip()
        d_norm = " ".join(d_tokens)
        if q_phrase and q_phrase in d_norm:
            exact_phrase_bonus = 0.20

        return min(1.0, f1 + exact_phrase_bonus)

    def _combine_score(self, semantic_score: float, keyword_score: float) -> float:
        return self.semantic_weight * semantic_score + self.keyword_weight * keyword_score

    def _merge_candidates(self, semantic_raw: dict, lexical_raw: dict) -> list[dict]:
        merged: dict[str, dict] = {}

        def add_batch(raw: dict, source: str) -> None:
            ids = raw.get("ids", [[]])[0] if raw.get("ids") else []
            docs = raw.get("documents", [[]])[0] if raw.get("documents") else []
            metadatas = raw.get("metadatas", [[]])[0] if raw.get("metadatas") else []
            distances = raw.get("distances", [[]])[0] if raw.get("distances") else []

            for i, chunk_id in enumerate(ids):
                metadata = metadatas[i] if i < len(metadatas) and metadatas[i] else {}
                document_text = docs[i] if i < len(docs) else ""
                distance = distances[i] if i < len(distances) else None

                if chunk_id not in merged:
                    merged[chunk_id] = {
                        "chunk_id": chunk_id,
                        "document_text": document_text,
                        "metadata": metadata,
                        "distance": distance,
                        "sources": {source},
                    }
                else:
                    if not merged[chunk_id]["document_text"] and document_text:
                        merged[chunk_id]["document_text"] = document_text
                    if not merged[chunk_id]["metadata"] and metadata:
                        merged[chunk_id]["metadata"] = metadata
                    if merged[chunk_id]["distance"] is None and distance is not None:
                        merged[chunk_id]["distance"] = distance
                    merged[chunk_id]["sources"].add(source)

        add_batch(semantic_raw or {}, "semantic")
        add_batch(lexical_raw or {}, "lexical")

        return list(merged.values())

    def _rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        results: list[dict] = []

        for item in candidates:
            chunk_id = item.get("chunk_id")
            metadata = item.get("metadata") or {}
            document_text = item.get("document_text") or ""
            distance = item.get("distance")

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
                    "sources": sorted(list(item.get("sources", []))),
                }
            )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def retrieve(self, *, query: str, user=None, top_k: int = 3, mode: str = "hybrid") -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []

        if not embedding_service.is_configured():
            raise RuntimeError("Embedding service is not configured")

        # ── Bước 1: Lấy danh sách doc user được phép xem TRƯỚC ──────────────
        allowed_doc_ids: list[str] | None = None
        if user is not None:
            allowed_doc_ids = fga_adapter.list_viewable_document_ids(user.id)
            if not allowed_doc_ids:
                return []  # không có doc nào được phép → trả về luôn

        # ── Bước 2: Query Chroma với filter ─────────────────────────────────
        use_filter = allowed_doc_ids is not None

        if mode == "keyword":
            lexical_k = max(self.lexical_candidate_k, top_k * self.candidate_multiplier)
            if use_filter:
                lexical_raw = self.repo.query_by_keyword(
                    query=query, top_k=lexical_k, document_ids=allowed_doc_ids
                )
            else:
                lexical_raw = self.repo.query_by_keyword(query=query, top_k=lexical_k)
            merged = self._merge_candidates({}, lexical_raw)

        elif mode == "semantic":
            emb = embedding_service.embed(query)
            semantic_k = max(top_k * self.candidate_multiplier, top_k)
            if use_filter:
                semantic_raw = self.repo.query_by_embedding(
                    embedding=emb, top_k=semantic_k, document_ids=allowed_doc_ids
                )
            else:
                semantic_raw = self.repo.query_by_embedding(embedding=emb, top_k=semantic_k)
            merged = self._merge_candidates(semantic_raw, {})

        else:  # hybrid
            emb = embedding_service.embed(query)
            semantic_k = max(top_k * self.candidate_multiplier, top_k)
            lexical_k = max(self.lexical_candidate_k, top_k * self.candidate_multiplier)
            if use_filter:
                semantic_raw = self.repo.query_by_embedding(
                    embedding=emb, top_k=semantic_k, document_ids=allowed_doc_ids
                )
                lexical_raw = self.repo.query_by_keyword(
                    query=query, top_k=lexical_k, document_ids=allowed_doc_ids
                )
            else:
                semantic_raw = self.repo.query_by_embedding(embedding=emb, top_k=semantic_k)
                lexical_raw = self.repo.query_by_keyword(query=query, top_k=lexical_k)
            merged = self._merge_candidates(semantic_raw, lexical_raw)

        if not merged:
            return []

        # ── Bước 3: Rerank (không cần filter FGA nữa) ───────────────────────
        ranked = self._rerank(query, merged)
        return ranked[:top_k]

retrieval_service = RetrievalService()
