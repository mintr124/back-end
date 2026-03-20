from sqlalchemy.orm import Session

from app.models.project import Project


class ProjectRepository:
    def get_by_code(self, db: Session, code: str) -> Project | None:
        return db.query(Project).filter(Project.code == code).first()

    def create(self, db: Session, code: str, name: str, department_id: str) -> Project:
        proj = Project(code=code, name=name, department_id=department_id)
        db.add(proj)
        db.flush()
        return proj

    def get_or_create(self, db: Session, code: str, name: str, department_id: str) -> Project:
        proj = self.get_by_code(db, code)
        if proj:
            return proj
        return self.create(db, code, name, department_id)
