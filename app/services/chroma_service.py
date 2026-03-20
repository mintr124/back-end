from app.repositories.chroma_repository import ChromaRepository


class ChromaService:
    def __init__(self):
        self.repo = ChromaRepository()

    def upsert_chunk(self, *, chunk_id: str, document_text: str, embedding: list[float], metadata: dict):
        self.repo.upsert(
            chunk_id=chunk_id,
            document_text=document_text,
            embedding=embedding,
            metadata=metadata,
        )


chroma_service = ChromaService()
