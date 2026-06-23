# RAG Policy-Contract Agent

Pipeline tạo Policy-Contract cho evidence chunk trong hệ thống RAG doanh nghiệp,
kèm Admin UI (Streamlit) để cấu hình rule động không cần sửa code.

## Cấu trúc file
- `rules_data.py` — Load 20 bộ Security-Rule + 4 Global Rule. **Tự động đọc từ
  `rules_store.json` nếu file đó tồn tại** (để đồng bộ với Admin UI); nếu không có,
  dùng data hardcode mặc định.
- `rules_store.json` — Nguồn dữ liệu rule runtime, được Admin UI đọc/ghi trực tiếp.
- `domain_classifier.py` — Xác định nghiệp vụ (domain) của chunk
- `risk_analyzer.py` — Tính effective_sensitivity (PII-aware) + Intent Risk Score
- `rule_selector.py` — Lấy candidate rule, chấm điểm, threshold selection, conflict resolution (deny-overrides)
- `policy_agent.py` — Agent điều phối toàn bộ pipeline + demo chạy thử
- `admin_ui.py` — **Streamlit Admin UI** để CRUD rule/domain động qua giao diện web
- `requirements.txt` — dependency cho Admin UI (`streamlit`)

## Chạy demo Agent
```bash
python3 policy_agent.py
```

## Chạy Admin UI
```bash
pip install -r requirements.txt
streamlit run admin_ui.py
```
Admin UI gồm 4 tab:
1. **Tổng quan** — xem bảng 20 domain, thêm domain mới
2. **Quản lý theo Domain** — sửa/xoá thông tin domain, thêm/sửa/xoá từng rule với form đầy đủ field
   (role, sensitivity, intent, action, priority, condition flags...) + preview JSON realtime
3. **Global Rules** — quản lý 4 rule áp dụng toàn hệ thống
4. **Import/Export** — export toàn bộ rule store ra JSON, import file JSON để ghi đè,
   khôi phục từ backup tự động (mỗi lần Lưu sẽ backup vào `rule_backups/`)

Mọi thay đổi qua Admin UI ghi trực tiếp vào `rules_store.json` → lần chạy tiếp theo của
`policy_agent.py` sẽ tự động dùng rule mới, **không cần sửa code hay deploy lại**.

Nếu chạy Agent dạng service (vd. trong FastAPI), gọi `rules_data.reload_rules()`
trước mỗi request để luôn lấy rule mới nhất mà không cần restart service.

## Tuỳ biến cho production
1. Thay `_similarity_score` trong `domain_classifier.py` bằng embedding model thật
   (sentence-transformers, hoặc dùng `build_llm_classifier_prompt()` để gọi Claude API).
2. Thay `detect_pii` trong `risk_analyzer.py` bằng NER thật (Presidio, spaCy NER, hoặc LLM-based).
3. Điều chỉnh `SCORE_THRESHOLD` và `MAX_RULES_WARN` trong `rule_selector.py` theo dữ liệu thực tế.
4. Thêm rule mới: dùng Admin UI, hoặc append trực tiếp vào `rules_store.json`.
5. Tích hợp logging/audit_log thật cho các rule có `"audit_log": True`.
6. Production nên thêm xác thực (login) cho Admin UI vì đây là trang quản trị nhạy cảm —
   Streamlit không có auth sẵn, cần đặt sau reverse-proxy (vd. OAuth2-proxy) hoặc dùng
   `streamlit-authenticator`.

