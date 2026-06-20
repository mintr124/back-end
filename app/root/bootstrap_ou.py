"""
python -m app.bootstrap_ou

Tạo Corp. root và seed cấu trúc mẫu theo diagram:
  Corp.
  ├── Department
  │   ├── HR          (OUI)
  │   ├── Marketing   (OUI)
  │   └── Finance     (OUI)
  ├── Division
  └── Branch

Chạy 1 lần sau khi migrate.
"""
import sys
sys.path.insert(0, ".")

from app.db.session import SessionLocal
from app.models.org_unit import OrgUnit
from app.models.org_unit_instance import OrgUnitInstance
from app.models.position import Position
from app.fga.adapter import fga_adapter


def main():
    db = SessionLocal()
    try:
        # ── OU types ──────────────────────────────────────────────────────────
        corp = OrgUnit(name="Corp.")
        db.add(corp)
        db.flush()

        dept_ou   = OrgUnit(name="Department", parent_id=corp.id)
        div_ou    = OrgUnit(name="Division",   parent_id=corp.id)
        branch_ou = OrgUnit(name="Branch",     parent_id=corp.id)
        db.add_all([dept_ou, div_ou, branch_ou])
        db.flush()

        proj_ou   = OrgUnit(name="Project", parent_id=dept_ou.id)
        team_ou   = OrgUnit(name="Team",    parent_id=dept_ou.id)
        group_ou  = OrgUnit(name="Group",   parent_id=div_ou.id)
        prog_ou   = OrgUnit(name="Program", parent_id=div_ou.id)
        sup_ou    = OrgUnit(name="Support Unit", parent_id=branch_ou.id)
        db.add_all([proj_ou, team_ou, group_ou, prog_ou, sup_ou])
        db.flush()

        # ── Positions cho Corp. ───────────────────────────────────────────────
        Position(name="Admin",    ou_id=corp.id, clearance=5)
        Position(name="Director", ou_id=corp.id, clearance=5)
        corp_positions = [
            Position(name="Admin",    ou_id=corp.id, clearance=5),
            Position(name="Director", ou_id=corp.id, clearance=4),
        ]
        db.add_all(corp_positions)

        # ── Positions cho Department ──────────────────────────────────────────
        dept_positions = [
            Position(name="Dept Manager",        ou_id=dept_ou.id, clearance=4),
            Position(name="Deputy Dept Manager", ou_id=dept_ou.id, clearance=3),
            Position(name="Employee",            ou_id=dept_ou.id, clearance=2),
        ]
        db.add_all(dept_positions)

        # ── Positions cho Project ─────────────────────────────────────────────
        proj_positions = [
            Position(name="Project Leader", ou_id=proj_ou.id, clearance=3),
            Position(name="Member",         ou_id=proj_ou.id, clearance=2),
        ]
        db.add_all(proj_positions)
        db.flush()

        # ── OUI instances ─────────────────────────────────────────────────────
        # Corp. root instance (1 cái duy nhất)
        corp_oui = OrgUnitInstance(name="Corp.", ou_id=corp.id)
        db.add(corp_oui)
        db.flush()

        # Department instances
        hr_oui        = OrgUnitInstance(name="HR",        ou_id=dept_ou.id)
        marketing_oui = OrgUnitInstance(name="Marketing", ou_id=dept_ou.id)
        finance_oui   = OrgUnitInstance(name="Finance",   ou_id=dept_ou.id)
        db.add_all([hr_oui, marketing_oui, finance_oui])
        db.flush()

        # Gán OUI cha (HR, Marketing, Finance đều thuộc Corp. OUI)
        hr_oui.parents        = [corp_oui]
        marketing_oui.parents = [corp_oui]
        finance_oui.parents   = [corp_oui]
        db.flush()

        # Sync FGA parent links
        for oui in [hr_oui, marketing_oui, finance_oui]:
            fga_adapter.link_oui_parent(oui.id, corp_oui.id)

        db.commit()
        print("✓ Bootstrap hoàn tất.")
        print(f"  Corp. OU id:  {corp.id}")
        print(f"  Corp. OUI id: {corp_oui.id}")
        print(f"  HR OUI id:    {hr_oui.id}")
        print(f"  Marketing OUI id: {marketing_oui.id}")
        print(f"  Finance OUI id:   {finance_oui.id}")
        print()
        print("Tiếp theo: assign admin/director vào Corp. OUI qua POST /api/v1/users/assign-oui")

    finally:
        db.close()


if __name__ == "__main__":
    main()