from typing import Optional
from pydantic import BaseModel, ConfigDict

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    role: str
    clearance_level: str
    department_id: Optional[str] = None
    status: str
