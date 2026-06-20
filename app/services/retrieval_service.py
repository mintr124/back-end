"""
retrieval_service.py  –  v2
=============================
Strategy: Hybrid retrieval with Reciprocal Rank Fusion (RRF)

Pipeline:
  1. Parallel semantic search  (Chroma ANN via embedding)
  2. Parallel BM25-style lexical search  (Chroma full-text with where_document)
  3. RRF fusion  → single ranked list
  4. Optional score-threshold filter
  5. FGA permission gate  (chunk-level, not just document-level)

Why RRF instead of weighted sum:
  - Score scales of semantic (cosine distance) and BM25 are incomparable.
    Normalising each independently before weighted sum is fragile.
  - RRF only needs rank positions → robust, parameter-free.
  - k=60 is the standard constant proven to work well empirically.

Cosine distance → similarity:
  Chroma returns  distance = 1 − cosine_sim  (for cosine space).
  So  similarity = 1 − distance  (NOT  1/(1+d) which is for L2).
"""
from __future__ import annotations

import logging
import math
from typing import Any
import re

from app.services.sensitivity_levels import SensitivityLevel, ROLE_MAX_SENSITIVITY, SENSITIVITY_PATTERNS
from app.fga.adapter import fga_adapter
from app.repositories.chroma_repository import ChromaRepository
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

# RRF constant – higher k → smoother ranking (less sensitive to top ranks)
_RRF_K = 60
GMAIL_CHROMA_COLLECTION = "gmail_chunks"


class RetrievalService:
    def __init__(
        self,
        semantic_candidates: int = 20,   # how many to fetch from ANN
        lexical_candidates: int = 20,    # how many to fetch from keyword search
        minimum_score: float = 0.10,     # final RRF-normalised score threshold
    ):
        self.repo = ChromaRepository()
        self.semantic_candidates = semantic_candidates
        self.lexical_candidates  = lexical_candidates
        self.minimum_score       = minimum_score

    # ------------------------------------------------------------------
    # Similarity conversion  (cosine space only)
    # ------------------------------------------------------------------

    def _cosine_sim(self, distance: Any) -> float:
        """Chroma cosine distance → similarity in [0, 1]."""
        try:
            d = float(distance)
            if not math.isfinite(d):
                return 0.0
            # distance is already 1 - cosine_similarity, clamp for safety
            return max(0.0, min(1.0, 1.0 - d))
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # RRF fusion
    # ------------------------------------------------------------------

    def _rrf_score(self, rank: int) -> float:
        """Reciprocal Rank Fusion score for a given 0-based rank."""
        return 1.0 / (_RRF_K + rank + 1)

    def _fuse(
        self,
        semantic_results: list[dict],
        lexical_results: list[dict],
    ) -> list[dict]:
        """
        Merge two ranked lists using RRF.
        Each input item must have keys: chunk_id, document_text, metadata, distance.
        Returns merged list sorted by rrf_score descending.
        """
        scores: dict[str, float]  = {}
        registry: dict[str, dict] = {}

        def process(results: list[dict], source: str) -> None:
            for rank, item in enumerate(results):
                cid = item["chunk_id"]

                scores[cid] = scores.get(cid, 0.0) + self._rrf_score(rank)

                if cid not in registry:
                    registry[cid] = {
                        "chunk_id": item["chunk_id"],
                        "document_text": item["document_text"],
                        "metadata": item["metadata"],
                        "semantic_distance": None,
                        "lexical_distance": None,
                        "sources": set(),
                    }

                if source == "semantic":
                    registry[cid]["semantic_distance"] = item.get("distance")

                if source == "lexical":
                    registry[cid]["lexical_distance"] = item.get("distance")

                registry[cid]["sources"].add(source)

        process(semantic_results, "semantic")
        process(lexical_results,  "lexical")

        merged = []
        for cid, item in registry.items():
            rrf = scores[cid]
            merged.append({
                "chunk_id": item["chunk_id"],
                "document_text": item["document_text"],
                "metadata": item["metadata"],
                "semantic_distance": item.get("semantic_distance"),
                "lexical_distance": item.get("lexical_distance"),
                "rrf_score": round(rrf, 6),
                "sources": sorted(item["sources"]),
            })

        merged.sort(key=lambda x: x["rrf_score"], reverse=True)
        return merged

    # ------------------------------------------------------------------
    # Parse Chroma raw result → list[dict]
    # ------------------------------------------------------------------

    def _parse_chroma(self, raw: dict) -> list[dict]:
        ids       = (raw.get("ids")       or [[]])[0]
        docs      = (raw.get("documents") or [[]])[0]
        metas     = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]

        results = []
        for i, cid in enumerate(ids):
            results.append({
                "chunk_id":      cid,
                "document_text": docs[i]      if i < len(docs)      else "",
                "metadata":      metas[i]     if i < len(metas)     else {},
                "distance":      distances[i] if i < len(distances) else None,
            })
        return results

    # ------------------------------------------------------------------
    # Build where clause for Chroma
    # ------------------------------------------------------------------

    def _build_where(
        self,
        allowed_doc_ids: list[str] | None,
        oui_ids: list[str] | None,
    ) -> dict | None:
        if allowed_doc_ids:
            return {"document_id": {"$in": allowed_doc_ids}}
        return None
    
    def _post_filter_oui(
        self,
        results: list[dict],
        oui_ids: list[str] | None,
    ) -> list[dict]:
        """Filter sau khi retrieve: giữ chunk nếu document thuộc ít nhất 1 oui_id yêu cầu."""
        if not oui_ids:
            return results
        oui_set = set(oui_ids)
        filtered = []
        for chunk in results:
            # oui_id metadata là "abc,def,ghi"
            chunk_ouis = set(
                (chunk.get("metadata", {}).get("oui_id") or "").split(",")
            )
            chunk_ouis.discard("")
            if chunk_ouis & oui_set:  # intersection
                filtered.append(chunk)
        return filtered
    
    def _classify_chunk_sensitivity(self, chunk: dict) -> SensitivityLevel:
        """
        Classify độ nhạy của chunk dựa trên:
        1. metadata.sensitivity (nếu đã được tag khi ingest)
        2. Regex scan nội dung (fallback)
        """
        # Ưu tiên metadata nếu có
        meta_sensitivity = chunk.get("metadata", {}).get("sensitivity")
        if meta_sensitivity:
            try:
                return SensitivityLevel[meta_sensitivity.upper()]
            except KeyError:
                pass

        # Fallback: scan nội dung
        text = chunk.get("document_text", "")
        for level in [SensitivityLevel.RESTRICTED,
                    SensitivityLevel.CONFIDENTIAL,
                    SensitivityLevel.INTERNAL]:
            for pattern in SENSITIVITY_PATTERNS.get(level, []):
                if re.search(pattern, text, re.IGNORECASE):
                    return level

        return SensitivityLevel.PUBLIC


    # Trong _apply_sensitivity_gate, thay toàn bộ hàm:
    def _apply_sensitivity_gate(self, chunks: list[dict], user) -> list[dict]:
        if user is None:
            return [c for c in chunks
                    if self._classify_chunk_sensitivity(c) == SensitivityLevel.PUBLIC]

        max_clearance = getattr(user, "max_clearance", 1)
        # clearance 1-5 map sang SensitivityLevel 1-5 trực tiếp
        max_level = SensitivityLevel(max_clearance)
        allowed: list[dict] = []

        for chunk in chunks:
            chunk_level = self._classify_chunk_sensitivity(chunk)
            if chunk_level > max_level:
                logger.warning(
                    "SENSITIVITY GATE: blocked chunk=%s level=%s user=%s clearance=%d",
                    chunk.get("chunk_id"), chunk_level.name,
                    getattr(user, "id", "?"), max_clearance,
                )
                continue
            allowed.append(chunk)

        return allowed


    def _redact_for_director(self, chunk: dict) -> dict:
        """
        Director được xem CONFIDENTIAL nhưng một số field
        trong RESTRICTED patterns vẫn bị redact.
        """
        REDACT_PATTERNS = [
            (r"\b\d{1,3}(?:[.,]\d{3})+\s*(?:VND|đồng)\b", "[SỐ TIỀN ĐÃ ẨN]"),
            (r"\b(?:\+84|0)(?:3[2-9]|5[6-9]|7[0-9]|8[0-9]|9[0-9])[\s\-]?\d{3}[\s\-]?\d{3}\b",
            "[SĐT ĐÃ ẨN]"),
            (r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "[EMAIL ĐÃ ẨN]"),
            (r"\b\d{9}(?:\d{3})?\b", "[CCCD ĐÃ ẨN]"),
        ]
        new_chunk = dict(chunk)
        text = new_chunk.get("document_text", "")
        for pattern, replacement in REDACT_PATTERNS:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        new_chunk["document_text"]    = text
        new_chunk["_director_redacted"] = True
        return new_chunk

    # ------------------------------------------------------------------
    # Main retrieve
    # ------------------------------------------------------------------

    def retrieve(
        self,
        *,
        query: str,
        user=None,
        top_k: int = 5,
        mode: str = "hybrid",
        oui_ids: list[str] | None = None,
        chat_mode: str = "rag",
    ) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []

        if not embedding_service.is_configured():
            raise RuntimeError("Embedding service is not configured")

        # Lấy user_id sớm để tránh DetachedInstanceError
        user_id = str(user.id) if user else None

        if chat_mode == "gmail":
            return self._retrieve_from_collection(
                query=query, user=user, top_k=top_k,
                collection_name=GMAIL_CHROMA_COLLECTION,
                extra_where={"user_id": {"$eq": user_id}} if user_id else None,  # ← $eq operator
            )
        elif chat_mode == "all":
            rag_results = self._retrieve_main(query=query, user=user, top_k=top_k, oui_ids=oui_ids)
            gmail_results = self._retrieve_from_collection(
                query=query, user=user, top_k=top_k,
                collection_name=GMAIL_CHROMA_COLLECTION,
                extra_where={"user_id": {"$eq": user_id}} if user_id else None,  # ← $eq operator
            )
            merged = rag_results + gmail_results
            merged.sort(key=lambda x: x.get("score", 0), reverse=True)
            return merged[:top_k]
        else:
            return self._retrieve_main(
                query=query, user=user, top_k=top_k, mode=mode, oui_ids=oui_ids
            )

    def _retrieve_main(
        self,
        *,
        query: str,
        user=None,
        top_k: int = 5,
        mode: str = "hybrid",
        oui_ids: list[str] | None = None,
    ) -> list[dict]:
        """Retrieve từ collection chính (document_chunks)."""
        allowed_doc_ids: list[str] | None = None
        if user is not None:
            allowed_doc_ids = fga_adapter.list_viewable_document_ids(user.id)
            if not allowed_doc_ids:
                return []

        where = self._build_where(allowed_doc_ids, oui_ids)

        semantic_list: list[dict] = []
        lexical_list: list[dict] = []

        if mode in ("semantic", "hybrid"):
            emb = embedding_service.embed(query)
            raw = self.repo.query_by_embedding(
                embedding=emb, top_k=self.semantic_candidates, where=where,
            )
            semantic_list = self._parse_chroma(raw)

        if mode in ("keyword", "hybrid"):
            raw = self.repo.query_by_keyword(
                query=query, top_k=self.lexical_candidates, where=where,
            )
            lexical_list = self._parse_chroma(raw)

        if not semantic_list and not lexical_list:
            return []

        fused = self._fuse(semantic_list, lexical_list) if mode == "hybrid" else (
            sorted([{**i, "rrf_score": self._cosine_sim(i.get("distance")), "sources": ["semantic"]}
                    for i in semantic_list], key=lambda x: x["rrf_score"], reverse=True)
            if mode == "semantic" else
            sorted([{**i, "rrf_score": max(0.0, 1.0 - float(i.get("distance") or 1.0)), "sources": ["lexical"]}
                    for i in lexical_list], key=lambda x: x["rrf_score"], reverse=True)
        )

        max_rrf = fused[0]["rrf_score"] if fused else 0.0

        results = []
        for item in fused:
            sem = self._cosine_sim(item.get("semantic_distance")) if item.get("semantic_distance") is not None else None
            kw = max(0.0, min(1.0, 1.0 - float(item["lexical_distance"]))) if item.get("lexical_distance") is not None else None
            score = round(item["rrf_score"] / max_rrf, 6) if max_rrf else 0.0
            if score < self.minimum_score:
                continue
            results.append({
                "chunk_id": item["chunk_id"],
                "document_text": item["document_text"],
                "metadata": item["metadata"] or {},
                "score": round(score, 6),
                "semantic_score": round(sem, 6) if sem is not None else None,
                "keyword_score": round(kw, 6) if kw is not None else None,
                "sources": item.get("sources", []),
            })

        results = self._apply_sensitivity_gate(results, user)
        results = self._post_filter_oui(results, oui_ids)
        return results[:top_k]


    def _retrieve_from_collection(
        self,
        *,
        query: str,
        user=None,
        top_k: int = 5,
        collection_name: str = "gmail_chunks",
        extra_where: dict | None = None,
    ) -> list[dict]:
        """Retrieve từ một Chroma collection tùy chỉnh (gmail_chunks)."""
        import chromadb
        from app.core.config import settings

        client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        collection = client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )
        
        count = collection.count()
        logger.info("Gmail collection '%s' count=%d extra_where=%s", collection_name, count, extra_where)

        emb = embedding_service.embed(query)

        kwargs: dict = dict(
            query_embeddings=[emb],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        if extra_where:
            kwargs["where"] = extra_where

        try:
            raw = collection.query(**kwargs)
        except Exception as e:
            logger.warning("Gmail collection query failed: %s", e)
            return []

        parsed = self._parse_chroma(raw)
        results = []
        for item in parsed:
            score = self._cosine_sim(item.get("distance"))
            results.append({
                "chunk_id": item["chunk_id"],
                "document_text": item["document_text"],
                "metadata": {**(item["metadata"] or {}), "source": "gmail"},
                "score": round(score, 6),
                "semantic_score": round(score, 6),
                "keyword_score": None,
                "sources": ["semantic"],
            })

        return results


retrieval_service = RetrievalService(
    semantic_candidates=20,
    lexical_candidates=20,
    minimum_score=0.0,
)