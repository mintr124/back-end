"""
LLM-based domain classifier with entity overlap scoring.

Pipeline per chunk:
  1. LLM assigns confidence score to every active domain
  2. Entity overlap: |detected_entity_types ∩ domain_entities| / |domain_entities|
  3. final_score = 0.4 × LLM_confidence + 0.6 × entity_overlap
  4. Return all domains with final_score >= DOMAIN_THRESHOLD, sorted by score desc
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DOMAIN_THRESHOLD = 0.3

_DOMAIN_TTL = 300.0  # 5-minute cache
_cache_lock = threading.Lock()

# ── Domain description cache ──────────────────────────────────────────────────
_domain_desc_cache: dict[str, str] = {}  # {code: description}
_domain_desc_ts: float = 0.0

# ── Domain entity type cache ──────────────────────────────────────────────────
_domain_entity_cache: dict[str, set[str]] = {}  # {code: {entity_type}}
_domain_entity_ts: float = 0.0


def _get_domain_descriptions(db=None) -> dict[str, str]:
    global _domain_desc_cache, _domain_desc_ts
    now = time.monotonic()
    if now - _domain_desc_ts < _DOMAIN_TTL and _domain_desc_cache:
        return _domain_desc_cache
    if db is None:
        return _domain_desc_cache
    with _cache_lock:
        if now - _domain_desc_ts < _DOMAIN_TTL and _domain_desc_cache:
            return _domain_desc_cache
        try:
            from app.repositories.policy_repository import policy_repository
            domains = policy_repository.list_domains(db, active_only=True)
            _domain_desc_cache = {d.code: (d.description or d.name) for d in domains}
            _domain_desc_ts = now
            logger.debug("Domain description cache refreshed: %d domains", len(_domain_desc_cache))
        except Exception as exc:
            logger.warning("Failed to refresh domain description cache: %s", exc)
    return _domain_desc_cache


def _get_domain_entity_types(db=None) -> dict[str, set[str]]:
    global _domain_entity_cache, _domain_entity_ts
    now = time.monotonic()
    if now - _domain_entity_ts < _DOMAIN_TTL and _domain_entity_cache:
        return _domain_entity_cache
    if db is None:
        return _domain_entity_cache
    with _cache_lock:
        if now - _domain_entity_ts < _DOMAIN_TTL and _domain_entity_cache:
            return _domain_entity_cache
        try:
            from app.repositories.policy_repository import policy_repository
            _domain_entity_cache = policy_repository.get_entity_types_grouped_by_domain(db)
            _domain_entity_ts = now
            logger.debug("Domain entity type cache refreshed: %d domains", len(_domain_entity_cache))
        except Exception as exc:
            logger.warning("Failed to refresh domain entity type cache: %s", exc)
    return _domain_entity_cache


def invalidate_domain_cache() -> None:
    global _domain_desc_ts, _domain_entity_ts
    _domain_desc_ts = 0.0
    _domain_entity_ts = 0.0


@dataclass
class DomainPrediction:
    domain_code: str
    confidence: float   # final_score combining LLM + entity overlap


_CLASSIFY_SYSTEM = (
    "You are a document domain classifier for an enterprise RAG system. "
    "Given a text chunk and business domain definitions, assign a confidence score (0.0–1.0) "
    "to EACH domain indicating how strongly the chunk belongs to it. "
    "Return JSON only: {\"<domain_code>\": <score>, ...} for ALL listed domain codes. "
    "Use 0.0 for clearly unrelated domains. No explanation, no extra keys."
)


class DomainClassifier:

    def classify(
        self,
        chunk_text: str,
        *,
        db=None,
        detected_entity_types: set[str] | None = None,
    ) -> list[DomainPrediction]:
        """Classify chunk into domains using LLM + entity overlap scoring.
        Returns all domains with final_score >= DOMAIN_THRESHOLD, sorted by score desc."""
        domain_descriptions = _get_domain_descriptions(db)
        domain_entities     = _get_domain_entity_types(db)

        if not domain_descriptions:
            return [DomainPrediction("GEN-00", 1.0)]

        detected = detected_entity_types or set()
        llm_scores = self._classify_llm(chunk_text, domain_descriptions)
        print(f"[CLASSIFIER] llm_scores: {llm_scores}")

        results: list[DomainPrediction] = []
        for code in domain_descriptions:
            llm_conf = llm_scores.get(code, 0.0)
            overlap  = _entity_overlap(detected, domain_entities.get(code, set()))
            final    = round(0.4 * llm_conf + 0.6 * overlap, 3)
            print(f"[CLASSIFIER]   {code}: llm={llm_conf:.2f} overlap={overlap:.2f} final={final:.3f} {'✓' if final >= DOMAIN_THRESHOLD else '✗'}")
            if final >= DOMAIN_THRESHOLD:
                results.append(DomainPrediction(code, final))

        if not results:
            # Fallback: pick the domain with the highest LLM score
            if llm_scores:
                best = max(llm_scores, key=lambda k: llm_scores[k])
                results = [DomainPrediction(best, round(llm_scores[best], 3))]
            else:
                results = [DomainPrediction("GEN-00", 1.0)]

        return sorted(results, key=lambda x: x.confidence, reverse=True)

    def _classify_llm(
        self, chunk_text: str, domain_descriptions: dict[str, str]
    ) -> dict[str, float]:
        """Ask LLM to score every domain; returns {domain_code: confidence 0-1}."""
        try:
            from app.services.llm_service import llm_service
            if not llm_service.is_configured():
                return {}

            domain_list = "\n".join(f"- {k}: {v}" for k, v in domain_descriptions.items())
            prompt = (
                f"Domain list:\n{domain_list}\n\n"
                f"Chunk text:\n\"\"\"{chunk_text[:800]}\"\"\"\n\n"
                "Score each domain code listed above."
            )
            text, _, _ = llm_service.generate(
                prompt=prompt,
                system=_CLASSIFY_SYSTEM,
                max_tokens=256,
                temperature=0.0,
            )
            raw = _extract_json(text)
            return {
                k: max(0.0, min(1.0, float(v)))
                for k, v in raw.items()
                if k in domain_descriptions and isinstance(v, (int, float))
            }
        except Exception as exc:
            logger.warning("LLM domain classification failed: %s", exc)
            return {}


def _entity_overlap(detected: set[str], domain_entities: set[str]) -> float:
    if not domain_entities:
        return 0.0
    return len(detected & domain_entities) / len(domain_entities)


def _extract_json(text: str) -> dict:
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {}
