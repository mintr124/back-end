# Sensitivity scale: 1=public, 2=internal, 3=confidential, 4=restricted, 5=top_secret

MIN_SENSITIVITY = 1
MAX_SENSITIVITY = 5

SENSITIVITY_PATTERNS = {
    4: [  # restricted
        r"hồ\s+sơ\s+nhân\s+viên",
        r"TMG-EMP-\d+",
        r"mức\s+lương\s+cơ\s+bản",
        r"tổng\s+thu\s+nhập",
        r"lịch\s+sử\s+kỷ\s+luật",
        r"đánh\s+giá\s+nhân\s+viên",
        r"người\s+liên\s+hệ\s+khẩn\s+cấp",
        r"bảng\s+lương",
        r"thông\s+tin\s+tài\s+khoản\s+ngân\s+hàng",
        r"\d{1,3}(?:[.,]\d{3})+\s*(?:VND|đồng)",
        r"số\s+cccd",
        r"địa\s+chỉ\s+thường\s+trú",
        r"chỗ\s+ở\s+hiện\s+tại",
    ],
    3: [  # confidential
        r"doanh\s+thu\s+nội\s+bộ",
        r"ngân\s+sách\s+(?:bộ\s+phận|phòng|dự\s+án)",
        r"kế\s+hoạch\s+kinh\s+doanh",
        r"báo\s+cáo\s+tài\s+chính\s+nội\s+bộ",
        r"chiến\s+lược\s+(?:nội\s+bộ|công\s+ty)",
        r"kế\s+hoạch\s+nhân\s+sự",
        r"cắt\s+giảm\s+nhân\s+sự",
        r"danh\s+sách\s+(?:khách\s+hàng|đối\s+tác)",
    ],
    2: [  # internal
        r"nội\s+quy\s+(?:công\s+ty|lao\s+động)",
        r"quy\s+trình\s+nội\s+bộ",
        r"biên\s+bản\s+họp",
    ],
}