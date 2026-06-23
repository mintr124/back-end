"""
admin_ui.py
Streamlit Admin UI để quản lý động (CRUD) các Security-Rule cho RAG Policy-Contract Agent.

Schema dữ liệu khớp với rules_data.py / rule_selector.py:
- rule_sets: { domain_code: { name, base_sensitivity, rules: [rule...] } }
- global_rules: [rule...]
- domain_descriptions: { domain_code: description } (dùng cho domain classifier)

Lưu trữ: file JSON `rules_store.json` (tách data khỏi code để chỉnh sửa runtime
mà không cần deploy lại code Python).

Chạy: streamlit run admin_ui.py
"""

import json
import copy
import os
from datetime import datetime

import streamlit as st

STORE_PATH = "rules_store.json"
BACKUP_DIR = "rule_backups"

SENSITIVITY_LEVELS = ["Public", "Internal", "Confidential", "Restricted", "TopSecret"]
RISK_LEVELS = ["low", "medium", "high", "very_high"]
ACTIONS = ["ALLOW", "ALLOW_WITH_WATERMARK", "REDACT", "DENY"]
INTENTS = ["lookup", "aggregate", "export", "compare", "summarize"]
INTENT_RISK_SIGNALS = ["normal", "cross_dept", "bulk_extraction", "suspicious"]

# Các field "đặc thù" (condition flags) mà rule có thể có — hiển thị dạng checkbox/input riêng
CONDITION_FIELDS = {
    "cross_dept_only": {"type": "bool", "label": "Chỉ áp dụng khi truy cập cross-department"},
    "require_department": {"type": "text", "label": "Yêu cầu user.department =="},
    "not_owner_and_not_role": {"type": "list", "label": "Không phải owner VÀ không thuộc role (list, phẩy)"},
    "not_subject": {"type": "bool", "label": "Áp dụng khi user KHÔNG phải subject của tài liệu"},
    "subject_is_direct_report": {"type": "bool", "label": "Yêu cầu subject là direct-report của user"},
    "require_assigned_customer": {"type": "bool", "label": "Bỏ qua rule nếu user đã được assign khách hàng"},
    "require_pii_detected": {"type": "bool", "label": "Chỉ áp dụng khi phát hiện PII"},
    "require_intent_risk": {"type": "select", "label": "Yêu cầu intent_risk_signal ==", "options": INTENT_RISK_SIGNALS},
    "require_before_publish_date": {"type": "bool", "label": "Chỉ áp dụng trước publish_date"},
    "require_after_publish_date": {"type": "bool", "label": "Chỉ áp dụng sau publish_date"},
    "exclude_roles_from_block": {"type": "list", "label": "Role được loại trừ khỏi blocked_roles (list, phẩy)"},
}


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def load_store() -> dict:
    if not os.path.exists(STORE_PATH):
        return {"rule_sets": {}, "global_rules": [], "domain_descriptions": {}, "sensitivity_order": SENSITIVITY_LEVELS}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_store(data: dict, make_backup: bool = True):
    if make_backup and os.path.exists(STORE_PATH):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"rules_store_{ts}.json")
        with open(STORE_PATH, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def new_rule_id(domain_code: str, existing_rules: list) -> str:
    prefix = f"R-{domain_code.replace('-', '')}-"
    nums = []
    for r in existing_rules:
        rid = r.get("rule_id", "")
        if rid.startswith(prefix):
            try:
                nums.append(int(rid.split("-")[-1]))
            except ValueError:
                pass
    next_num = max(nums, default=0) + 1
    return f"{prefix}{next_num:02d}"


def validate_rule(rule: dict) -> list:
    """Trả về list lỗi (rỗng nếu hợp lệ)."""
    errors = []
    if not rule.get("rule_id"):
        errors.append("rule_id không được để trống")
    if not rule.get("name"):
        errors.append("Tên rule không được để trống")
    if rule.get("action") not in ACTIONS:
        errors.append(f"action phải thuộc {ACTIONS}")
    if rule.get("risk_level") not in RISK_LEVELS:
        errors.append(f"risk_level phải thuộc {RISK_LEVELS}")
    if rule.get("min_sensitivity") and rule["min_sensitivity"] not in SENSITIVITY_LEVELS:
        errors.append(f"min_sensitivity phải thuộc {SENSITIVITY_LEVELS}")
    priority = rule.get("priority")
    if priority is not None and not (0 <= priority <= 100):
        errors.append("priority phải trong khoảng 0-100")
    return errors


# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------
def render_rule_form(rule: dict, domain_code: str, key_prefix: str) -> dict:
    """Render form chỉnh sửa 1 rule, trả về dict rule đã cập nhật (chưa save)."""
    updated = copy.deepcopy(rule)

    col1, col2 = st.columns(2)
    with col1:
        updated["rule_id"] = st.text_input("Rule ID", value=rule.get("rule_id", ""), key=f"{key_prefix}_id", disabled=True)
        updated["name"] = st.text_input("Tên rule", value=rule.get("name", ""), key=f"{key_prefix}_name")
        updated["action"] = st.selectbox(
            "Action", ACTIONS, index=ACTIONS.index(rule.get("action", "DENY")) if rule.get("action") in ACTIONS else 0,
            key=f"{key_prefix}_action",
        )
        updated["risk_level"] = st.selectbox(
            "Risk level", RISK_LEVELS,
            index=RISK_LEVELS.index(rule.get("risk_level", "medium")) if rule.get("risk_level") in RISK_LEVELS else 1,
            key=f"{key_prefix}_risk",
        )
        updated["priority"] = st.slider("Priority (deny-overrides dùng giá trị cao hơn thắng)", 0, 100,
                                         value=int(rule.get("priority", 50)), key=f"{key_prefix}_priority")

    with col2:
        min_sens_options = ["(không yêu cầu)"] + SENSITIVITY_LEVELS
        cur_sens = rule.get("min_sensitivity") or "(không yêu cầu)"
        sens_choice = st.selectbox("Min sensitivity", min_sens_options,
                                    index=min_sens_options.index(cur_sens) if cur_sens in min_sens_options else 0,
                                    key=f"{key_prefix}_sens")
        updated["min_sensitivity"] = None if sens_choice == "(không yêu cầu)" else sens_choice

        updated["min_user_level"] = st.number_input("Min user level (0 = không yêu cầu)", min_value=0, max_value=10,
                                                      value=int(rule.get("min_user_level") or 0), key=f"{key_prefix}_lvl")
        if updated["min_user_level"] == 0:
            updated["min_user_level"] = None

        updated["mandatory"] = st.checkbox("Mandatory (luôn được thêm vào contract)",
                                            value=bool(rule.get("mandatory", False)), key=f"{key_prefix}_mand")
        updated["audit_log"] = st.checkbox("Ghi audit log khi áp dụng",
                                            value=bool(rule.get("audit_log", False)), key=f"{key_prefix}_audit")
        updated["alert_security_team"] = st.checkbox("Cảnh báo Security Team khi áp dụng",
                                                       value=bool(rule.get("alert_security_team", False)),
                                                       key=f"{key_prefix}_alert")

    st.markdown("**Roles**")
    col3, col4 = st.columns(2)
    with col3:
        applicable_str = st.text_input("Applicable roles (phẩy)",
                                        value=", ".join(rule.get("applicable_roles", [])),
                                        key=f"{key_prefix}_appl_roles")
        updated["applicable_roles"] = [r.strip() for r in applicable_str.split(",") if r.strip()] or None
    with col4:
        blocked_str = st.text_input("Blocked roles (phẩy)",
                                     value=", ".join(rule.get("blocked_roles", [])),
                                     key=f"{key_prefix}_blocked_roles")
        updated["blocked_roles"] = [r.strip() for r in blocked_str.split(",") if r.strip()] or None

    st.markdown("**Intents áp dụng** (để trống = mọi intent)")
    selected_intents = st.multiselect("Applicable intents", INTENTS,
                                       default=rule.get("applicable_intents", []),
                                       key=f"{key_prefix}_intents")
    updated["applicable_intents"] = selected_intents or None

    st.markdown("**Redaction fields** (nếu action = REDACT)")
    redact_str = st.text_input("Các field cần redact (phẩy)",
                                value=", ".join(rule.get("redaction_fields", [])),
                                key=f"{key_prefix}_redact")
    updated["redaction_fields"] = [r.strip() for r in redact_str.split(",") if r.strip()] or None

    with st.expander("⚙️ Điều kiện đặc thù (condition flags)"):
        for field_key, spec in CONDITION_FIELDS.items():
            cur_val = rule.get(field_key)
            if spec["type"] == "bool":
                updated[field_key] = st.checkbox(spec["label"], value=bool(cur_val), key=f"{key_prefix}_{field_key}")
                if not updated[field_key]:
                    updated[field_key] = None
            elif spec["type"] == "text":
                val = st.text_input(spec["label"], value=cur_val or "", key=f"{key_prefix}_{field_key}")
                updated[field_key] = val or None
            elif spec["type"] == "list":
                val = st.text_input(spec["label"], value=", ".join(cur_val or []), key=f"{key_prefix}_{field_key}")
                updated[field_key] = [v.strip() for v in val.split(",") if v.strip()] or None
            elif spec["type"] == "select":
                options = ["(không yêu cầu)"] + spec["options"]
                cur = cur_val or "(không yêu cầu)"
                choice = st.selectbox(spec["label"], options,
                                       index=options.index(cur) if cur in options else 0,
                                       key=f"{key_prefix}_{field_key}")
                updated[field_key] = None if choice == "(không yêu cầu)" else choice

    # Loại bỏ key có value None để JSON gọn
    return {k: v for k, v in updated.items() if v is not None}


def render_rule_preview(rule: dict):
    badge_color = {"ALLOW": "🟢", "ALLOW_WITH_WATERMARK": "🟡", "REDACT": "🟠", "DENY": "🔴"}
    icon = badge_color.get(rule.get("action"), "⚪")
    st.code(json.dumps(rule, ensure_ascii=False, indent=2), language="json")
    st.caption(f"{icon} action={rule.get('action')} | priority={rule.get('priority')} | "
               f"risk={rule.get('risk_level')} | mandatory={rule.get('mandatory', False)}")


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="RAG Policy-Contract — Admin UI", layout="wide")
    st.title("🛡️ Admin UI — Quản lý Security Rules (RAG Policy-Contract)")

    if "store" not in st.session_state:
        st.session_state.store = load_store()

    store = st.session_state.store

    tab_overview, tab_domain, tab_global, tab_import_export = st.tabs(
        ["📋 Tổng quan", "🏷️ Quản lý theo Domain", "🌐 Global Rules", "📦 Import / Export"]
    )

    # ---------------- TAB 1: Overview ----------------
    with tab_overview:
        st.subheader("Danh sách 20 Domain hiện có")
        rows = []
        for code, ruleset in store["rule_sets"].items():
            rows.append({
                "Domain": code,
                "Tên nghiệp vụ": ruleset.get("name", ""),
                "Base sensitivity": ruleset.get("base_sensitivity", ""),
                "Số rule": len(ruleset.get("rules", [])),
            })
        st.dataframe(rows, use_container_width=True)

        st.markdown("---")
        st.subheader("➕ Thêm domain mới")
        with st.form("add_domain_form"):
            c1, c2, c3 = st.columns(3)
            with c1:
                new_code = st.text_input("Domain code (VD: SEC-01)")
            with c2:
                new_name = st.text_input("Tên nghiệp vụ")
            with c3:
                new_base_sens = st.selectbox("Base sensitivity", SENSITIVITY_LEVELS, index=1)
            new_desc = st.text_area("Domain description (dùng cho classifier)")
            submitted = st.form_submit_button("Tạo domain")
            if submitted:
                if not new_code or not new_name:
                    st.error("Cần nhập đủ Domain code và Tên nghiệp vụ.")
                elif new_code in store["rule_sets"]:
                    st.error(f"Domain code '{new_code}' đã tồn tại.")
                else:
                    store["rule_sets"][new_code] = {
                        "name": new_name, "base_sensitivity": new_base_sens, "rules": []
                    }
                    store["domain_descriptions"][new_code] = new_desc or new_name
                    save_store(store)
                    st.success(f"Đã tạo domain {new_code}.")
                    st.rerun()

    # ---------------- TAB 2: Domain rule management ----------------
    with tab_domain:
        domain_codes = list(store["rule_sets"].keys())
        if not domain_codes:
            st.info("Chưa có domain nào. Hãy tạo ở tab Tổng quan.")
        else:
            selected_domain = st.selectbox("Chọn domain để quản lý", domain_codes)
            ruleset = store["rule_sets"][selected_domain]

            st.markdown(f"**Nghiệp vụ:** {ruleset.get('name')}  |  **Base sensitivity:** {ruleset.get('base_sensitivity')}")

            with st.expander("✏️ Sửa thông tin domain"):
                edited_name = st.text_input("Tên nghiệp vụ", value=ruleset.get("name", ""), key="edit_domain_name")
                edited_sens = st.selectbox("Base sensitivity", SENSITIVITY_LEVELS,
                                            index=SENSITIVITY_LEVELS.index(ruleset.get("base_sensitivity", "Internal")),
                                            key="edit_domain_sens")
                edited_desc = st.text_area("Description", value=store["domain_descriptions"].get(selected_domain, ""),
                                            key="edit_domain_desc")
                if st.button("💾 Lưu thông tin domain"):
                    ruleset["name"] = edited_name
                    ruleset["base_sensitivity"] = edited_sens
                    store["domain_descriptions"][selected_domain] = edited_desc
                    save_store(store)
                    st.success("Đã lưu.")
                    st.rerun()

                if st.button("🗑️ Xoá toàn bộ domain này", type="secondary"):
                    st.session_state["confirm_delete_domain"] = selected_domain

                if st.session_state.get("confirm_delete_domain") == selected_domain:
                    st.warning(f"Xác nhận xoá domain {selected_domain} và toàn bộ rule trong đó?")
                    cc1, cc2 = st.columns(2)
                    if cc1.button("✅ Xác nhận xoá"):
                        del store["rule_sets"][selected_domain]
                        store["domain_descriptions"].pop(selected_domain, None)
                        save_store(store)
                        del st.session_state["confirm_delete_domain"]
                        st.success("Đã xoá domain.")
                        st.rerun()
                    if cc2.button("❌ Huỷ"):
                        del st.session_state["confirm_delete_domain"]
                        st.rerun()

            st.markdown("---")
            st.subheader(f"Rules trong {selected_domain} ({len(ruleset['rules'])})")

            for idx, rule in enumerate(ruleset["rules"]):
                with st.expander(f"{rule['rule_id']} — {rule['name']}"):
                    col_form, col_preview = st.columns([2, 1])
                    with col_form:
                        updated_rule = render_rule_form(rule, selected_domain, key_prefix=f"{selected_domain}_{idx}")
                        errors = validate_rule(updated_rule)
                        if errors:
                            for e in errors:
                                st.error(e)
                        bcol1, bcol2 = st.columns(2)
                        if bcol1.button("💾 Lưu rule", key=f"save_{selected_domain}_{idx}", disabled=bool(errors)):
                            ruleset["rules"][idx] = updated_rule
                            save_store(store)
                            st.success("Đã lưu rule.")
                            st.rerun()
                        if bcol2.button("🗑️ Xoá rule", key=f"del_{selected_domain}_{idx}"):
                            ruleset["rules"].pop(idx)
                            save_store(store)
                            st.success("Đã xoá rule.")
                            st.rerun()
                    with col_preview:
                        st.markdown("**Preview JSON**")
                        render_rule_preview(updated_rule)

            st.markdown("---")
            st.subheader("➕ Thêm rule mới vào domain này")
            if st.button(f"Tạo rule mới cho {selected_domain}"):
                new_id = new_rule_id(selected_domain, ruleset["rules"])
                new_rule = {
                    "rule_id": new_id,
                    "name": "Rule mới (chưa đặt tên)",
                    "action": "DENY",
                    "risk_level": "medium",
                    "priority": 50,
                    "mandatory": False,
                }
                ruleset["rules"].append(new_rule)
                save_store(store)
                st.success(f"Đã tạo {new_id}. Mở rộng để chỉnh sửa.")
                st.rerun()

    # ---------------- TAB 3: Global rules ----------------
    with tab_global:
        st.subheader(f"Global Rules ({len(store['global_rules'])})")
        st.caption("Các rule này áp dụng cho MỌI domain, luôn có priority=100 theo thiết kế gốc, "
                    "dùng cho các ràng buộc xuyên suốt (PII, export limit, external block, rate limit...).")

        for idx, rule in enumerate(store["global_rules"]):
            with st.expander(f"{rule['rule_id']} — {rule['name']}"):
                col_form, col_preview = st.columns([2, 1])
                with col_form:
                    updated_rule = render_rule_form(rule, "GLOBAL", key_prefix=f"global_{idx}")
                    errors = validate_rule(updated_rule)
                    if errors:
                        for e in errors:
                            st.error(e)
                    bcol1, bcol2 = st.columns(2)
                    if bcol1.button("💾 Lưu", key=f"save_global_{idx}", disabled=bool(errors)):
                        store["global_rules"][idx] = updated_rule
                        save_store(store)
                        st.success("Đã lưu.")
                        st.rerun()
                    if bcol2.button("🗑️ Xoá", key=f"del_global_{idx}"):
                        store["global_rules"].pop(idx)
                        save_store(store)
                        st.success("Đã xoá.")
                        st.rerun()
                with col_preview:
                    render_rule_preview(updated_rule)

        st.markdown("---")
        if st.button("➕ Tạo Global Rule mới"):
            existing_ids = [r["rule_id"] for r in store["global_rules"]]
            n = len(existing_ids) + 1
            new_rule = {
                "rule_id": f"R-GLOBAL-NEW-{n:02d}",
                "name": "Global rule mới (chưa đặt tên)",
                "action": "DENY",
                "risk_level": "medium",
                "priority": 100,
                "mandatory": True,
            }
            store["global_rules"].append(new_rule)
            save_store(store)
            st.success("Đã tạo. Mở rộng để chỉnh sửa.")
            st.rerun()

    # ---------------- TAB 4: Import / Export ----------------
    with tab_import_export:
        st.subheader("Export toàn bộ rule store")
        export_json = json.dumps(store, ensure_ascii=False, indent=2)
        st.download_button("⬇️ Download rules_store.json", data=export_json,
                            file_name="rules_store.json", mime="application/json")

        st.markdown("---")
        st.subheader("Import rule store (ghi đè toàn bộ)")
        uploaded = st.file_uploader("Chọn file JSON", type=["json"])
        if uploaded is not None:
            try:
                new_data = json.load(uploaded)
                required_keys = {"rule_sets", "global_rules"}
                if not required_keys.issubset(new_data.keys()):
                    st.error(f"File JSON thiếu key bắt buộc: {required_keys}")
                else:
                    st.json(new_data, expanded=False)
                    if st.button("⚠️ Xác nhận ghi đè toàn bộ rule store"):
                        st.session_state.store = new_data
                        save_store(new_data)
                        st.success("Đã import thành công.")
                        st.rerun()
            except json.JSONDecodeError as e:
                st.error(f"File JSON không hợp lệ: {e}")

        st.markdown("---")
        st.subheader("📜 Lịch sử backup")
        if os.path.exists(BACKUP_DIR):
            backups = sorted(os.listdir(BACKUP_DIR), reverse=True)
            if backups:
                selected_backup = st.selectbox("Chọn bản backup để khôi phục", backups)
                if st.button("♻️ Khôi phục từ backup này"):
                    with open(os.path.join(BACKUP_DIR, selected_backup), "r", encoding="utf-8") as f:
                        restored = json.load(f)
                    st.session_state.store = restored
                    save_store(restored)
                    st.success(f"Đã khôi phục từ {selected_backup}.")
                    st.rerun()
            else:
                st.caption("Chưa có backup nào.")
        else:
            st.caption("Chưa có backup nào.")

    st.sidebar.markdown("## ℹ️ Hướng dẫn")
    st.sidebar.info(
        "- Mỗi lần Lưu sẽ tự backup file cũ vào `rule_backups/`.\n"
        "- `policy_agent.py` cần được sửa để đọc rule từ `rules_store.json` "
        "(thay vì import trực tiếp `rules_data.py`) để áp dụng thay đổi ngay lập tức.\n"
        "- Rule có `mandatory=True` sẽ luôn được agent thêm vào contract dù điểm thấp.\n"
        "- `priority` cao hơn thắng trong cùng 1 action khi resolve conflict."
    )
    st.sidebar.metric("Tổng số domain", len(store["rule_sets"]))
    st.sidebar.metric("Tổng số domain-rule", sum(len(r["rules"]) for r in store["rule_sets"].values()))
    st.sidebar.metric("Tổng số global-rule", len(store["global_rules"]))


if __name__ == "__main__":
    main()
