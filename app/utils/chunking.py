import hashlib
from typing import Iterable

from app.utils.file_parser import ParsedDocument


def chunk_parsed_document(parsed: ParsedDocument, max_chars: int = 1200, overlap: int = 150) -> list[dict]:
    chunks = []
    index = 0

    for page_no, text in parsed.pages:
        text = text.strip()
        if not text:
            continue

        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            piece = text[start:end].strip()
            if piece:
                chunks.append(
                    {
                        "chunk_index": index,
                        "chunk_text": piece,
                        "page_start": page_no,
                        "page_end": page_no,
                        "token_count": len(piece.split()),
                        "chunk_hash": hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                        "metadata_json": {
                            "source_page": page_no,
                            "source_page_start": page_no,
                            "source_page_end": page_no,
                        },
                    }
                )
                index += 1
            if end >= len(text):
                break
            start = max(end - overlap, 0)

    return chunks
