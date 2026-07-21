"""
Service wrapper for ChromaDB vector operations: upsert, delete, embedding search, and keyword search.
"""
from __future__ import annotations

from app.repositories.chroma_repository import ChromaRepository


class ChromaService:
    """Thin service wrapper; lazy repo creation avoids import-time side effects."""

    def __init__(self):
        self._repo = None

    # Lazily instantiate the ChromaRepository on first access.
    @property
    def repo(self) -> ChromaRepository:
        if self._repo is None:
            self._repo = ChromaRepository()
        return self._repo

    # Upsert a single chunk with its embedding and metadata into ChromaDB.
    def upsert_chunk(
        self,
        *,
        chunk_id: str,
        document_text: str,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        self.repo.upsert(
            chunk_id=chunk_id,
            document_text=document_text,
            embedding=embedding,
            metadata=metadata,
        )

    # Delete chunks by their IDs; no-op if the list is empty.
    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self.repo.delete_by_ids(chunk_ids)

    # Run a vector similarity search and return the raw ChromaDB result dict.
    def query_by_embedding(self, *, embedding: list[float], top_k: int = 5) -> dict:
        return self.repo.query_by_embedding(embedding=embedding, top_k=top_k)

    # Run a keyword (BM25) search and return the raw ChromaDB result dict.
    def query_by_keyword(self, *, query: str, top_k: int = 5) -> dict:
        return self.repo.query_by_keyword(query=query, top_k=top_k)

    # Apply metadata updates to a set of chunks in place.
    def update_document_metadata(self, chunk_ids: list[str], metadata_updates: dict) -> None:
        self.repo.update_document_metadata(chunk_ids, metadata_updates)

    # Return a mapping of chunk_id → metadata dict for the given IDs.
    def get_metadatas_by_ids(self, chunk_ids: list[str]) -> dict[str, dict]:
        return self.repo.get_metadatas_by_ids(chunk_ids)

    # Return the max chunk_sensitivity across all chunks of a document.
    def get_max_chunk_sensitivity(self, document_id: str) -> int:
        return self.repo.get_max_chunk_sensitivity(document_id)


# Module-level singleton; imported by the ingest pipeline and retrieval service.
chroma_service = ChromaService()
