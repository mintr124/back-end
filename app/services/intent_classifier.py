"""
intent_classifier.py
====================
Lightweight intent classification for RAG queries.
Layer 1: keyword matching (fast, no API call)
Layer 2: LLM fallback if keyword matching returns "lookup" but query seems ambiguous

Intent classes:
  lookup     — Tra cứu thông tin cụ thể (default)
  aggregate  — Tổng hợp / thống kê
  export     — Xuất / tải dữ liệu
  compare    — So sánh hai hay nhiều thứ
  summarize  — Tóm tắt nội dung
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

INTENT_CLASSES = ["lookup", "aggregate", "export", "compare", "summarize"]

# ── Keyword map (Vietnamese + English) ────────────────────────────────────────
_KEYWORD_MAP: dict[str, list[str]] = {
    "export": [
        "xuất", "tải về", "tải xuống", "export", "download",
        "excel", "csv", "báo cáo xuất", "trích xuất toàn bộ",
    ],
    "aggregate": [
        "tổng cộng", "tổng hợp", "bao nhiêu", "thống kê", "đếm",
        "trung bình", "sum", "count", "aggregate", "thống kê",
        "tổng số", "bao gồm tất cả",
    ],
    "compare": [
        "so sánh", "khác nhau", "giống nhau", "phân biệt",
        "compare", "versus", "vs", "hơn hay kém", "khác biệt",
        "đối chiếu",
    ],
    "summarize": [
        "tóm tắt", "tóm lược", "tổng quan", "overview",
        "summarize", "summary", "ngắn gọn", "điểm chính",
        "ý chính", "nội dung chính",
    ],
}

# Patterns for ambiguous queries that benefit from LLM classification
_AMBIGUOUS_PATTERNS = re.compile(
    r"(?i)\b(toàn bộ|tất cả|mọi|hết|danh sách|list|all|every)\b"
)

_CLASSIFY_SYSTEM = (
    "Classify the intent of a user query for an enterprise knowledge base. "
    "Choose exactly one: lookup | aggregate | export | compare | summarize. "
    "Return JSON: {\"intent\": \"<class>\"}. No explanation."
)


class IntentClassifier:

    def classify(self, query: str, *, use_llm: bool = True) -> str:
        """
        Returns one of: lookup | aggregate | export | compare | summarize.
        Tries keyword matching first; if result is 'lookup' and query looks
        ambiguous, optionally calls LLM for confirmation.
        """
        keyword_intent = self._keyword_classify(query)

        if keyword_intent != "lookup":
            return keyword_intent

        if use_llm and _AMBIGUOUS_PATTERNS.search(query):
            llm_intent = self._llm_classify(query)
            if llm_intent:
                return llm_intent

        return "lookup"

    def _keyword_classify(self, query: str) -> str:
        q_lower = query.lower()
        for intent, keywords in _KEYWORD_MAP.items():
            if any(kw in q_lower for kw in keywords):
                return intent
        return "lookup"

    def _llm_classify(self, query: str) -> str | None:
        try:
            from app.services.llm_service import llm_service
            if not llm_service.is_configured():
                return None
            text, _, _ = llm_service.generate(
                prompt=f"Query: {query}",
                system=_CLASSIFY_SYSTEM,
                max_tokens=32,
                temperature=0.0,
            )
            import json, re
            m = re.search(r'\{[\s\S]*\}', text)
            if m:
                result = json.loads(m.group())
                intent = result.get("intent", "").lower().strip()
                if intent in INTENT_CLASSES:
                    return intent
        except Exception as exc:
            logger.debug("LLM intent classification failed: %s", exc)
        return None


intent_classifier = IntentClassifier()
