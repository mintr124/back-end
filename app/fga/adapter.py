from __future__ import annotations
import logging
from sqlalchemy.orm import Session

from app.fga.client import fga_client
from app.models.document import Document
from app.services.oui_tree_service import oui_tree_service

logger = logging.getLogger(__name__)

MAX_CLEARANCE = 5


class FGAAdapter:
    """
    Sync tuples vào OpenFGA dùng Conditional Tuples.

    Access rules:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  Doc thuộc OUI-X, sensitivity=S                                     │
    │  • Owner:                              can_view + can_edit          │
    │  • Member OUI-X, clearance ≥ S:       can_view  (viewer)           │
    │  • Member ancestor OUI, clearance ≥ S: can_view  (viewer)          │
    │  • Member ROOT OUI, clearance ≥ S:    can_view + can_edit (editor) │
    │  • Member descendant OUI (S=1 only):  can_view  (viewer)           │
    └─────────────────────────────────────────────────────────────────────┘

    Tuple counts per doc (bất kể độ sâu cây):
      1 (owner) + 1 (direct#member→viewer) + 1 (parent#ancestor_member→viewer) + 1 (root#member→editor)
      = tối đa 4 tuples, không enumerate từng ancestor.
    """

    # ── OUI membership ─────────────────────────────────────────────────────────

    def add_oui_member(self, user_id: str, oui_id: str) -> None:
        fga_client.write([
            {"user": f"user:{user_id}", "relation": "member", "object": f"oui:{oui_id}"}
        ])

    def remove_oui_member(self, user_id: str, oui_id: str) -> None:
        fga_client.delete([
            {"user": f"user:{user_id}", "relation": "member", "object": f"oui:{oui_id}"}
        ])

    def link_oui_parent(self, oui_id: str, parent_oui_id: str) -> None:
        fga_client.write([
            {"user": f"oui:{parent_oui_id}", "relation": "parent_oui", "object": f"oui:{oui_id}"}
        ])

    def unlink_oui_parent(self, oui_id: str, parent_oui_id: str) -> None:
        fga_client.delete([
            {"user": f"oui:{parent_oui_id}", "relation": "parent_oui", "object": f"oui:{oui_id}"}
        ])

    # ── Document sync ──────────────────────────────────────────────────────────

    def sync_document_tuples(self, db: Session, doc: Document) -> None:
        tuples: list[dict] = []
        doc_obj = f"document:{doc.id}"
        sensitivity = doc.sensitivity or 1
        required_clearance = min(sensitivity, MAX_CLEARANCE)
        condition = {
            "name": "clearance_sufficient",
            "context": {"required_clearance": required_clearance},
        }

        if doc.owner_user_id:
            tuples.append({
                "user": f"user:{doc.owner_user_id}",
                "relation": "owner",
                "object": doc_obj,
            })

        seen: set[tuple] = set()

        def _add(user_str: str, relation: str, cond: dict) -> None:
            key = (user_str, relation)
            if key not in seen:
                seen.add(key)
                tuples.append({
                    "user": user_str,
                    "relation": relation,
                    "object": doc_obj,
                    "condition": cond,
                })

        for oui in doc.ouis:
            # Direct OUI members → viewer
            _add(f"oui:{oui.id}#member", "viewer", condition)

            # Direct parents dùng ancestor_member → FGA resolve toàn bộ ancestor → viewer
            for parent_id in oui_tree_service.get_direct_parents(db, oui.id):
                _add(f"oui:{parent_id}#ancestor_member", "viewer", condition)

            # Chỉ root mới có quyền edit
            root_id = oui_tree_service.get_root_oui_id(db, oui.id)
            if root_id != oui.id:
                _add(f"oui:{root_id}#member", "editor", condition)
            else:
                # Doc thuộc root: root members vừa viewer vừa editor — dùng editor để bao cả hai
                _add(f"oui:{root_id}#member", "editor", condition)

            # Descendant members → viewer chỉ khi public (sensitivity=1)
            if sensitivity == 1:
                pub_condition = {
                    "name": "clearance_sufficient",
                    "context": {"required_clearance": 1},
                }
                for desc_id in oui_tree_service.get_descendants(db, oui.id):
                    _add(f"oui:{desc_id}#member", "viewer", pub_condition)

        if tuples:
            fga_client.write(tuples)

    def delete_document_tuples(self, doc_id: str, tuples_to_delete: list[dict]) -> None:
        if tuples_to_delete:
            fga_client.delete(tuples_to_delete)

    def get_document_tuples(self, doc_id: str) -> list[dict]:
        return fga_client.read(object=f"document:{doc_id}")

    def list_viewable_document_ids(self, user_id: str, user_clearance: int) -> list[str]:
        objects = fga_client.list_objects(
            user=f"user:{user_id}",
            relation="can_view",
            object_type="document",
            context={"user_clearance": user_clearance},
        )
        return [o.split(":", 1)[1] for o in objects if o.startswith("document:")]

    # ── Check ──────────────────────────────────────────────────────────────────

    def can_view(self, user_id: str, doc_id: str, user_clearance: int) -> bool:
        return fga_client.check(
            user=f"user:{user_id}",
            relation="can_view",
            object=f"document:{doc_id}",
            context={"user_clearance": user_clearance},
        )

    def can_edit(self, user_id: str, doc_id: str, user_clearance: int) -> bool:
        return fga_client.check(
            user=f"user:{user_id}",
            relation="can_edit",
            object=f"document:{doc_id}",
            context={"user_clearance": user_clearance},
        )

    # ── Model deployment ───────────────────────────────────────────────────────

    def push_model(self) -> str:
        import json
        from pathlib import Path
        model_path = Path(__file__).parent / "model.json"
        model = json.loads(model_path.read_text(encoding="utf-8"))
        model_id = fga_client.write_model(model)
        logger.info("FGA model pushed: %s", model_id)
        return model_id


fga_adapter = FGAAdapter()
