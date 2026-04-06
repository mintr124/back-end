from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.department import Department
from app.repositories.department_repository import DepartmentRepository
from app.schemas.user import UserResponse
from app.services.audit_service import audit_service
from app.core.security import hash_password
from app.fga.adapter import fga_adapter
from app.services.document_service import document_service
from app.repositories.document_repository import DocumentRepository


class UserService:
    def __init__(self):
        self.dept_repo = DepartmentRepository()

    def list_users(self, db: Session) -> list[UserResponse]:
        users = db.query(User).order_by(User.name).all()
        result = []
        for u in users:
            dept_name = None
            if u.department_id:
                dept = self.dept_repo.get_by_id(db, u.department_id)
                dept_name = dept.name if dept else None
            result.append(UserResponse(
                id=u.id, email=u.email, name=u.name, role=u.role,
                clearance_level=u.clearance_level, department_id=u.department_id,
                department_name=dept_name, status=u.status,
            ))
        return result

    def create_user(self, db: Session, actor: User, payload, trace_id: str) -> User:
        if actor.role not in {"admin_auditor", "director"}:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if payload.role not in {"admin_auditor", "director"} and not payload.department_id:
            raise HTTPException(status_code=400, detail="department_id is required for this role")
        if payload.department_id:
            dept = self.dept_repo.get_by_id(db, payload.department_id)
            if not dept:
                raise HTTPException(status_code=404, detail="Department not found")
        existing = db.query(User).filter(User.email == payload.email).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Email '{payload.email}' already exists")

        user = User(
            email=payload.email, name=payload.name, role=payload.role,
            clearance_level=payload.clearance_level,
            department_id=payload.department_id or None,
            status="active", password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()
        audit_service.log_action(
            db, trace_id=trace_id, user_id=actor.id,
            action="user.create", resource_type="user",
            resource_id=user.id, decision="allow",
            input_json={**payload.model_dump(mode="json"), "password": "***"},
        )
        db.commit()
        db.refresh(user)

        doc_repo = DocumentRepository()
        if payload.department_id:
            if user.role == "department_manager":
                fga_adapter.add_dept_manager(user.id, payload.department_id)
            else:
                fga_adapter.add_dept_member(user.id, payload.department_id)
            for doc in doc_repo.list_by_department(db, payload.department_id):
                old_tuples = fga_adapter.get_document_tuples(doc.id)
                fga_adapter.delete_document_tuples(doc.id, old_tuples)
                document_service._sync_fga(db, doc)

        for doc in doc_repo.list_no_dept_no_project(db):
            old_tuples = fga_adapter.get_document_tuples(doc.id)
            fga_adapter.delete_document_tuples(doc.id, old_tuples)
            document_service._sync_fga(db, doc)

        return user

    def update_user(self, db: Session, actor: User, user_id: str, payload) -> User:
        if actor.role not in {"admin_auditor", "director"}:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        old_role = user.role
        old_clearance = user.clearance_level
        old_dept_id = user.department_id

        if payload.role is not None:
            user.role = payload.role
        if payload.clearance_level is not None:
            user.clearance_level = payload.clearance_level
        if payload.status is not None:
            user.status = payload.status
        if payload.department_id is not None:
            user.department_id = payload.department_id

        db.commit()
        db.refresh(user)

        role_changed = payload.role is not None and payload.role != old_role
        clearance_changed = payload.clearance_level is not None and payload.clearance_level != old_clearance
        dept_changed = payload.department_id is not None and payload.department_id != old_dept_id
        doc_repo = DocumentRepository()

        if dept_changed:
            if old_dept_id:
                if old_role == "department_manager":
                    fga_adapter.remove_dept_manager(user_id, old_dept_id)
                else:
                    fga_adapter.remove_dept_member(user_id, old_dept_id)
            if user.department_id:
                if user.role == "department_manager":
                    fga_adapter.add_dept_manager(user_id, user.department_id)
                else:
                    fga_adapter.add_dept_member(user_id, user.department_id)
            for dept_id in filter(None, [old_dept_id, user.department_id]):
                for doc in doc_repo.list_by_department(db, dept_id):
                    old_tuples = fga_adapter.get_document_tuples(doc.id)
                    fga_adapter.delete_document_tuples(doc.id, old_tuples)
                    document_service._sync_fga(db, doc)

        if role_changed or clearance_changed:
            if role_changed and user.department_id:
                if old_role == "department_manager":
                    fga_adapter.remove_dept_manager(user_id, user.department_id)
                else:
                    fga_adapter.remove_dept_member(user_id, user.department_id)
                if user.role == "department_manager":
                    fga_adapter.add_dept_manager(user_id, user.department_id)
                else:
                    fga_adapter.add_dept_member(user_id, user.department_id)
            if user.department_id:
                for doc in doc_repo.list_by_department(db, user.department_id):
                    old_tuples = fga_adapter.get_document_tuples(doc.id)
                    fga_adapter.delete_document_tuples(doc.id, old_tuples)
                    document_service._sync_fga(db, doc)
            for doc in doc_repo.list_no_dept_no_project(db):
                old_tuples = fga_adapter.get_document_tuples(doc.id)
                fga_adapter.delete_document_tuples(doc.id, old_tuples)
                document_service._sync_fga(db, doc)

        return user


user_service = UserService()