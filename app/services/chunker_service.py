from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.utils.file_parser import ParsedDocument

try:
    import tiktoken
except Exception:
    tiktoken = None


@dataclass
class ChunkConfig:
    max_tokens: int = 800
    overlap_tokens: int = 400
    min_chunk_tokens: int = 80
    encoding_name: str = "cl100k_base"


class ChunkerService:
    def __init__(self, config: ChunkConfig | None = None):
        self.config = config or ChunkConfig()
        self._encoding = None

        if tiktoken is not None:
            try:
                self._encoding = tiktoken.get_encoding(self.config.encoding_name)
            except Exception:
                self._encoding = None

    def _normalize_text(self, text: str) -> str:
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    def _count_tokens(self, text: str) -> int:
        text = self._normalize_text(text)
        if not text:
            return 0

        if self._encoding is not None:
            try:
                return len(self._encoding.encode(text))
            except Exception:
                pass

        return max(1, len(text.split()))

    def _split_paragraphs(self, text: str) -> list[str]:
        text = self._normalize_text(text)
        if not text:
            return []

        parts = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        return parts if parts else [text]

    def _split_sentences(self, text: str) -> list[str]:
        text = self._normalize_text(text)
        if not text:
            return []

        # Tách câu an toàn cho tiếng Anh + tiếng Việt + ký tự chấm câu phổ biến
        sentences = re.split(r"(?<=[.!?。！？])\s+", text)
        return [s.strip() for s in sentences if s.strip()]

    def _hard_split_by_tokens(self, text: str) -> list[str]:
        text = self._normalize_text(text)
        if not text:
            return []

        max_tokens = self.config.max_tokens
        overlap_tokens = self.config.overlap_tokens

        if self._encoding is not None:
            tokens = self._encoding.encode(text)
            if not tokens:
                return []

            chunks: list[str] = []
            start = 0
            while start < len(tokens):
                end = min(start + max_tokens, len(tokens))
                piece = self._encoding.decode(tokens[start:end]).strip()
                if piece:
                    chunks.append(piece)
                if end >= len(tokens):
                    break
                start = max(end - overlap_tokens, 0)
            return chunks

        words = text.split()
        if not words:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = min(start + max_tokens, len(words))
            piece = " ".join(words[start:end]).strip()
            if piece:
                chunks.append(piece)
            if end >= len(words):
                break
            start = max(end - overlap_tokens, 0)

        return chunks

    def _extract_units(self, text: str) -> list[str]:
        paragraphs = self._split_paragraphs(text)
        if len(paragraphs) >= 2:
            return paragraphs

        sentences = self._split_sentences(text)
        if len(sentences) >= 2:
            return sentences

        text = self._normalize_text(text)
        return [text] if text else []

    def _pack_units(self, units: list[str]) -> list[str]:
        chunks: list[str] = []
        current_parts: list[str] = []
        current_tokens = 0

        def flush_current() -> None:
            nonlocal current_parts, current_tokens
            piece = "\n\n".join(current_parts).strip()
            if piece:
                chunks.append(piece)
            current_parts = []
            current_tokens = 0

        for unit in units:
            unit = self._normalize_text(unit)
            if not unit:
                continue

            unit_tokens = self._count_tokens(unit)

            if unit_tokens > self.config.max_tokens:
                if current_parts:
                    flush_current()
                chunks.extend(self._hard_split_by_tokens(unit))
                continue

            if current_parts and current_tokens + unit_tokens > self.config.max_tokens:
                flush_current()

                # overlap bằng phần cuối của chunk trước
                if chunks:
                    last_piece = chunks[-1]
                    tail_units = self._split_sentences(last_piece) or [last_piece]
                    overlap_parts: list[str] = []
                    overlap_tokens = 0

                    for u in reversed(tail_units):
                        ut = self._count_tokens(u)
                        if overlap_tokens + ut > self.config.overlap_tokens:
                            break
                        overlap_parts.insert(0, u)
                        overlap_tokens += ut

                    if overlap_parts:
                        current_parts = overlap_parts[:]
                        current_tokens = overlap_tokens

            current_parts.append(unit)
            current_tokens += unit_tokens

        if current_parts:
            flush_current()

        return chunks

    def chunk(self, parsed: ParsedDocument) -> list[dict]:
        chunks: list[dict] = []
        index = 0

        for page_no, text in parsed.pages:
            text = self._normalize_text(text)
            if not text:
                continue

            units = self._extract_units(text)
            page_chunks = self._pack_units(units)

            for piece in page_chunks:
                piece = piece.strip()
                if not piece:
                    continue

                token_count = self._count_tokens(piece)
                if token_count < self.config.min_chunk_tokens:
                    continue

                chunks.append(
                    {
                        "chunk_index": index,
                        "chunk_text": piece,
                        "page_start": page_no,
                        "page_end": page_no,
                        "token_count": token_count,
                        "chunk_hash": hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                        "metadata_json": {
                            "source_page": page_no,
                            "source_page_start": page_no,
                            "source_page_end": page_no,
                        },
                    }
                )
                index += 1

        return chunks


chunker_service = ChunkerService(
    ChunkConfig(
        max_tokens=800,
        overlap_tokens=400,
        min_chunk_tokens=80,
    )
)
