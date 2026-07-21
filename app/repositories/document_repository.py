"""
Repository for document persistence and retrieval.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_version import DocumentVersion


class DocumentRepository:
    # Return a document by ID.
    def get_by_id(self, db: Session, document_id: str) -> Document | None:
        return db.get(Document, document_id)

    # Return all documents ordered by last update descending.
    def list_all(self, db: Session) -> list[Document]:
        return db.query(Document).order_by(Document.updated_at.desc()).all()

    # Persist a new document.
    def create(self, db: Session, doc: Document) -> Document:
        db.add(doc)
        db.flush()
        return doc

    # Flush pending changes for an existing document.
    def save(self, db: Session, doc: Document) -> Document:
        db.flush()
        return doc

    # Return the highest version_no for a document, or 0 if none exist.
    def get_max_version_no(self, db: Session, document_id: str) -> int:
        return db.query(func.max(DocumentVersion.version_no)).filter(DocumentVersion.document_id == document_id).scalar() or 0

