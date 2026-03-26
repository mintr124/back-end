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

    def query_by_embedding(self, *, embedding: list[float], top_k: int = 5) -> dict:
        return self.repo.query_by_embedding(embedding=embedding, top_k=top_k)

    def query_by_keyword(self, *, query: str, top_k: int = 5) -> dict:
        return self.repo.query_by_keyword(query=query, top_k=top_k)


chroma_service = ChromaService()
