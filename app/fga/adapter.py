from __future__ import annotations

import logging

from app.fga.client import fga_client
from app.models.document import Document
from app.models.user import User

logger = logging.getLogger(__name__)


class FGAAdapter:
    """
    Mọi thay đổi quan hệ user↔dept, user↔project, document tạo mới
    đều gọi adapter này để sync tuples vào OpenFGA.

    Hierarchy: admin_auditor > director > department_manager > employee

    Access rules:
    - owner: luôn xem được & sửa được tài liệu của mình
    - admin_auditor: xem/sửa mọi tài liệu
    - director: xem mọi tài liệu (không phân biệt dept/project)
    - Không thuộc dept/project + public: tất cả mọi người
    - Không thuộc dept/project + non-public: admin_auditor + director
    - Trong dept + public: tất cả trong dept
    - Trong dept + non-public: dept_manager trong dept + admin_auditor + director
    - Trong project + public: tất cả trong project
    - Trong project + non-public: dept_manager của dept chứa project + admin_auditor + director
    """

    # ── department ────────────────────────────────────────────────────────────

    def add_dept_member(self, user_id: str, dept_id: str):
        try:
            fga_client.write([
                {"user": f"user:{user_id}", "relation": "member", "object": f"department:{dept_id}"}
            ])
        except Exception:
            pass

    def add_dept_manager(self, user_id: str, dept_id: str):
        try:
            fga_client.write([
                {"user": f"user:{user_id}", "relation": "manager", "object": f"department:{dept_id}"}
            ])
        except Exception:
            pass

    def remove_dept_member(self, user_id: str, dept_id: str):
        try:
            fga_client.delete([
                {"user": f"user:{user_id}", "relation": "member", "object": f"department:{dept_id}"}
            ])
        except Exception:
            pass

    def remove_dept_manager(self, user_id: str, dept_id: str):
        try:
            fga_client.delete([
                {"user": f"user:{user_id}", "relation": "manager", "object": f"department:{dept_id}"}
            ])
        except Exception:
            pass

    # ── project ───────────────────────────────────────────────────────────────

    def add_project_member(self, user_id: str, proj_id: str):
        fga_client.write([
            {"user": f"user:{user_id}", "relation": "member", "object": f"project:{proj_id}"}
        ])

    def add_project_director(self, user_id: str, proj_id: str):
        fga_client.write([
            {"user": f"user:{user_id}", "relation": "director", "object": f"project:{proj_id}"}
        ])

    def link_project_dept(self, proj_id: str, dept_id: str):
        """Liên kết project với department — để dept_manager tự động có quyền."""
        fga_client.write([
            {"user": f"department:{dept_id}", "relation": "department", "object": f"project:{proj_id}"}
        ])

    def remove_project_member(self, user_id: str, proj_id: str):
        fga_client.delete([
            {"user": f"user:{user_id}", "relation": "member", "object": f"project:{proj_id}"}
        ])

    def unlink_project_dept(self, proj_id: str, dept_id: str):
        """Xóa liên kết project-department cũ khi đổi department."""
        fga_client.delete([
            {"user": f"department:{dept_id}", "relation": "department", "object": f"project:{proj_id}"}
        ])

    # ── document ──────────────────────────────────────────────────────────────

    def sync_document_tuples(
        self,
        doc: Document,
        all_users: list[User],
        dept_users: list[User],
        project_users: list[User],
        dept_managers: list[User],
    ):
        tuples = []
        doc_obj = f"document:{doc.id}"

        CLEARANCE_RANK = {
            "public": 1, "internal": 2, "confidential": 3,
            "restricted": 4, "top_secret": 5,
        }
        doc_rank = CLEARANCE_RANK.get(doc.sensitivity_level or "public", 1)

        def has_clearance(u: User) -> bool:
            return CLEARANCE_RANK.get(u.clearance_level or "public", 1) >= doc_rank

        # ── Owner luôn có quyền xem & sửa tài liệu của mình ─────────────────
        if doc.owner_user_id:
            tuples.append({
                "user": f"user:{doc.owner_user_id}",
                "relation": "owner",
                "object": doc_obj,
            })

        # ── admin_auditor và director luôn có quyền ───────────────────────────
        for u in all_users:
            if u.role == "admin_auditor":
                tuples.append({"user": f"user:{u.id}", "relation": "admin_auditor", "object": doc_obj})
            elif u.role == "director":
                tuples.append({"user": f"user:{u.id}", "relation": "global_director", "object": doc_obj})

        # ── Không thuộc dept / project ────────────────────────────────────────
        if not doc.department_id and not doc.project_id:
            for u in all_users:
                if u.role not in ("admin_auditor", "director") and has_clearance(u):
                    tuples.append({
                        "user": f"user:{u.id}",
                        "relation": "public_viewer",
                        "object": doc_obj,
                    })
        # ── Thuộc department, không có project ───────────────────────────────
        elif doc.department_id and not doc.project_id:
            for u in dept_users:
                if u.role not in ("admin_auditor", "director") and has_clearance(u):
                    if u.role == "department_manager":
                        tuples.append({"user": f"user:{u.id}", "relation": "dept_manager", "object": doc_obj})
                    else:
                        tuples.append({"user": f"user:{u.id}", "relation": "dept_member", "object": doc_obj})

        # ── Thuộc project ─────────────────────────────────────────────────────
        elif doc.project_id:
            for u in project_users:
                if u.role not in ("admin_auditor", "director") and has_clearance(u):
                    if u.role == "department_manager":
                        tuples.append({"user": f"user:{u.id}", "relation": "project_dept_manager", "object": doc_obj})
                    else:
                        tuples.append({"user": f"user:{u.id}", "relation": "project_member", "object": doc_obj})
            # dept_manager của dept chứa project (có thể không trong project_users)
            for u in dept_managers:
                if u.role == "department_manager" and has_clearance(u):
                    if not any(t["user"] == f"user:{u.id}" and t["object"] == doc_obj for t in tuples):
                        tuples.append({"user": f"user:{u.id}", "relation": "project_dept_manager", "object": doc_obj})

        if tuples:
            logger.info("FGA write tuples: %s", tuples)
            fga_client.write(tuples)

    def delete_document_tuples(self, doc_id: str, tuples_to_delete: list[dict]):
        """Xóa toàn bộ tuples cũ của document trước khi sync lại."""
        if tuples_to_delete:
            fga_client.delete(tuples_to_delete)

    def get_document_tuples(self, doc_id: str) -> list[dict]:
        return fga_client.read(object=f"document:{doc_id}")

    def list_viewable_document_ids(self, user_id: str) -> list[str]:
        """Trả về list doc_id user được phép xem."""
        objects = fga_client.list_objects(
            user=f"user:{user_id}",
            relation="can_view",
            object_type="document",
        )
        return [o.split(":", 1)[1] for o in objects if o.startswith("document:")]

    # ── Check ─────────────────────────────────────────────────────────────────

    def can_view(self, user_id: str, doc_id: str) -> bool:
        return fga_client.check(
            user=f"user:{user_id}",
            relation="can_view",
            object=f"document:{doc_id}",
        )

    def can_edit(self, user_id: str, doc_id: str) -> bool:
        return fga_client.check(
            user=f"user:{user_id}",
            relation="can_edit",
            object=f"document:{doc_id}",
        )


fga_adapter = FGAAdapter()