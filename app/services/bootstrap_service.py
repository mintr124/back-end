from sqlalchemy.orm import Session

from app.models.department import Department
from app.models.project import Project
from app.models.user import User


class BootstrapService:
    def seed_defaults(self, db: Session):
        if db.query(User).count() > 0:
            return

        engineering = Department(name="Engineering")
        bod = Department(name="Board of Directors")
        hr = Department(name="HR")
        it_admin = Department(name="IT Administration")
        finance = Department(name="Finance")
        sale = Department(name="Sales")

        db.add_all([engineering, bod, it_admin, finance])
        db.flush()

        project_erp = Project(name="ERP Upgrade", department_id=finance.id)
        project_kb = Project(name="Knowledge Base Platform", department_id=bod.id)

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
                department_id=finance.id,
                status="active",
            ),
            User(
                email="cuong.le@company.com",
                name="Le Cuong",
                role="director",
                clearance_level="restricted",
                department_id=bod.id,
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
