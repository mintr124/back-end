from sqlalchemy.orm import Session
from app.core.security import hash_password

from app.models.department import Department
from app.models.project import Project
from app.models.user import User


class BootstrapService:
    def seed_defaults(self, db: Session):
        if db.query(User).count() > 0:
            return

        it_admin = Department(name="IT Administration")
        db.add(it_admin)
        db.flush()

        admin = User(
            email="admin",
            name="Administrator",
            role="admin_auditor",
            clearance_level="top_secret",
            department_id=it_admin.id,
            status="active",
            password_hash=hash_password("Admin@123"),
        )
        db.add(admin)
        db.commit()


bootstrap_service = BootstrapService()