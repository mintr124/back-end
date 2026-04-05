from sqlalchemy.orm import Session
from app.models.user import User
from app.models.user_project import UserProject


class UserRepository:
    def get_by_id(self, db: Session, user_id: str) -> User | None:
        return db.get(User, user_id)

    def get_by_email(self, db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email == email).first()

    def get_first_by_role(self, db: Session, role: str) -> User | None:
        return db.query(User).filter(User.role == role).first()

    def list_all(self, db: Session) -> list[User]:
        return db.query(User).order_by(User.created_at.asc()).all()

    def list_active(self, db: Session) -> list[User]:
        return db.query(User).filter(User.status == "active").all()

    def list_by_dept(self, db: Session, dept_id: str) -> list[User]:
        # Dùng trực tiếp department_id trên User, không cần join
        return (
            db.query(User)
            .filter(
                User.department_id == dept_id,
                User.status == "active",
            )
            .all()
        )

    def list_by_project(self, db: Session, proj_id: str) -> list[User]:
        return (
            db.query(User)
            .join(UserProject, UserProject.user_id == User.id)
            .filter(UserProject.project_id == proj_id, User.status == "active")
            .all()
        )