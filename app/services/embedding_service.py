from __future__ import annotations

import os
from typing import Iterable

from app.core.config import settings

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


class EmbeddingService:
    def __init__(
        self,
        model_name: str | None = None,
        dimensions: int | None = None,
    ):
        self.model_name = (
            model_name
            or getattr(settings, "openai_embedding_model", None)
            or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
        )

        raw_dims = (
            dimensions
            if dimensions is not None
            else getattr(settings, "openai_embedding_dims", None)
            or os.getenv("OPENAI_EMBEDDING_DIMS", "256")
        )
        self.dimensions = int(raw_dims) if raw_dims not in (None, "", 0, "0") else None

        self.api_key = (
            getattr(settings, "openai_api_key", None)
            or os.getenv("OPENAI_API_KEY")
        )
        self.base_url = (
            getattr(settings, "openai_api_base", None)
            or os.getenv("OPENAI_API_BASE")
        )

        self.client = None
        if OpenAI is not None and self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or None,
            )

    def is_configured(self) -> bool:
        return self.client is not None

    def embed(self, text: str) -> list[float]:
        if not self.client:
            raise RuntimeError("OpenAI embedding client is not configured")

        text = (text or "").strip()
        if not text:
            return []

        payload: dict = {
            "model": self.model_name,
            "input": text,
        }

        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions

        resp = self.client.embeddings.create(**payload)
        return resp.data[0].embedding

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        if not self.client:
            raise RuntimeError("OpenAI embedding client is not configured")

        cleaned = [(t or "").strip() for t in texts]
        cleaned = [t for t in cleaned if t]

        if not cleaned:
            return []

        payload: dict = {
            "model": self.model_name,
            "input": cleaned,
        }

        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions

        resp = self.client.embeddings.create(**payload)
        return [item.embedding for item in resp.data]


embedding_service = EmbeddingService()
