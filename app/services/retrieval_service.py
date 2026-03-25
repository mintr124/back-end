import logging

from app.services.chroma_service import chroma_service
from app.services.embedding_service import embedding_service
from app.repositories.chroma_repository import ChromaRepository

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self):
        self.repo = ChromaRepository()

    def retrieve(self, *, query: str, user=None, top_k: int = 5) -> list[dict]:
        emb = embedding_service.embed(query)
        raw = self.repo.query_by_embedding(embedding=emb, top_k=top_k)
        # Debug: log raw response when no results found to aid troubleshooting
        if not raw or not raw.get("ids") or all(len(x) == 0 for x in raw.get("ids", [])):
            logger.warning("Retrieval returned no ids for query=%s; raw=%s", query, raw)
        results = []
        ids = raw.get("ids", [[]])[0]
        docs = raw.get("documents", [[]])[0]
        metadatas = raw.get("metadatas", [[]])[0]
        distances = raw.get("distances", [[]])[0]

        for i, cid in enumerate(ids):
            md = metadatas[i] if i < len(metadatas) else {}
            doc_text = docs[i] if i < len(docs) else None
            dist = distances[i] if i < len(distances) else None
            # convert distance to similarity in [0,1], assume cosine 
            #TODO: threshold to choose the most relevance
            try:
                relevance = 1.0 - float(dist)
            except Exception:
                relevance = None

            results.append(
                {
                    "chunk_id": cid,
                    "document_text": doc_text,
                    "metadata": md,
                    "distance": dist,
                    "relevance": relevance,
                }
            )

        return results


retrieval_service = RetrievalService()
