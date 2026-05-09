from enum import IntEnum

class SensitivityLevel(IntEnum):
    PUBLIC       = 0   # ai cũng xem được
    INTERNAL     = 1   # nhân viên nội bộ
    CONFIDENTIAL = 2   # director trở lên (redact một phần)
    RESTRICTED   = 3   # admin_auditor only (full access)

# Role → mức tối đa được xem
ROLE_MAX_SENSITIVITY = {
    "admin_auditor": SensitivityLevel.RESTRICTED,
    "director":      SensitivityLevel.CONFIDENTIAL,
    "manager":       SensitivityLevel.INTERNAL,
    "employee":      SensitivityLevel.INTERNAL,
    "guest":         SensitivityLevel.PUBLIC,
}

# Patterns để classify chunk khi retrieve
SENSITIVITY_PATTERNS = {
    SensitivityLevel.RESTRICTED: [
        r"hồ\s+sơ\s+nhân\s+viên",
        r"TMG-EMP-\d+",
        r"mức\s+lương\s+cơ\s+bản",
        r"tổng\s+thu\s+nhập",
        r"lịch\s+sử\s+kỷ\s+luật",
        r"đánh\s+giá\s+nhân\s+viên",
        r"người\s+liên\s+hệ\s+khẩn\s+cấp",
        r"bảng\s+lương",
        r"thông\s+tin\s+tài\s+khoản\s+ngân\s+hàng",
        r"\d{1,3}(?:[.,]\d{3})+\s*(?:VND|đồng)",   # số tiền cụ thể
        r"số\s+cccd",
        r"địa\s+chỉ\s+thường\s+trú",
        r"chỗ\s+ở\s+hiện\s+tại",
    ],
    SensitivityLevel.CONFIDENTIAL: [
        r"doanh\s+thu\s+nội\s+bộ",
        r"ngân\s+sách\s+(?:bộ\s+phận|phòng|dự\s+án)",
        r"kế\s+hoạch\s+kinh\s+doanh",
        r"báo\s+cáo\s+tài\s+chính\s+nội\s+bộ",
        r"chiến\s+lược\s+(?:nội\s+bộ|công\s+ty)",
        r"kế\s+hoạch\s+nhân\s+sự",
        r"cắt\s+giảm\s+nhân\s+sự",
        r"danh\s+sách\s+(?:khách\s+hàng|đối\s+tác)",
    ],
    SensitivityLevel.INTERNAL: [
        r"nội\s+quy\s+(?:công\s+ty|lao\s+động)",
        r"quy\s+trình\s+nội\s+bộ",
        r"biên\s+bản\s+họp",
    ],
}