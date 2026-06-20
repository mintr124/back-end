from chromadb import db
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_version import DocumentVersion


class DocumentRepository:
    def get_by_id(self, db: Session, document_id: str) -> Document | None:
        return db.get(Document, document_id)

    def list_all(self, db: Session) -> list[Document]:
        return db.query(Document).order_by(Document.updated_at.desc()).all()

    def create(self, db: Session, doc: Document) -> Document:
        db.add(doc)
        db.flush()
        return doc

    def save(self, db: Session, doc: Document) -> Document:
        db.flush()
        return doc

    def get_max_version_no(self, db: Session, document_id: str) -> int:
        return db.query(func.max(DocumentVersion.version_no)).filter(DocumentVersion.document_id == document_id).scalar() or 0
    
