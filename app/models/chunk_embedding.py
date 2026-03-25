from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class ChunkEmbedding(Base, TimestampMixin):
    __tablename__ = "chunk_embeddings"

    id = Column(String(36), primary_key=True, default=new_uuid)
    chunk_id = Column(String(36), ForeignKey("document_chunks.id"), nullable=False, unique=True, index=True)
    vector_db = Column(String(32), nullable=False, default="chroma")
    collection_name = Column(String(128), nullable=False)
    vector_id = Column(String(128), nullable=False, unique=True, index=True)
    embedding_model = Column(String(128), nullable=False)
    dimensions = Column(Integer, nullable=False)
    embedding_status = Column(String(32), nullable=False, default="completed")
    embedded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    chunk = relationship("DocumentChunk", back_populates="embeddings")
