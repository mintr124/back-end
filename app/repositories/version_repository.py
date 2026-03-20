from sqlalchemy.orm import Session

from app.models.document_version import DocumentVersion


class VersionRepository:
    def get_by_id(self, db: Session, version_id: str) -> DocumentVersion | None:
        return db.get(DocumentVersion, version_id)

    def list_by_document(self, db: Session, document_id: str) -> list[DocumentVersion]:
        return (
            db.query(DocumentVersion)
            .filter(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no.asc())
            .all()
        )

    def create(self, db: Session, version: DocumentVersion) -> DocumentVersion:
        db.add(version)
        db.flush()
        return version
