from __future__ import annotations
import logging
from sqlalchemy.orm import Session

from app.fga.client import fga_client
from app.models.document import Document
from app.models.user_oui_position import UserOuiPosition
from app.services.oui_tree_service import oui_tree_service

logger = logging.getLogger(__name__)


class FGAAdapter:
    """
    Sync tuples vào OpenFGA theo model OU/OUI/Position mới.

    Access rules:
    ┌─────────────────────────────────────────────────────────────────┐
    │  Doc thuộc OUI-X                                                │
    │  • User thuộc OUI-X:       có thể xem NẾU position.clearance  │
    │                             ≥ doc.sensitivity                   │
    │  • User thuộc ancestor:    luôn xem được (không check clearance)│
    │  • User thuộc descendant:  chỉ xem nếu doc.sensitivity = 1    │
    │                             (public) — check ở Python          │
    └─────────────────────────────────────────────────────────────────┘

    FGA chỉ quản lý structural access (thuộc OUI nào, ancestor nào).
    Clearance check thực hiện ở Python trong _sync_fga trước khi write tuple.
    """

    # ── OUI membership ────────────────────────────────────────────────────────

    def add_oui_member(self, user_id: str, oui_id: str) -> None:
        fga_client.write([
            {"user": f"user:{user_id}", "relation": "member", "object": f"oui:{oui_id}"}
        ])

    def remove_oui_member(self, user_id: str, oui_id: str) -> None:
        fga_client.delete([
            {"user": f"user:{user_id}", "relation": "member", "object": f"oui:{oui_id}"}
        ])

    def link_oui_parent(self, oui_id: str, parent_oui_id: str) -> None:
        """Liên kết OUI với OUI cha — để ancestor_member tự động inherit."""
        fga_client.write([
            {"user": f"oui:{parent_oui_id}", "relation": "parent_oui", "object": f"oui:{oui_id}"}
        ])

    def unlink_oui_parent(self, oui_id: str, parent_oui_id: str) -> None:
        fga_client.delete([
            {"user": f"oui:{parent_oui_id}", "relation": "parent_oui", "object": f"oui:{oui_id}"}
        ])

    # ── Document sync ─────────────────────────────────────────────────────────

    def sync_document_tuples(self, db: Session, doc: Document) -> None:
        from app.models.user_oui_position import UserOuiPosition

        tuples: list[dict] = []
        doc_obj = f"document:{doc.id}"

        # 1. Owner
        if doc.owner_user_id:
            tuples.append({"user": f"user:{doc.owner_user_id}", "relation": "owner", "object": doc_obj})

        # 2-4. Loop qua từng OUI doc thuộc về
        for oui in doc.ouis:
            # Ancestors → ancestor_viewer
            ancestor_ids = oui_tree_service.get_ancestors(db, oui.id)
            for anc_id in ancestor_ids:
                for m in db.query(UserOuiPosition).filter(UserOuiPosition.oui_id == anc_id).all():
                    t = {"user": f"user:{m.user_id}", "relation": "ancestor_viewer", "object": doc_obj}
                    if t not in tuples:
                        tuples.append(t)

            # Direct members → oui_member nếu clearance đủ
            for m in db.query(UserOuiPosition).filter(UserOuiPosition.oui_id == oui.id).all():
                if m.position and m.position.clearance >= doc.sensitivity:
                    t = {"user": f"user:{m.user_id}", "relation": "oui_member", "object": doc_obj}
                    if t not in tuples:
                        tuples.append(t)

            # Descendants → chỉ khi public (sensitivity = 1)
            if doc.sensitivity == 1:
                for desc_id in oui_tree_service.get_descendants(db, oui.id):
                    for m in db.query(UserOuiPosition).filter(UserOuiPosition.oui_id == desc_id).all():
                        t = {"user": f"user:{m.user_id}", "relation": "oui_member", "object": doc_obj}
                        if t not in tuples:
                            tuples.append(t)

        if tuples:
            fga_client.write(tuples)

    def delete_document_tuples(self, doc_id: str, tuples_to_delete: list[dict]) -> None:
        if tuples_to_delete:
            fga_client.delete(tuples_to_delete)

    def get_document_tuples(self, doc_id: str) -> list[dict]:
        return fga_client.read(object=f"document:{doc_id}")

    def list_viewable_document_ids(self, user_id: str) -> list[str]:
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