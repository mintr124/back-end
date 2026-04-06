from pydantic import BaseModel

class DepartmentCreateRequest(BaseModel):
    name: str

class DepartmentResponse(BaseModel):
    id: str
    name: str
    project_count: int = 0
    user_count: int = 0 

    model_config = {"from_attributes": True}