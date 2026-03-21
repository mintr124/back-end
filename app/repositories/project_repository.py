from sqlalchemy.orm import Session

from app.models.project import Project


class ProjectRepository:
    def get_by_id(self, db: Session, id: str) -> Project | None:
        return db.query(Project).filter(Project.id == id).first()

    def create(self, db: Session, name: str, department_id: str) -> Project:
        proj = Project(name=name, department_id=department_id)
        db.add(proj)
        db.flush()
        return proj

    def get_or_create(self, db: Session, id: str, name: str, department_id: str) -> Project:
        proj = self.get_by_id(db, id)
        if proj:
            return proj
        return self.create(db, name, department_id)
