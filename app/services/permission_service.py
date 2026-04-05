from app.models.document import Document
from app.models.user import User
from app.fga.adapter import fga_adapter


#TODO: check permission again and entrypoint for the OpenFGA, can change role-as-code or not?

class OpenFGAAdapter:
    def check(self, **_kwargs) -> bool:
        return True


class PermissionService:
    EDIT_ROLES = {"department_manager", "director", "admin_auditor"}

    def __init__(self, fga: OpenFGAAdapter | None = None):
        self.fga = fga or OpenFGAAdapter()

    @staticmethod
    def _rank(level: str) -> int:
        return {
            "public": 1,
            "internal": 2,
            "confidential": 3,
            "restricted": 4,
            "top_secret": 5,
        }.get(level or "internal", 0)

    # def can_view_document(self, user: User, doc: Document) -> tuple[bool, str]:
    #     if not self.fga.check(user=user, document=doc, action="view"):
    #         return False, "fga_denied"

    #     if self._rank(user.clearance_level) < self._rank(doc.sensitivity_level):
    #         return False, "clearance_denied"

    #     if user.role == "employee": #TODO: check if document in project then if user not in the project
    #         return (user.department_id == doc.department_id, "department_scope_denied" if user.department_id != doc.department_id else "ok")

    #     if user.role == "department_manager":
    #         return (user.department_id == doc.department_id, "department_scope_denied" if user.department_id != doc.department_id else "ok")

    #     if user.role in {"director", "admin_auditor"}:
    #         return True, "ok"

    #     return False, "role_denied"
    
    def can_view_document(self, user: User, doc: Document) -> tuple[bool, str]:
        # ── Use FGA ---
        if not fga_adapter.can_view(user.id, doc.id):
            return False, "fga_denied"

        return True, "ok"

    # def can_create_document(self, user: User, department_id: str) -> tuple[bool, str]:
    #     if not self.fga.check(user=user, department_id=department_id, action="create"):
    #         return False, "fga_denied"

    #     if user.role in {"director", "admin_auditor"}:
    #         return True, "ok"

    #     if user.role == "department_manager" and user.department_id == department_id:
    #         return True, "ok"

    #     return False, "role_or_scope_denied"
    
    def can_create_document(self, user: User, department_id: str) -> tuple[bool, str]:
        if user.role in {"director", "admin_auditor"}:
            return True, "ok"
        if user.role in {"department_manager", "employee"}:
            # chỉ upload vào dept của mình
            if user.department_id == department_id:
                return True, "ok"
            return False, "department_scope_denied"
        return False, "role_or_scope_denied"

    # def can_update_document(self, user: User, doc: Document) -> tuple[bool, str]:
    #     if not self.fga.check(user=user, document=doc, action="edit"):
    #         return False, "fga_denied"

    #     if doc.status == "archived" and user.role != "admin_auditor":
    #         return False, "archived_denied"

    #     if self._rank(user.clearance_level) < self._rank(doc.sensitivity_level):
    #         return False, "clearance_denied"

    #     if doc.allowed_roles and user.role not in doc.allowed_roles and user.role != "admin_auditor":
    #         return False, "allowed_roles_denied"

    #     if user.role == "department_manager" and user.department_id != doc.department_id:
    #         return False, "department_scope_denied"

    #     if user.role in self.EDIT_ROLES:
    #         return True, "ok"

    #     return False, "role_denied"
    
    def can_update_document(self, user: User, doc: Document) -> tuple[bool, str]:
        # ── Owner luôn được update doc của mình ──────────────────────────────
        if doc.owner_user_id == user.id:
            return True, "ok"

        # ── Use FGA ───
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