"""
FGA adapter. Translates domain operations (document sync, OUI membership,
permission checks) into OpenFGA tuple writes and reads.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.fga.client import fga_client
from app.models.document import Document
from app.services.oui_tree_service import oui_tree_service

logger = logging.getLogger(__name__)

# Upper bound for sensitivity-based clearance conditions written to FGA.
MAX_CLEARANCE = 5


class FGAAdapter:
    """
    Syncs relationship tuples into OpenFGA using Conditional Tuples.

    Access rules:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  Doc belongs to OUI-X, sensitivity=S                                │
    │  • Owner:                              can_view + can_edit          │
    │  • Member OUI-X, clearance ≥ S:        can_view  (viewer)           │
    │  • Member ancestor OUI, clearance ≥ S: can_view  (viewer)           │
    │  • Member ROOT OUI, clearance ≥ S:     can_view + can_edit (editor) │
    │  • Member descendant OUI (S=1 only):   can_view  (viewer)           │
    └─────────────────────────────────────────────────────────────────────┘

    Tuple counts per doc (regardless of tree depth):
      1 (owner) + 1 (direct#member→viewer) + 1 (parent#ancestor_member→viewer) + 1 (root#member→editor)
      = at most 4 tuples; ancestors are not enumerated individually.
    """

    # ── OUI membership ─────────────────────────────────────────────────────────

    # Write a member tuple granting the user membership in the given OUI.
    def add_oui_member(self, user_id: str, oui_id: str) -> None:
        fga_client.write([
            {"user": f"user:{user_id}", "relation": "member", "object": f"oui:{oui_id}"}
        ])

    # Delete the member tuple revoking the user's membership in the given OUI.
    def remove_oui_member(self, user_id: str, oui_id: str) -> None:
        fga_client.delete([
            {"user": f"user:{user_id}", "relation": "member", "object": f"oui:{oui_id}"}
        ])

    # Write a parent_oui tuple linking oui_id as a child of parent_oui_id.
    def link_oui_parent(self, oui_id: str, parent_oui_id: str) -> None:
        fga_client.write([
            {"user": f"oui:{parent_oui_id}", "relation": "parent_oui", "object": f"oui:{oui_id}"}
        ])

    # Delete the parent_oui tuple removing the parent link between the two OUIs.
    def unlink_oui_parent(self, oui_id: str, parent_oui_id: str) -> None:
        fga_client.delete([
            {"user": f"oui:{parent_oui_id}", "relation": "parent_oui", "object": f"oui:{oui_id}"}
        ])

    # ── Document sync ──────────────────────────────────────────────────────────

    # Build and write all access tuples for a document based on its OUI tree and sensitivity.
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

            # Direct parents use ancestor_member so FGA resolves the full ancestor chain → viewer.
            for parent_id in oui_tree_service.get_direct_parents(db, oui.id):
                _add(f"oui:{parent_id}#ancestor_member", "viewer", condition)

            # Only root OUI members receive edit access.
            root_id = oui_tree_service.get_root_oui_id(db, oui.id)
            if root_id != oui.id:
                _add(f"oui:{root_id}#member", "editor", condition)
            else:
                # Doc belongs to root: root members get editor (which implies viewer as well).
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

    # Delete a list of previously written tuples for a document.
    def delete_document_tuples(self, doc_id: str, tuples_to_delete: list[dict]) -> None:
        if tuples_to_delete:
            fga_client.delete(tuples_to_delete)

    # Return all currently stored tuples for a document (used before re-sync).
    def get_document_tuples(self, doc_id: str) -> list[dict]:
        return fga_client.read(object=f"document:{doc_id}")

    # Return all document IDs the user can view given their clearance level.
    def list_viewable_document_ids(self, user_id: str, user_clearance: int) -> list[str]:
        objects = fga_client.list_objects(
            user=f"user:{user_id}",
            relation="can_view",
            object_type="document",
            context={"user_clearance": user_clearance},
        )
        return [o.split(":", 1)[1] for o in objects if o.startswith("document:")]

    # ── Check ──────────────────────────────────────────────────────────────────

    # Return True if the user has can_view permission on the document.
    def can_view(self, user_id: str, doc_id: str, user_clearance: int) -> bool:
        return fga_client.check(
            user=f"user:{user_id}",
            relation="can_view",
            object=f"document:{doc_id}",
            context={"user_clearance": user_clearance},
        )

    # Return True if the user has can_edit permission on the document.
    def can_edit(self, user_id: str, doc_id: str, user_clearance: int) -> bool:
        return fga_client.check(
            user=f"user:{user_id}",
            relation="can_edit",
            object=f"document:{doc_id}",
            context={"user_clearance": user_clearance},
        )

    # ── Model deployment ───────────────────────────────────────────────────────

    # Load model.json from disk, upload it to FGA, and return the new model ID.
    def push_model(self) -> str:
        model_path = Path(__file__).parent / "model.json"
        model = json.loads(model_path.read_text(encoding="utf-8"))
        model_id = fga_client.write_model(model)
        logger.info("FGA model pushed: %s", model_id)
        return model_id


# Module-level singleton; imported by document_service, org_units, and permission_service.
fga_adapter = FGAAdapter()
