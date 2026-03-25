import json
import os

import chromadb

from app.core.config import settings


class ChromaRepository:
    def __init__(self):
        os.makedirs(settings.chroma_path, exist_ok=True)
        self.client = chromadb.PersistentClient(path=settings.chroma_path)
        self.collection = self.client.get_or_create_collection(
            name=settings.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(
        self,
        *,
        chunk_id: str,
        document_text: str,
        embedding: list[float],
        metadata: dict,
    ):
        flat = {}
        for k, v in metadata.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                flat[k] = v
            else:
                flat[k] = json.dumps(v, ensure_ascii=False)

        self.collection.upsert(
            ids=[chunk_id],
            documents=[document_text],
            embeddings=[embedding],
            metadatas=[flat],
        )

    def query_by_embedding(self, *, embedding: list[float], top_k: int = 5) -> dict:
        # returns a dict with ids, documents, metadatas, distances
        res = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            # 'ids' is not a valid include item for chromadb query; ids are returned separately
            include=["metadatas", "documents", "distances"],
        )
        return res
