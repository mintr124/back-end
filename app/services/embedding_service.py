import hashlib
import math
import re


class EmbeddingService:
    def __init__(self, dims: int = 384, model_name: str = "hash-embedding-v1"):
        self.dims = dims
        self.model_name = model_name

    def embed(self, text: str) -> list[float]:
        tokens = re.findall(r"\w+", text.lower())
        vec = [0.0] * self.dims

        for token in tokens:
            h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dims
            weight = 1.0 + ((h >> 8) % 7) / 10.0
            vec[idx] += weight if ((h >> 3) & 1) == 0 else -weight

        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 8) for v in vec]


embedding_service = EmbeddingService()
