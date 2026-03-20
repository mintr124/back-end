from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str
    minio: str
    chroma: str
