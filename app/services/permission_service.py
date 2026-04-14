from app.models.document import Document
from app.models.user import User
from app.fga.adapter import fga_adapter


class PermissionService:
    EDIT_ROLES = {"department_manager", "director", "admin_auditor"}

    @staticmethod
    def _rank(level: str) -> int:
        return {
            "public": 1,
            "internal": 2,
            "confidential": 3,
            "restricted": 4,
            "top_secret": 5,
        }.get(level or "internal", 0)

    def can_view_document(self, user: User, doc: Document) -> tuple[bool, str]:
        if not fga_adapter.can_view(user.id, doc.id):
            return False, "fga_denied"
        return True, "ok"

    def can_create_document(self, user: User, department_id: str | None) -> tuple[bool, str]:
        # director và admin_auditor tạo được mọi loại doc
        if user.role in {"director", "admin_auditor"}:
            return True, "ok"

        # Doc chung công ty (không thuộc dept nào) — chỉ manager trở lên
        if department_id is None:
            if user.role == "department_manager":
                return True, "ok"
            return False, "only_managers_can_create_company_doc"

        # Doc thuộc dept — chỉ upload vào dept của mình
        if user.role in {"department_manager", "employee"}:
            if user.department_id == department_id:
                return True, "ok"
            return False, "department_scope_denied"

        return False, "role_or_scope_denied"

    def can_update_document(self, user: User, doc: Document) -> tuple[bool, str]:
        # Owner luôn được update doc của mình
        if doc.owner_user_id == user.id:
            return True, "ok"

        if not fga_adapter.can_edit(user.id, doc.id):
            return False, "fga_denied"

        if doc.status == "archived" and user.role != "admin_auditor":
            return False, "archived_denied"

        if self._rank(user.clearance_level) < self._rank(doc.sensitivity_level):
            return False, "clearance_denied"

        if user.role in self.EDIT_ROLES:
            return True, "ok"

        return False, "role_denied"


permission_service = PermissionService()