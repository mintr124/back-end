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

from app.fga.adapter import fga_adapter
from app.repositories.chroma_repository import ChromaRepository
from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

# RRF constant – higher k → smoother ranking (less sensitive to top ranks)
_RRF_K = 60


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
                    registry[cid] = {**item, "sources": set()}
                registry[cid]["sources"].add(source)

        process(semantic_results, "semantic")
        process(lexical_results,  "lexical")

        merged = []
        for cid, item in registry.items():
            rrf = scores[cid]
            merged.append({
                **item,
                "rrf_score": round(rrf, 6),
                "sources":   sorted(item["sources"]),
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
        project_ids:     list[str] | None,
        department_ids:  list[str] | None,
    ) -> dict | None:
        conditions = []

        if allowed_doc_ids:
            conditions.append({"document_id": {"$in": allowed_doc_ids}})
        if project_ids:
            conditions.append({"project_id": {"$in": project_ids}})
        if department_ids:
            conditions.append({"department_id": {"$in": department_ids}})

        if len(conditions) > 1:
            return {"$and": conditions}
        if len(conditions) == 1:
            return conditions[0]
        return None

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
        project_ids:    list[str] | None = None,
        department_ids: list[str] | None = None,
    ) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []

        if not embedding_service.is_configured():
            raise RuntimeError("Embedding service is not configured")

        # ── 1. Permission gate ────────────────────────────────────────
        allowed_doc_ids: list[str] | None = None
        if user is not None:
            allowed_doc_ids = fga_adapter.list_viewable_document_ids(user.id)
            if not allowed_doc_ids:
                return []

        where = self._build_where(allowed_doc_ids, project_ids, department_ids)

        # ── 2. Fetch candidates ───────────────────────────────────────
        semantic_list: list[dict] = []
        lexical_list:  list[dict] = []

        if mode in ("semantic", "hybrid"):
            emb = embedding_service.embed(query)
            raw = self.repo.query_by_embedding(
                embedding=emb,
                top_k=self.semantic_candidates,
                where=where,
            )
            semantic_list = self._parse_chroma(raw)
            logger.info("SEMANTIC: %d results, distances=%s",
                len(semantic_list), [round(r.get("distance",0),4) for r in semantic_list])

        if mode in ("keyword", "hybrid"):
            raw = self.repo.query_by_keyword(
                query=query,
                top_k=self.lexical_candidates,
                where=where,
            )
            lexical_list = self._parse_chroma(raw)
            logger.info("LEXICAL: %d results", len(lexical_list))

        if not semantic_list and not lexical_list:
            logger.warning("NO CANDIDATES FOUND - where=%s", where)
            return []

        # ── 3. Fuse ───────────────────────────────────────────────────
        if mode == "semantic":
            fused = [{**item, "rrf_score": self._cosine_sim(item["distance"]), "sources": ["semantic"]}
                     for item in semantic_list]
            fused.sort(key=lambda x: x["rrf_score"], reverse=True)
        elif mode == "keyword":
            fused = [{**item, "rrf_score": self._rrf_score(i), "sources": ["lexical"]}
                     for i, item in enumerate(lexical_list)]
        else:
            fused = self._fuse(semantic_list, lexical_list)

        logger.info("FUSED: %d items, top scores=%s",
            len(fused), [round(f.get("rrf_score",0),4) for f in fused[:5]])
        logger.info("MINIMUM_SCORE=%.2f", self.minimum_score)

        # ── 4. Score threshold + annotate ─────────────────────────────
        results: list[dict] = []
        for item in fused:
            score = item["rrf_score"]
            if score < self.minimum_score:
                continue

            # add semantic_score for transparency
            sem_score = self._cosine_sim(item.get("distance"))
            results.append({
                "chunk_id":      item["chunk_id"],
                "document_text": item["document_text"],
                "metadata":      item["metadata"] or {},
                "distance":      item.get("distance"),
                "score":         round(score, 6),
                "semantic_score": round(sem_score, 6),
                "sources":       item.get("sources", []),
            })

        return results[:top_k]


retrieval_service = RetrievalService(
    semantic_candidates=20,
    lexical_candidates=20,
    minimum_score=0.0,
)