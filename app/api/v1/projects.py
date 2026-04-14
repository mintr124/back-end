from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.deps import get_current_user, get_trace_id
from app.db.session import get_db
from app.models.user import User
from app.schemas.project import (
    ProjectCreateRequest, ProjectResponse,
    ProjectUpdateDepartmentRequest, ProjectUpdateUsersRequest,
)
from app.services.project_service import project_service

router = APIRouter()

@router.get("", response_model=list[ProjectResponse])
def list_projects(
    department_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return project_service.list_projects(db, current_user, department_id)

@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(
    payload: ProjectCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    trace_id: str = Depends(get_trace_id),
):
    return project_service.create_project(db, current_user, payload, trace_id)

@router.patch("/{project_id}/department", response_model=ProjectResponse)
def update_project_department(
    project_id: str,
    payload: ProjectUpdateDepartmentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return project_service.update_department(db, current_user, project_id, payload.department_id)

@router.patch("/{project_id}/users", response_model=ProjectResponse)
def update_project_users(
    project_id: str,
    payload: ProjectUpdateUsersRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return project_service.update_users(db, current_user, project_id, payload.user_ids)

@router.get("/{project_id}/users", response_model=list)
def get_project_users(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return project_service.get_project_users(db, project_id)