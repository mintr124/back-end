from sqlalchemy.orm import Session

from app.models.department import Department


class DepartmentRepository:
    def get_by_id(self, db: Session, department_id: str) -> Department | None:
        return db.get(Department, department_id)

    def get_by_code(self, db: Session, code: str) -> Department | None:
        return db.query(Department).filter(Department.code == code).first()

    def create(self, db: Session, code: str, name: str) -> Department:
        dept = Department(code=code, name=name)
        db.add(dept)
        db.flush()
        return dept
