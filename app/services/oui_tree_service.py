"""
Service for traversing the OUI (multi-parent DAG) tree: conflict checks, FGA sync, and access scope queries.
"""
from __future__ import annotations
from sqlalchemy.orm import Session
from app.models.org_unit_instance import OrgUnitInstance, oui_parent


# Service for traversing the OUI (multi-parent DAG) tree: conflict checks, FGA sync, and access scope queries.
class OuiTreeService:

    # Return the list of immediate parent OUI IDs (non-recursive).
    def get_direct_parents(self, db: Session, oui_id: str) -> list[str]:
        rows = db.execute(
            oui_parent.select().where(oui_parent.c.oui_id == oui_id)
        ).fetchall()
        return [row.parent_oui_id for row in rows]

    # Traverse up to the root and return the OUI ID of the node with no parent.
    def get_root_oui_id(self, db: Session, oui_id: str) -> str:
        current = oui_id
        while True:
            rows = db.execute(
                oui_parent.select().where(oui_parent.c.oui_id == current)
            ).fetchall()
            if not rows:
                return current
            current = rows[0].parent_oui_id

    # Return all ancestor OUI IDs (excluding the node itself) via BFS over oui_parents.
    def get_ancestors(self, db: Session, oui_id: str) -> list[str]:
        visited: set[str] = set()
        queue = [oui_id]
        while queue:
            current = queue.pop()
            rows = db.execute(
                oui_parent.select().where(oui_parent.c.oui_id == current)
            ).fetchall()
            for row in rows:
                pid = row.parent_oui_id
                if pid not in visited:
                    visited.add(pid)
                    queue.append(pid)
        return list(visited)

    # Return all descendant OUI IDs (excluding the node itself) via BFS.
    def get_descendants(self, db: Session, oui_id: str) -> list[str]:
        visited: set[str] = set()
        queue = [oui_id]
        while queue:
            current = queue.pop()
            rows = db.execute(
                oui_parent.select().where(oui_parent.c.parent_oui_id == current)
            ).fetchall()
            for row in rows:
                cid = row.oui_id
                if cid not in visited:
                    visited.add(cid)
                    queue.append(cid)
        return list(visited)

    # Return the union of all ancestors and descendants (used for conflict checks).
    def get_ancestor_and_descendant_ids(self, db: Session, oui_id: str) -> set[str]:
        return set(self.get_ancestors(db, oui_id)) | set(self.get_descendants(db, oui_id))

    # Check if the user can be assigned to oui_id; return None if OK or an error message on conflict.
    def check_conflict(self, db: Session, user_id: str, oui_id: str) -> str | None:
        from app.models.user_oui_position import UserOuiPosition

        # Fetch all OUI records the user is currently assigned to.
        existing = db.query(UserOuiPosition).filter(
            UserOuiPosition.user_id == user_id
        ).all()
        if not existing:
            return None

        # OUIs on the same branch as oui_id.
        forbidden = self.get_ancestor_and_descendant_ids(db, oui_id)
        forbidden.add(oui_id)  # also block re-assigning to the same OUI (UNIQUE handles it, but this is explicit)

        for rec in existing:
            if rec.oui_id in forbidden:
                oui = db.get(OrgUnitInstance, rec.oui_id)
                oui_name = oui.name if oui else rec.oui_id
                return (
                    f"Conflict: user đã có position tại '{oui_name}' "
                    f"— không thể assign thêm vào node cùng nhánh"
                )
        return None


    # Return the set of all OUI IDs in the user's tree branches: own OUIs plus all ancestors and descendants.
    def get_user_branch_oui_ids(self, db: Session, user) -> set[str]:
        user_oui_ids = {uop.oui_id for uop in getattr(user, "oui_positions", [])}
        all_ids: set[str] = set(user_oui_ids)
        for oui_id in user_oui_ids:
            all_ids.update(self.get_ancestors(db, oui_id))
            all_ids.update(self.get_descendants(db, oui_id))
        return all_ids

    # Return all document IDs belonging to the given set of OUI IDs.
    def get_doc_ids_for_oui_ids(self, db: Session, oui_ids: set[str]) -> set[str]:
        if not oui_ids:
            return set()
        from app.models.document import document_oui
        rows = db.execute(
            document_oui.select().where(document_oui.c.oui_id.in_(list(oui_ids)))
        ).fetchall()
        return {row.document_id for row in rows}


# Module-level singleton; imported by the policy agent, FGA sync, and document service.
oui_tree_service = OuiTreeService()
