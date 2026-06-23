"""
domain_classifier.py
====================
LLM-based domain classifier.
Loads domain descriptions from DB (with 5-minute TTL cache).
Falls back to keyword matching if LLM is unavailable.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_domain_cache: dict[str, str] = {}   # {code: description}
_domain_cache_ts: float = 0.0
_DOMAIN_TTL = 300.0


def _get_domain_descriptions(db=None) -> dict[str, str]:
    global _domain_cache, _domain_cache_ts
    now = time.monotonic()
    if now - _domain_cache_ts < _DOMAIN_TTL and _domain_cache:
        return _domain_cache
    if db is None:
        return _domain_cache
    try:
        from app.repositories.policy_repository import policy_repository
        domains = policy_repository.list_domains(db, active_only=True)
        _domain_cache = {d.code: (d.description or d.name) for d in domains}
        _domain_cache_ts = now
        logger.debug("Domain description cache refreshed: %d domains", len(_domain_cache))
    except Exception as exc:
        logger.warning("Failed to refresh domain cache: %s", exc)
    return _domain_cache


def invalidate_domain_cache() -> None:
    global _domain_cache_ts
    _domain_cache_ts = 0.0


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DomainPrediction:
    domain_code: str
    confidence: float


@dataclass
class ClassificationResult:
    primary: DomainPrediction
    secondary: Optional[DomainPrediction]


# ── LLM classifier ────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "You are a document domain classifier for an enterprise RAG system. "
    "Given a text chunk and a list of business domains, identify which domains the chunk belongs to. "
    "Return JSON only: "
    "{\"primary_domain\": \"<code>\", \"primary_confidence\": <0..1>, "
    "\"secondary_domain\": \"<code or null>\", \"secondary_confidence\": <0..1 or null>}. "
    "No explanation."
)


class DomainClassifier:

    def classify(
        self,
        chunk_text: str,
        *,
        db=None,
        metadata_tags: list[str] | None = None,
        metadata_department: str | None = None,
        secondary_gap_threshold: float = 0.15,
    ) -> ClassificationResult:
        domain_descriptions = _get_domain_descriptions(db)

        if not domain_descriptions:
            return ClassificationResult(
                primary=DomainPrediction("GEN-00", 1.0),
                secondary=None,
            )

        # Try LLM classification first
        result = self._classify_llm(
            chunk_text, domain_descriptions, metadata_tags, metadata_department
        )
        if result:
            return result

        # Fallback: keyword/Jaccard similarity
        return self._classify_keyword(
            chunk_text, domain_descriptions, metadata_tags, metadata_department,
            secondary_gap_threshold,
        )

    def _classify_llm(
        self,
        chunk_text: str,
        domain_descriptions: dict[str, str],
        metadata_tags: list[str] | None,
        metadata_department: str | None,
    ) -> Optional[ClassificationResult]:
        try:
            from app.services.llm_service import llm_service
            if not llm_service.is_configured():
                return None

            domain_list = "\n".join(f"- {k}: {v}" for k, v in domain_descriptions.items())
            prompt = (
                f"Domain list:\n{domain_list}\n\n"
                f"Chunk text:\n\"\"\"{chunk_text[:800]}\"\"\"\n\n"
                f"Metadata: department={metadata_department or 'N/A'}, "
                f"tags={', '.join(metadata_tags or []) or 'N/A'}\n\n"
                "Classify into the most relevant domain(s)."
            )
            text, _, _ = llm_service.generate(
                prompt=prompt,
                system=_CLASSIFY_SYSTEM,
                max_tokens=128,
                temperature=0.0,
            )
            raw = _extract_json(text)
            primary_code = raw.get("primary_domain", "GEN-00")
            primary_conf = float(raw.get("primary_confidence", 0.5))
            sec_code     = raw.get("secondary_domain")
            sec_conf     = raw.get("secondary_confidence")

            # Validate codes
            if primary_code not in domain_descriptions:
                primary_code = "GEN-00"
            secondary = None
            if sec_code and sec_code in domain_descriptions and sec_conf is not None:
                secondary = DomainPrediction(sec_code, round(float(sec_conf), 3))

            return ClassificationResult(
                primary=DomainPrediction(primary_code, round(primary_conf, 3)),
                secondary=secondary,
            )
        except Exception as exc:
            logger.warning("LLM domain classification failed: %s", exc)
            return None

    def _classify_keyword(
        self,
        chunk_text: str,
        domain_descriptions: dict[str, str],
        metadata_tags: list[str] | None,
        metadata_department: str | None,
        secondary_gap_threshold: float,
    ) -> ClassificationResult:
        def tokenize(t: str) -> set[str]:
            return set(w.lower().strip(".,!?():;\"'") for w in t.split())

        combined = chunk_text + " " + " ".join(metadata_tags or [])
        a = tokenize(combined)

        scores: dict[str, float] = {}
        for code, desc in domain_descriptions.items():
            b = tokenize(desc)
            if not a or not b:
                scores[code] = 0.0
                continue
            inter = len(a & b)
            union = len(a | b)
            score = inter / union if union else 0.0
            if metadata_department and metadata_department.lower() in code.lower():
                score += 0.05
            scores[code] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        if not ranked or ranked[0][1] <= 0.0:
            return ClassificationResult(
                primary=DomainPrediction("GEN-00", 1.0),
                secondary=None,
            )

        top1_code, top1_score = ranked[0]
        top2_code, top2_score = ranked[1] if len(ranked) > 1 else (None, 0.0)

        secondary = None
        if top2_code and (top1_score - top2_score) < secondary_gap_threshold:
            secondary = DomainPrediction(top2_code, round(top2_score, 3))

        return ClassificationResult(
            primary=DomainPrediction(top1_code, round(top1_score, 3)),
            secondary=secondary,
        )


def _extract_json(text: str) -> dict:
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {}
