from pydantic import BaseModel

class RoleResponse(BaseModel):
    id: str
    name: str
    model_config = {"from_attributes": True}