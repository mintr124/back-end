from sqlalchemy.orm import Session

from app.models.department import Department


class DepartmentRepository:
    def get_by_id(self, db: Session, department_id: str) -> Department | None:
        return db.get(Department, department_id)

    def create(self, db: Session,name: str) -> Department:
        dept = Department(name=name)
        db.add(dept)
        db.flush()
        return dept
