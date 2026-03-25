import hashlib
from typing import Iterable

from app.utils.file_parser import ParsedDocument


def chunk_parsed_document(
    parsed: ParsedDocument,
    max_tokens: int = 800,
    overlap_tokens: int = 400,
) -> list[dict]:
    service = ChunkerService(
        ChunkConfig(
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            min_chunk_tokens=80,
        )
    )
    return service.chunk(parsed)

