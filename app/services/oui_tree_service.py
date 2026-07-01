from __future__ import annotations
from sqlalchemy.orm import Session
from app.models.org_unit_instance import OrgUnitInstance, oui_parent


class OuiTreeService:
    """
    Helper để duyệt cây OUI (multi-parent DAG).
    Dùng cho:
    - Conflict check khi assign user
    - Sync FGA tuples (lấy ancestors để grant quyền)
    - Access check (node con xem doc public của node cha)
    """

    def get_direct_parents(self, db: Session, oui_id: str) -> list[str]:
        """Trả về list oui_id của các parent trực tiếp (không đệ quy)."""
        rows = db.execute(
            oui_parent.select().where(oui_parent.c.oui_id == oui_id)
        ).fetchall()
        return [row.parent_oui_id for row in rows]

    def get_root_oui_id(self, db: Session, oui_id: str) -> str:
        """Traverse lên gốc, trả về oui_id của root (không có parent)."""
        current = oui_id
        while True:
            rows = db.execute(
                oui_parent.select().where(oui_parent.c.oui_id == current)
            ).fetchall()
            if not rows:
                return current
            current = rows[0].parent_oui_id

    def get_ancestors(self, db: Session, oui_id: str) -> list[str]:
        """
        Trả về list oui_id của tất cả ancestors (không bao gồm chính nó).
        BFS duyệt lên qua bảng oui_parents.
        """
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

    def get_descendants(self, db: Session, oui_id: str) -> list[str]:
        """
        Trả về list oui_id của tất cả descendants (không bao gồm chính nó).
        BFS duyệt xuống.
        """
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

    def get_ancestor_and_descendant_ids(self, db: Session, oui_id: str) -> set[str]:
        """Trả về union của ancestors + descendants (dùng cho conflict check)."""
        return set(self.get_ancestors(db, oui_id)) | set(self.get_descendants(db, oui_id))

    def check_conflict(self, db: Session, user_id: str, oui_id: str) -> str | None:
        """
        Kiểm tra xem user có thể được assign vào oui_id không.
        Trả về None nếu OK, hoặc thông báo lỗi nếu conflict.
        """
        from app.models.user_oui_position import UserOuiPosition

        # Lấy tất cả OUI user đang thuộc
        existing = db.query(UserOuiPosition).filter(
            UserOuiPosition.user_id == user_id
        ).all()
        if not existing:
            return None

        # Các OUI trên cùng nhánh với oui_id
        forbidden = self.get_ancestor_and_descendant_ids(db, oui_id)
        forbidden.add(oui_id)  # không được assign vào cùng OUI 2 lần (đã có UNIQUE nhưng check rõ hơn)

        for rec in existing:
            if rec.oui_id in forbidden:
                oui = db.get(OrgUnitInstance, rec.oui_id)
                oui_name = oui.name if oui else rec.oui_id
                return (
                    f"Conflict: user đã có position tại '{oui_name}' "
                    f"— không thể assign thêm vào node cùng nhánh"
                )
        return None


    def get_user_branch_oui_ids(self, db: Session, user) -> set[str]:
        """
        Trả về tất cả OUI IDs trong nhánh cây chứa user:
        user's OUIs + tất cả ancestors + tất cả descendants.
        Dùng cho query_scope_mode = 'branch_only'.
        """
        user_oui_ids = {uop.oui_id for uop in getattr(user, "oui_positions", [])}
        all_ids: set[str] = set(user_oui_ids)
        for oui_id in user_oui_ids:
            all_ids.update(self.get_ancestors(db, oui_id))
            all_ids.update(self.get_descendants(db, oui_id))
        return all_ids

    def get_doc_ids_for_oui_ids(self, db: Session, oui_ids: set[str]) -> set[str]:
        """Trả về tất cả document IDs thuộc các OUI trong oui_ids."""
        if not oui_ids:
            return set()
        from app.models.document import document_oui
        rows = db.execute(
            document_oui.select().where(document_oui.c.oui_id.in_(list(oui_ids)))
        ).fetchall()
        return {row.document_id for row in rows}


oui_tree_service = OuiTreeService()
