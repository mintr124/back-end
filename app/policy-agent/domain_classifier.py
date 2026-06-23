"""
domain_classifier.py
Xác định domain (nghiệp vụ) của 1 evidence chunk.

Triển khai demo dùng keyword/embedding-similarity đơn giản (không cần GPU/model lớn).
Trong production: thay hàm `_similarity_score` bằng embedding model thật
(ví dụ: sentence-transformers, hoặc gọi Claude API để classify few-shot).
"""

from dataclasses import dataclass
from typing import List
from rules_data import DOMAIN_DESCRIPTIONS


@dataclass
class DomainPrediction:
    domain_code: str
    confidence: float


@dataclass
class ClassificationResult:
    primary: DomainPrediction
    secondary: DomainPrediction | None  # None nếu không có domain phụ đáng kể


def _tokenize(text: str) -> set:
    return set(w.lower().strip(".,!?():;\"'") for w in text.split())


def _similarity_score(chunk_text: str, domain_desc: str) -> float:
    """
    Demo: Jaccard similarity giữa token của chunk và domain description.
    PRODUCTION: thay bằng cosine similarity giữa embedding(chunk) và embedding(domain_desc),
    hoặc gọi LLM classifier few-shot để có độ chính xác cao hơn.
    """
    a, b = _tokenize(chunk_text), _tokenize(domain_desc)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class DomainClassifier:
    def __init__(self, domain_descriptions: dict = None):
        self.domain_descriptions = domain_descriptions or DOMAIN_DESCRIPTIONS

    def classify(
        self,
        chunk_text: str,
        metadata_tags: List[str] = None,
        metadata_department: str = None,
        secondary_gap_threshold: float = 0.15,
    ) -> ClassificationResult:
        """
        Trả về domain chính + domain phụ (nếu confidence gap nhỏ).
        metadata_tags / metadata_department dùng làm "prior" cộng điểm,
        không quyết định tuyệt đối.
        """
        scores = {}
        combined_text = chunk_text + " " + " ".join(metadata_tags or [])

        for domain_code, desc in self.domain_descriptions.items():
            score = _similarity_score(combined_text, desc)

            # Prior boost nếu department metadata khớp tên domain (rất nhẹ, +0.05)
            if metadata_department and metadata_department.lower() in domain_code.lower():
                score += 0.05

            scores[domain_code] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        # fallback nếu mọi domain đều có điểm ~0 -> GEN-00
        if ranked[0][1] <= 0.0:
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


# ---------------------------------------------------------------------------
# Gợi ý production: dùng LLM (Claude) làm classifier chính xác hơn keyword-based
# ---------------------------------------------------------------------------
CLASSIFIER_PROMPT_TEMPLATE = """Bạn là bộ phân loại nghiệp vụ cho hệ thống RAG doanh nghiệp.
Dưới đây là danh sách domain hợp lệ và mô tả:

{domain_list}

Chunk nội dung cần phân loại:
\"\"\"{chunk_text}\"\"\"

Metadata: department={department}, tags={tags}

Hãy trả về JSON CHỈ với format:
{{"primary_domain": "<code>", "primary_confidence": <0..1>,
  "secondary_domain": "<code or null>", "secondary_confidence": <0..1 or null>}}
Không thêm giải thích, không thêm text khác.
"""


def build_llm_classifier_prompt(chunk_text: str, department: str, tags: List[str]) -> str:
    domain_list = "\n".join(f"- {k}: {v}" for k, v in DOMAIN_DESCRIPTIONS.items())
    return CLASSIFIER_PROMPT_TEMPLATE.format(
        domain_list=domain_list,
        chunk_text=chunk_text,
        department=department,
        tags=", ".join(tags or []),
    )
