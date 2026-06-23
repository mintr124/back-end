"""
Hybrid Entity Extraction Pipeline cho văn bản doanh nghiệp tiếng Việt
======================================================================

Kiến trúc:
  Layer 1 (Regex)   -> Structured PII: email, phone, national_id,
                        bank_account, tax_id, social_insurance, dob
  Layer 2 (GLiNER)  -> Free-text entity: full_name, address, job_title,
                        department, organization, strategic/sentiment...
  Layer 3 (Rule)    -> Tổng hợp thành các nhãn boolean
                        (has_pii, has_number, has_credential, ...)

Lưu ý quan trọng (rút ra từ phân tích dataset thật):
  - has_number CHỈ true khi có số liệu TÀI CHÍNH/ĐỊNH LƯỢNG
    (tiền, %, số lượng kinh doanh) - KHÔNG tính phone/CCCD/ngày sinh/mã NV.
  - has_pii true khi có bất kỳ structured PII hoặc full_name/address.
"""

import re
from gliner import GLiNER

# ============================================================================
# LAYER 1: REGEX - Structured PII (chính xác hơn GLiNER cho các loại này)
# ============================================================================

REGEX_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),

    "phone": re.compile(r"\b0\d{2,3}[.\s]?\d{3}[.\s]?\d{3,4}\b"),

    # CCCD/CMND: thường đi kèm từ khóa "CCCD" hoặc "CMND"
    "national_id": re.compile(
        r"(?:CCCD|CMND|Số CMND/CCCD|Số CMND|Số CCCD)[:\s/]*?(\d{9}|\d{12})",
        re.IGNORECASE,
    ),

    # Mã số thuế cá nhân: đi kèm từ khóa "mã số thuế"
    "tax_id": re.compile(
        r"(?:mã số thuế)[^\d]{0,15}(\d{10,13})", re.IGNORECASE
    ),

    # Số BHXH: format VN + số, hoặc đi kèm "BHXH"
    "social_insurance": re.compile(
        r"(?:số BHXH|số sổ BHXH)[:\s]*?([A-Z]{2}\d{8,12}|\d{8,12})",
        re.IGNORECASE,
    ),

    # Số tài khoản ngân hàng: đi kèm "tài khoản"
    "bank_account": re.compile(
        r"(?:tài khoản|TK)[^\d]{0,20}(\d{9,16})", re.IGNORECASE
    ),

    # Ngày sinh: format dd/mm/yyyy đi kèm "ngày sinh" / "DOB" / "sinh"
    "dob": re.compile(
        r"(?:ngày sinh|sinh ngày|DOB)[:\s]*?(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE,
    ),

    # Ngày tháng chung (không gắn với DOB)
    "date_generic": re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),

    # Tiền VND
    "money": re.compile(r"\b[\d.,]+\s?(?:VND|đồng|VNĐ)\b", re.IGNORECASE),

    # Phần trăm
    "percentage": re.compile(r"\b\d{1,3}\s?%"),
}


def extract_structured_entities(text):
    """Trích xuất entity structured bằng regex, trả về list dict giống format GLiNER."""
    results = []
    for label, pattern in REGEX_PATTERNS.items():
        for m in pattern.finditer(text):
            value = m.group(1) if m.groups() else m.group(0)
            results.append({
                "text": value,
                "label": label,
                "start": m.start(1) if m.groups() else m.start(),
                "end": m.end(1) if m.groups() else m.end(),
                "score": 1.0,   # regex match -> confidence tuyệt đối
                "source": "regex",
            })
    return results


# ============================================================================
# LAYER 2: GLiNER - Free-text entity (cần hiểu ngữ cảnh)
# ============================================================================

model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")

GLINER_LABELS = [
    "full_name",        # Tên người
    "address",          # Địa chỉ
    "organization",     # Tên công ty
    "department",       # Phòng/ban
    "job_title",        # Chức danh
    "product_name",     # Tên sản phẩm
    "strategic_plan",   # Kế hoạch/chiến lược
    "sentiment_expression",  # Câu cảm xúc/đánh giá
    "contract_clause",  # Điều khoản hợp đồng
    "law_reference",    # Văn bản pháp luật
    "product_launch",
    "pricing_strategy",
    "revenue"
]


def extract_freetext_entities(text, threshold=0.3):
    raw = model.predict_entities(text, GLINER_LABELS, threshold=threshold)
    for e in raw:
        e["source"] = "gliner"
    return raw


# ============================================================================
# LAYER 3: RULE - Tổng hợp thành boolean labels (ĐÃ SỬA LOGIC has_number)
# ============================================================================

# Các entity type được tính là "PII"
PII_TYPES = {
    "full_name", "email", "phone", "national_id", "bank_account",
    "tax_id", "social_insurance", "dob", "address",
}

# Các entity type được tính vào has_number (CHỈ tài chính/định lượng)
NUMBER_TYPES = {"money", "percentage"}  # KHÔNG gồm phone/national_id/dob/date_generic

CREDENTIAL_KEYWORDS = re.compile(
    r"(?i)\b(mật khẩu|password|api[_\s]?key|token|secret|otp)\b"
)
LEGAL_KEYWORDS = re.compile(
    r"(?i)\b(nghị định|thông tư|điều\s+\d+|luật|hợp đồng|quyết định số)\b"
)
SENTIMENT_KEYWORDS = re.compile(
    r"(?i)\b(hài lòng|không hài lòng|lo ngại|bức xúc|tích cực|"
    r"tiêu cực|phàn nàn|tuyệt vời|tệ)\b"
)
STRATEGIC_KEYWORDS = re.compile(
    r"(?i)\b(chiến lược|kế hoạch mở rộng|sáp nhập|m&a|định hướng|roadmap)\b"
)
CUSTOMER_KEYWORDS = re.compile(
    r"(?i)\b(khách hàng|CRM|hạng thành viên|đối tác)\b"
)
INTERNAL_COMM_KEYWORDS = re.compile(
    r"(?i)\b(biên bản họp|thông báo nội bộ|ban lãnh đạo)\b"
)


def detect_boolean_labels(text, all_entities):
    found_types = {e["label"] for e in all_entities}

    return {
        "has_pii": bool(found_types & PII_TYPES),

        "has_number": bool(found_types & NUMBER_TYPES),

        "has_credential": bool(CREDENTIAL_KEYWORDS.search(text)),

        "has_legal": bool(LEGAL_KEYWORDS.search(text)),

        "has_sentiment": bool(SENTIMENT_KEYWORDS.search(text)),

        "has_strategic": bool(STRATEGIC_KEYWORDS.search(text)),

        # has_customer: cần phân biệt "khách hàng" (CRM data) vs nhân viên nội bộ
        # -> chỉ true nếu có keyword khách hàng VÀ KHÔNG có dấu hiệu nội bộ rõ
        "has_customer": bool(CUSTOMER_KEYWORDS.search(text)),

        "has_internal_comm": bool(INTERNAL_COMM_KEYWORDS.search(text)),
    }


# ============================================================================
# PIPELINE TỔNG HỢP
# ============================================================================

def run_pipeline(text, gliner_threshold=0.3):
    structured = extract_structured_entities(text)
    freetext = extract_freetext_entities(text, threshold=gliner_threshold)

    # Loại bỏ trùng lặp: nếu GLiNER và regex bắt cùng 1 span, giữ regex (ưu tiên)
    all_entities = structured + freetext

    booleans = detect_boolean_labels(text, all_entities)

    return {
        "entities": all_entities,
        "labels": booleans,
    }


# ============================================================================
# TEST TRÊN CÁC SAMPLE THỰC TẾ
# ============================================================================

samples = [
    {
        "id": "sample_00000",
        "text": "KẾ HOẠCH RA MẮT SẢN PHẨM — VINFAST VF9 ELECTRIC FLEET EDITION\nTimeline: soft launch cho khách hàng doanh nghiệp ngày 15/07/2025, mass market launch ngày 01/09/2025. Chiến lược giá: phân khúc premium 1,89 tỷ VND, thấp hơn Tesla Model Y 23% nhưng cao hơn BYD Seal 15%. Bundle đặc biệt cho fleet: mua từ 10 xe tặng trạm sạc AC 22kW. Kênh phân phối ưu tiên: trực tiếp qua website và showroom flagship, hạn chế qua đại lý để kiểm soát trải nghiệm khách hàng. Mục tiêu năm 2025: 2.500 xe bán ra, doanh thu 4.725 tỷ VND.",
    },
]

for sample in samples:
    print(f"\n{'='*70}\n{sample['id']}\n{'='*70}")
    result = run_pipeline(sample["text"])

    print("\n-- Entities --")
    for e in sorted(result["entities"], key=lambda x: x.get("start", 0)):
        print(f"  [{e['source']:6s}] {e['text']!r:35s} => {e['label']}")