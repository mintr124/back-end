from sqlalchemy import Column, ForeignKey, Integer, JSON, String, Table, Text
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid

# Many-to-many: document <-> org_unit_instance.
document_oui = Table(
    "document_oui",
    Base.metadata,
    Column("document_id", String(36), ForeignKey("documents.id"), primary_key=True),
    Column("oui_id",      String(36), ForeignKey("org_unit_instances.id"), primary_key=True),
)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id            = Column(String(36), primary_key=True, default=new_uuid)
    title         = Column(String(255), nullable=False, index=True)
    description   = Column(Text, nullable=True)
    owner_user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    document_type = Column(String(64), nullable=False, default="general")
    sensitivity   = Column(Integer, nullable=False, default=2)  # 1=public,2=internal,3=confidential,4=restricted,5=top_secret
    data_type     = Column(String(64), nullable=False, default="text")
    tags          = Column(JSON, nullable=False, default=list)
    status        = Column(String(32), nullable=False, default="draft")

    current_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=True, index=True)

    # Multi OUI.
    ouis = relationship("OrgUnitInstance", secondary=document_oui, back_populates="documents")

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
