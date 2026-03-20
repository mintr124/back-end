from sqlalchemy import Column, ForeignKey, JSON, String
from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class DocumentPolicySnapshot(Base, TimestampMixin):
    __tablename__ = "document_policy_snapshots"

    id = Column(String(36), primary_key=True, default=new_uuid)
    document_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=False, index=True)
    policy_version = Column(String(32), nullable=False)
    contract_json = Column(JSON, nullable=False)
