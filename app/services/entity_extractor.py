"""
entity_extractor.py
====================
Hybrid entity extraction pipeline:
  Layer 1 (Regex)  → Structured PII (email, phone, national_id, ...)
  Layer 2 (GLiNER) → Free-text entities from labels defined in active DB domains
  Layer 3 (Rule)   → Boolean summary labels (has_pii, has_number, ...)

GLiNER labels are loaded dynamically from the database (DomainEntityType table).
A TTL cache refreshes the label list every 5 minutes to avoid per-request DB hits.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regex patterns (Layer 1) ──────────────────────────────────────────────────

REGEX_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\b0\d{2,3}[.\s]?\d{3}[.\s]?\d{3,4}\b"),
    "national_id": re.compile(
        r"(?:CCCD|CMND|Số CMND/CCCD|Số CMND|Số CCCD)[:\s/]*?(\d{9}|\d{12})", re.IGNORECASE
    ),
    "tax_id": re.compile(r"(?:mã số thuế)[^\d]{0,15}(\d{10,13})", re.IGNORECASE),
    "social_insurance": re.compile(
        r"(?:số BHXH|số sổ BHXH)[:\s]*?([A-Z]{2}\d{8,12}|\d{8,12})", re.IGNORECASE
    ),
    "bank_account": re.compile(r"(?:tài khoản|TK)[^\d]{0,20}(\d{9,16})", re.IGNORECASE),
    "dob": re.compile(
        r"(?:ngày sinh|sinh ngày|DOB)[:\s]*?(\d{1,2}/\d{1,2}/\d{2,4})", re.IGNORECASE
    ),
    "date_generic": re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    "money": re.compile(r"\b[\d.,]+\s?(?:VND|đồng|VNĐ)\b", re.IGNORECASE),
    "percentage": re.compile(r"\b\d{1,3}\s?%"),
}

# Boolean label rules (Layer 3)
PII_TYPES = {
    "full_name", "email", "phone", "national_id", "bank_account",
    "tax_id", "social_insurance", "dob", "address",
}
NUMBER_TYPES = {"money", "percentage"}

_CREDENTIAL_RE = re.compile(r"(?i)\b(mật khẩu|password|api[_\s]?key|token|secret|otp)\b")
_LEGAL_RE      = re.compile(r"(?i)\b(nghị định|thông tư|điều\s+\d+|luật|hợp đồng|quyết định số)\b")
_SENTIMENT_RE  = re.compile(r"(?i)\b(hài lòng|không hài lòng|lo ngại|bức xúc|tích cực|tiêu cực|phàn nàn|tuyệt vời|tệ)\b")
_STRATEGIC_RE  = re.compile(r"(?i)\b(chiến lược|kế hoạch mở rộng|sáp nhập|m&a|định hướng|roadmap)\b")
_CUSTOMER_RE   = re.compile(r"(?i)\b(khách hàng|CRM|hạng thành viên|đối tác)\b")
_INTERNAL_RE   = re.compile(r"(?i)\b(biên bản họp|thông báo nội bộ|ban lãnh đạo)\b")


# ── GLiNER lazy loader with TTL cache ─────────────────────────────────────────

_gliner_model = None
_gliner_lock  = threading.Lock()

def _get_gliner():
    global _gliner_model
    if _gliner_model is None:
        with _gliner_lock:
            if _gliner_model is None:
                try:
                    from gliner import GLiNER
                    logger.info("Loading GLiNER model urchade/gliner_multi-v2.1 ...")
                    _gliner_model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
                    logger.info("GLiNER model loaded.")
                except Exception as exc:
                    logger.error("Failed to load GLiNER: %s", exc)
                    _gliner_model = None
    return _gliner_model


# Label cache: refreshed every 5 minutes from DB
_label_cache: list[str] = []
_label_cache_ts: float  = 0.0
_LABEL_TTL = 300.0  # seconds


def _get_active_labels(db=None) -> list[str]:
    """Return GLiNER labels from active domains. Falls back to [] if DB unavailable."""
    global _label_cache, _label_cache_ts
    now = time.monotonic()
    if now - _label_cache_ts < _LABEL_TTL and _label_cache:
        return _label_cache

    if db is None:
        return _label_cache  # stale but acceptable

    try:
        from app.repositories.policy_repository import policy_repository
        entity_types = policy_repository.get_all_active_entity_types(db)
        labels = list({et.entity_type for et in entity_types})
        _label_cache = labels
        _label_cache_ts = now
        logger.debug("GLiNER label cache refreshed: %d labels", len(labels))
    except Exception as exc:
        logger.warning("Failed to refresh GLiNER labels: %s", exc)

    return _label_cache


def invalidate_label_cache() -> None:
    """Call after creating/deleting entity types to force cache refresh."""
    global _label_cache_ts
    _label_cache_ts = 0.0


# ── Layer 1: Regex extraction ─────────────────────────────────────────────────

def extract_structured_entities(text: str) -> list[dict]:
    results = []
    for label, pattern in REGEX_PATTERNS.items():
        for m in pattern.finditer(text):
            value = m.group(1) if m.groups() else m.group(0)
            results.append({
                "text":   value,
                "label":  label,
                "start":  m.start(1) if m.groups() else m.start(),
                "end":    m.end(1)   if m.groups() else m.end(),
                "score":  1.0,
                "source": "regex",
            })
    return results


# ── Layer 2: GLiNER extraction ────────────────────────────────────────────────

def extract_freetext_entities(text: str, labels: list[str], threshold: float = 0.3) -> list[dict]:
    if not labels:
        return []
    model = _get_gliner()
    if model is None:
        return []
    try:
        raw = model.predict_entities(text, labels, threshold=threshold)
        for e in raw:
            e["source"] = "gliner"
        return raw
    except Exception as exc:
        logger.error("GLiNER inference error: %s", exc)
        return []


# ── Layer 3: Boolean labels ───────────────────────────────────────────────────

def detect_boolean_labels(text: str, all_entities: list[dict]) -> dict[str, bool]:
    found_types = {e["label"] for e in all_entities}
    return {
        "has_pii":           bool(found_types & PII_TYPES),
        "has_number":        bool(found_types & NUMBER_TYPES),
        "has_credential":    bool(_CREDENTIAL_RE.search(text)),
        "has_legal":         bool(_LEGAL_RE.search(text)),
        "has_sentiment":     bool(_SENTIMENT_RE.search(text)),
        "has_strategic":     bool(_STRATEGIC_RE.search(text)),
        "has_customer":      bool(_CUSTOMER_RE.search(text)),
        "has_internal_comm": bool(_INTERNAL_RE.search(text)),
    }


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(
    text: str,
    *,
    db=None,
    gliner_threshold: float = 0.3,
) -> dict:
    """
    Returns:
      {
        "entities": [{"text", "label", "start", "end", "score", "source"}, ...],
        "labels":   {"has_pii": bool, "has_number": bool, ...},
        "entity_types": ["full_name", "email", ...]   # deduplicated types found
      }
    """
    structured = extract_structured_entities(text)
    gliner_labels = _get_active_labels(db)
    freetext = extract_freetext_entities(text, gliner_labels, threshold=gliner_threshold)

    # Merge: regex takes priority for same text span
    all_entities = structured + freetext
    booleans = detect_boolean_labels(text, all_entities)

    return {
        "entities":     all_entities,
        "labels":       booleans,
        "entity_types": list({e["label"] for e in all_entities}),
    }
