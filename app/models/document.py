from sqlalchemy import Column, ForeignKey, JSON, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=new_uuid)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)

    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True, index=True)
    owner_user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    document_type = Column(String(64), nullable=False, default="general")
    sensitivity_level = Column(String(32), nullable=False, default="internal")
    data_type = Column(String(64), nullable=False, default="text")

    allowed_roles = Column(JSON, nullable=False, default=list)
    tags = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="draft")
    current_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=True, index=True)

    department = relationship("Department", back_populates="documents")
    project = relationship("Project", back_populates="documents")
    owner = relationship("User")
    versions = relationship(
        "DocumentVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="DocumentVersion.document_id",
    )
    current_version = relationship(
        "DocumentVersion",
        foreign_keys=[current_version_id],
        post_update=True,
        uselist=False,
    )

    @property
    def version_count(self):
        return len(self.versions or [])
