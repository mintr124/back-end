from __future__ import annotations

from app.repositories.chroma_repository import ChromaRepository


class ChromaService:
    """
    Thin service wrapper.
    Lazy repo creation avoids import-time side effects.
    """

    def __init__(self):
        self._repo = None

    @property
    def repo(self) -> ChromaRepository:
        if self._repo is None:
            self._repo = ChromaRepository()
        return self._repo

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
        
    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        from app.repositories.chroma_repository import ChromaRepository
        repo = ChromaRepository()
        repo.delete_by_ids(chunk_ids)

    def query_by_embedding(self, *, embedding: list[float], top_k: int = 5) -> dict:
        return self.repo.query_by_embedding(embedding=embedding, top_k=top_k)

    def query_by_keyword(self, *, query: str, top_k: int = 5) -> dict:
        return self.repo.query_by_keyword(query=query, top_k=top_k)
    
    def update_document_metadata(self, chunk_ids: list[str], metadata_updates: dict) -> None:
        self.repo.update_document_metadata(chunk_ids, metadata_updates)

    def get_metadatas_by_ids(self, chunk_ids: list[str]) -> dict[str, dict]:
        return self.repo.get_metadatas_by_ids(chunk_ids)


chroma_service = ChromaService()
