from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.project import Project
from app.models.user_project import UserProject
from app.models.user_department import UserDepartment
from app.models.user import User
from app.repositories.project_repository import ProjectRepository
from app.repositories.department_repository import DepartmentRepository
from app.schemas.project import ProjectCreateRequest, ProjectResponse
from app.services.audit_service import audit_service
from app.fga.adapter import fga_adapter


class ProjectService:
    def __init__(self):
        self.repo = ProjectRepository()
        self.dept_repo = DepartmentRepository()

    def _to_response(self, db: Session, p: Project) -> ProjectResponse:
        count = db.query(func.count(UserProject.user_id)).filter(
            UserProject.project_id == p.id
        ).scalar()
        return ProjectResponse(
            id=p.id, name=p.name,
            department_id=p.department_id,
            user_count=count or 0,
        )

    def list_projects(self, db: Session, user: User, department_id=None) -> list[ProjectResponse]:
        q = db.query(Project)

        if user.role in {"admin_auditor", "director"}:
            # Xem tất cả
            if department_id:
                q = q.filter(Project.department_id == department_id)

        elif user.role == "department_manager":
            q = q.filter(Project.department_id == user.department_id)
            if department_id:
                q = q.filter(Project.department_id == department_id)

        else:
            q = q.join(UserProject, UserProject.project_id == Project.id).filter(
                UserProject.user_id == user.id
            )
            if department_id:
                q = q.filter(Project.department_id == department_id)

        return [self._to_response(db, p) for p in q.order_by(Project.name).all()]

    def create_project(self, db: Session, user: User, payload, trace_id: str) -> ProjectResponse:
        if user.role not in {"admin_auditor", "director", "department_manager"}:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        dept = self.dept_repo.get_by_id(db, payload.department_id)
        if not dept:
            raise HTTPException(status_code=404, detail="Department not found")
        existing = db.query(Project).filter(
            Project.name == payload.name,
            Project.department_id == payload.department_id,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Project '{payload.name}' already exists")

        project = self.repo.create(db, payload.name, payload.department_id)
        audit_service.log_action(
            db, trace_id=trace_id, user_id=user.id,
            action="project.create", resource_type="project",
            resource_id=project.id, decision="allow",
            input_json=payload.model_dump(mode="json"),
        )
        db.commit()
        db.refresh(project)

        # ── FGA: liên kết project với department ─────────────────────────────
        fga_adapter.link_project_dept(project.id, payload.department_id)

        return self._to_response(db, project)

    def update_department(
        self, db: Session, actor: User, project_id: str, department_id: str
    ) -> ProjectResponse:
        if actor.role not in {"admin_auditor", "director", "department_manager"}:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        dept = self.dept_repo.get_by_id(db, department_id)
        if not dept:
            raise HTTPException(status_code=404, detail="Department not found")

        old_dept_id = project.department_id
        project.department_id = department_id
        db.commit()
        db.refresh(project)

        # ── FGA: xóa link dept cũ, thêm link dept mới ────────────────────────
        fga_adapter.unlink_project_dept(project_id, old_dept_id)
        fga_adapter.link_project_dept(project_id, department_id)

        return self._to_response(db, project)

    def update_users(
        self, db: Session, actor: User, project_id: str, user_ids: list[str]
    ) -> ProjectResponse:
        if actor.role not in {"admin_auditor", "director", "department_manager"}:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        old_user_ids = [
            row.user_id
            for row in db.query(UserProject).filter(
                UserProject.project_id == project_id
            ).all()
        ]

        db.query(UserProject).filter(UserProject.project_id == project_id).delete()
        for uid in user_ids:
            db.add(UserProject(user_id=uid, project_id=project_id))
        db.commit()

        for uid in old_user_ids:
            fga_adapter.remove_project_member(uid, project_id)
        for uid in user_ids:
            fga_adapter.add_project_member(uid, project_id)

        from app.repositories.document_repository import DocumentRepository
        doc_repo = DocumentRepository()
        docs = doc_repo.list_by_project(db, project_id)

        if docs:
            all_users = db.query(User).all()
            project_users = db.query(User).filter(
                User.id.in_(user_ids),
                User.role.notin_(["admin_auditor", "director"]),
            ).all()

            # Bỏ join UserDepartment — lấy dept_managers theo department_id trực tiếp
            dept_managers = db.query(User).filter(
                User.department_id == project.department_id,
                User.role == "department_manager",
            ).all()

            for doc in docs:
                # Bỏ join UserDepartment — lấy dept_users theo department_id trực tiếp
                dept_users = db.query(User).filter(
                    User.department_id == doc.department_id
                ).all() if doc.department_id else []

                existing_tuples = fga_adapter.get_document_tuples(doc.id)
                fga_adapter.delete_document_tuples(doc.id, existing_tuples)
                fga_adapter.sync_document_tuples(
                    doc=doc,
                    all_users=all_users,
                    dept_users=dept_users,
                    project_users=project_users,
                    dept_managers=dept_managers,
                )

        return self._to_response(db, project)

    def get_project_users(self, db: Session, project_id: str) -> list:
        rows = (
            db.query(User)
            .join(UserProject, UserProject.user_id == User.id)
            .filter(UserProject.project_id == project_id)
            .all()
        )
        return [{"id": u.id, "name": u.name, "email": u.email} for u in rows]


project_service = ProjectService()