from pydantic import BaseModel

class ClearanceLevelResponse(BaseModel):
    id: str
    name: str
    level: int
    model_config = {"from_attributes": True}