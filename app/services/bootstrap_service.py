from sqlalchemy.orm import Session

from app.models.department import Department
from app.models.project import Project
from app.models.user import User


class BootstrapService:
    def seed_defaults(self, db: Session):
        if db.query(User).count() > 0:
            return

        engineering = Department(code="engineering", name="Engineering")
        knowledge = Department(code="knowledge-management", name="Knowledge Management")
        it_admin = Department(code="it-administration", name="IT Administration")
        finance = Department(code="finance", name="Finance")

        db.add_all([engineering, knowledge, it_admin, finance])
        db.flush()

        project_erp = Project(code="ERP_upgrade", name="ERP Upgrade", department_id=finance.id)
        project_kb = Project(code="KB_platform", name="Knowledge Base Platform", department_id=knowledge.id)

        db.add_all([project_erp, project_kb])
        db.flush()

        users = [
            User(
                email="an.nguyen@company.com",
                name="Nguyen An",
                role="employee",
                clearance_level="internal",
                department_id=engineering.id,
                status="active",
            ),
            User(
                email="binh.tran@company.com",
                name="Tran Binh",
                role="department_manager",
                clearance_level="confidential",
                department_id=engineering.id,
                status="active",
            ),
            User(
                email="cuong.le@company.com",
                name="Le Cuong",
                role="knowledge_director",
                clearance_level="restricted",
                department_id=knowledge.id,
                status="active",
            ),
            User(
                email="dung.pham@company.com",
                name="Pham Dung",
                role="admin_auditor",
                clearance_level="top_secret",
                department_id=it_admin.id,
                status="active",
            ),
        ]
        db.add_all(users)
        db.commit()


bootstrap_service = BootstrapService()
