from sqlalchemy import Column, Integer, String

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class StorageObject(Base, TimestampMixin):
    __tablename__ = "storage_objects"

    id = Column(String(36), primary_key=True, default=new_uuid)
    provider = Column(String(32), nullable=False, default="minio")
    bucket = Column(String(128), nullable=False)
    object_key = Column(String(512), nullable=False, unique=True, index=True)
    object_kind = Column(String(64), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    checksum = Column(String(128), nullable=False, index=True)

