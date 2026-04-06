from typing import Optional, List
from pydantic import BaseModel, ConfigDict

class UserCreateRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str
    clearance_level: str
    department_id: Optional[str] = None

class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    clearance_level: Optional[str] = None
    status: Optional[str] = None
    department_id: Optional[str] = None

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str
    role: str
    clearance_level: str
    department_id: Optional[str] = None
    status: str

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    clearance_level: str
    department_id: Optional[str] = None
    department_name: Optional[str] = None
    status: str
    model_config = {"from_attributes": True}