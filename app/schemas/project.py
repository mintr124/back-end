from pydantic import BaseModel
from typing import List

class ProjectCreateRequest(BaseModel):
    name: str
    department_id: str

class ProjectResponse(BaseModel):
    id: str
    name: str
    department_id: str
    user_count: int = 0
    model_config = {"from_attributes": True}

class ProjectUpdateDepartmentRequest(BaseModel):
    department_id: str

class ProjectUpdateUsersRequest(BaseModel):
    user_ids: List[str]