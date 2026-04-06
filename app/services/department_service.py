from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.department import Department
from app.models.project import Project
from app.models.user import User
from app.repositories.department_repository import DepartmentRepository
from app.schemas.department import DepartmentCreateRequest, DepartmentResponse
from app.services.audit_service import audit_service


class DepartmentService:
    def __init__(self):
        self.repo = DepartmentRepository()

    def _to_response(self, db: Session, d: Department) -> DepartmentResponse:
        project_count = db.query(func.count(Project.id)).filter(
            Project.department_id == d.id
        ).scalar()

        # Thay UserDepartment → đếm trực tiếp từ bảng users
        user_count = db.query(func.count(User.id)).filter(
            User.department_id == d.id
        ).scalar()

        return DepartmentResponse(
            id=d.id,
            name=d.name,
            project_count=project_count or 0,
            user_count=user_count or 0,
        )

    def list_departments(self, db: Session) -> list[DepartmentResponse]:
        depts = db.query(Department).order_by(Department.name).all()
        return [self._to_response(db, d) for d in depts]

    def create_department(self, db, user, payload, trace_id):
        if user.role not in {"admin_auditor", "director"}:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        existing = db.query(Department).filter(Department.name == payload.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Department '{payload.name}' already exists")
        dept = self.repo.create(db, payload.name)
        audit_service.log_action(
            db, trace_id=trace_id, user_id=user.id,
            action="department.create", resource_type="department",
            resource_id=dept.id, decision="allow",
            input_json=payload.model_dump(mode="json"),
        )
        db.commit()
        db.refresh(dept)
        return self._to_response(db, dept)


department_service = DepartmentService()