from pydantic import BaseModel, ConfigDict


class ClearanceLevelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: int
