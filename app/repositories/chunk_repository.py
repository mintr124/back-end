"""
Repository for document chunk persistence and retrieval.
"""
from sqlalchemy.orm import Session

from app.models.document_chunk import DocumentChunk


class ChunkRepository:
    # Persist a new document chunk.
    def create(self, db: Session, chunk: DocumentChunk) -> DocumentChunk:
        db.add(chunk)
        db.flush()
        return chunk

    # Return all chunks for a document version ordered by chunk_index.
    def list_by_version(self, db: Session, version_id: str) -> list[DocumentChunk]:
        return db.query(DocumentChunk).filter(DocumentChunk.document_version_id == version_id).order_by(DocumentChunk.chunk_index.asc()).all()

    # Delete all chunks belonging to a document version.
    def delete_by_version(self, db: Session, version_id: str):
        db.query(DocumentChunk).filter(DocumentChunk.document_version_id == version_id).delete(synchronize_session=False)

