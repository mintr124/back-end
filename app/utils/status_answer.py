NO_ANSWER_PATTERNS = [
    "không đủ nguồn",
    "không có đủ thông tin",
    "không tìm thấy",
    "không thể trả lời",
    "i don't have enough information",
    "i cannot answer",
    "not enough information",
]

def is_no_answer(text: str | None) -> bool:
    if not text:
        return True

    t = text.strip().lower()
    return any(p in t for p in NO_ANSWER_PATTERNS)
