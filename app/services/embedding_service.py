"""
embedding_service.py  –  v2
============================
Key improvements vs v1:
  - embed_many() uses a single OpenAI API call (batch), not N calls
  - dimensions default to 1536 (text-embedding-3-small native), configurable
  - Ollama batch via single /api/embed endpoint (v0.3+), falls back to loop
  - retry logic (3 attempts) for transient network errors
"""
from __future__ import annotations

import logging
import os
import time
from typing import Iterable

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore


_BATCH_SIZE = 64   # OpenAI supports up to 2048 inputs; 64 is safe & fast
_MAX_RETRY  = 3
_RETRY_WAIT = 1.5  # seconds


class EmbeddingService:
    def __init__(
        self,
        model_name: str | None = None,
        dimensions: int | None = None,
    ):
        self.provider = (
            getattr(settings, "llm_provider", None) or
            os.getenv("LLM_PROVIDER", "openai")
        ).lower()

        # ── OpenAI ────────────────────────────────────────────────────
        self.model_name = (
            model_name
            or getattr(settings, "openai_embedding_model", None)
            or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        )

        raw_dims = (
            dimensions
            if dimensions is not None
            else getattr(settings, "openai_embedding_dims", None)
            or os.getenv("OPENAI_EMBEDDING_DIMS", "1536")
        )
        # text-embedding-3-small native = 1536; 3-large native = 3072
        # Reduce only if you really need smaller index; 1536 is a good default.
        self.dimensions = int(raw_dims) if raw_dims not in (None, "", 0, "0") else 1536

        self.api_key  = getattr(settings, "openai_api_key",  None) or os.getenv("OPENAI_API_KEY")
        self.base_url = getattr(settings, "openai_api_base", None) or os.getenv("OPENAI_API_BASE")

        # ── Ollama ────────────────────────────────────────────────────
        self.ollama_url   = os.getenv("OLLAMA_URL",         "http://host.docker.internal:11434")
        self.ollama_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

        # ── Client ────────────────────────────────────────────────────
        self.client: "OpenAI | None" = None
        if self.provider == "openai" and OpenAI is not None and self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url or None,
            )

    # ------------------------------------------------------------------
    def is_configured(self) -> bool:
        if self.provider == "openai":
            return self.client is not None
        return True  # ollama – assume server is up

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _openai_batch(self, texts: list[str]) -> list[list[float]]:
        if not self.client:
            raise RuntimeError("OpenAI embedding client is not configured")

        payload: dict = {"model": self.model_name, "input": texts}
        if self.dimensions:
            payload["dimensions"] = self.dimensions

        for attempt in range(1, _MAX_RETRY + 1):
            try:
                resp = self.client.embeddings.create(**payload)
                # API returns items sorted by index
                items = sorted(resp.data, key=lambda x: x.index)
                return [item.embedding for item in items]
            except Exception as exc:
                if attempt == _MAX_RETRY:
                    raise
                logger.warning("OpenAI embed attempt %d failed: %s – retrying…", attempt, exc)
                time.sleep(_RETRY_WAIT * attempt)

        return []  # unreachable

    def _ollama_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Ollama ≥ 0.3 supports /api/embed with multiple inputs.
        Falls back to sequential /api/embeddings for older versions.
        """
        try:
            r = requests.post(
                f"{self.ollama_url}/api/embed",
                json={"model": self.ollama_model, "input": texts},
                timeout=120,
            )
            if r.status_code == 200:
                data = r.json()
                if "embeddings" in data:
                    return data["embeddings"]
        except Exception:
            pass  # fall through to legacy loop

        # Legacy sequential
        results: list[list[float]] = []
        for text in texts:
            r = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.ollama_model, "prompt": text},
                timeout=60,
            )
            r.raise_for_status()
            results.append(r.json()["embedding"])
        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Embed a single text."""
        result = self.embed_many([text])
        return result[0] if result else []

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        """
        Batch embed.  Splits into sub-batches of _BATCH_SIZE to stay within
        API limits while still being much faster than N individual calls.
        """
        cleaned = [(t or "").strip() for t in texts]
        # keep track of original positions; skip empty but remember slots
        non_empty_idx: list[int] = []
        non_empty_texts: list[str] = []
        for i, t in enumerate(cleaned):
            if t:
                non_empty_idx.append(i)
                non_empty_texts.append(t)

        if not non_empty_texts:
            return [[] for _ in cleaned]

        # sub-batch loop
        all_vectors: list[list[float]] = []
        for start in range(0, len(non_empty_texts), _BATCH_SIZE):
            batch = non_empty_texts[start: start + _BATCH_SIZE]
            if self.provider == "openai":
                vecs = self._openai_batch(batch)
            else:
                vecs = self._ollama_batch(batch)
            all_vectors.extend(vecs)

        # reconstruct full list (empty slots stay [])
        output: list[list[float]] = [[] for _ in cleaned]
        for slot, vec in zip(non_empty_idx, all_vectors):
            output[slot] = vec

        return output


embedding_service = EmbeddingService()