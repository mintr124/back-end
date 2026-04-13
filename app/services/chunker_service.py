"""
chunker_service.py  –  Semantic-structural chunker (v2)
========================================================
Strategy:
  1. Detect headings → split document into sections
  2. Within each section, pack sentences into chunks respecting max_tokens
  3. Overlap is carried as a "tail window" of the previous chunk (sentence-level)
  4. Every chunk carries rich metadata: heading, section_index, position_ratio

Why these numbers:
  max_tokens  = 512   → sweet spot for text-embedding-3-small (1536-dim)
  overlap     = 80    → ~15 % of max; enough context without heavy duplication
  min_tokens  = 60    → discard noise fragments
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

try:
    import tiktoken
except ImportError:
    tiktoken = None

from app.utils.file_parser import ParsedDocument


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ChunkConfig:
    max_tokens: int     = 512
    overlap_tokens: int = 80
    min_chunk_tokens: int = 60
    encoding_name: str  = "cl100k_base"


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(
    r"""
    (?:
        ^\s*(?:chương|phần|mục|section|chapter)\s+[\dIVXivx]+[\.:]\s*.+
        |
        ^\s*(?:Điều|điều)\s+\d+[\.\:]\s*.+
        |
        ^\s*[A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠƯ]{4,}(?:\s+[A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠƯ]+)*\s*$
    )
    """,
    re.VERBOSE | re.MULTILINE | re.UNICODE | re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")

_PAGE_HEADER_STRIP_RE = re.compile(
    r"^.*?(CÔNG TY TNHH|Mã tài liệu|Lần ban hành|Ngày hiệu lực).*$",
    re.MULTILINE | re.IGNORECASE
)

_PAGE_HEADER_CHECK_RE = re.compile(  #
    r"CÔNG TY TNHH|Mã tài liệu|Lần ban hành|Ngày hiệu lực",
    re.IGNORECASE
)


def _is_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 120:
        return False
    if _PAGE_HEADER_CHECK_RE.search(line):  
        return False
    if line.endswith((",", "và", "hoặc", "của", "theo", "để", "trong", "khi",
                      "là", "các", "được", "có", "về", "với", "tại", "cho",
                      "từ", "như", "này")):
        return False
    return bool(_HEADING_RE.match(line))


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class ChunkerService:
    def __init__(self, config: ChunkConfig | None = None):
        self.cfg = config or ChunkConfig()
        self._enc = None
        if tiktoken is not None:
            try:
                self._enc = tiktoken.get_encoding(self.cfg.encoding_name)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    def _count(self, text: str) -> int:
        text = (text or "").strip()
        if not text:
            return 0
        if self._enc:
            try:
                return len(self._enc.encode(text))
            except Exception:
                pass
        return max(1, len(text.split()))

    def _decode(self, tokens: list) -> str:
        if self._enc:
            return self._enc.decode(tokens)
        return " ".join(str(t) for t in tokens)

    # ------------------------------------------------------------------
    # Text splitting helpers
    # ------------------------------------------------------------------

    def _split_sentences(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
        return [p.strip() for p in parts if p.strip()]

    def _hard_split(self, text: str) -> list[str]:
        """Token-level hard split for single oversized units."""
        text = (text or "").strip()
        if not text:
            return []
        max_t = self.cfg.max_tokens
        ovlp  = self.cfg.overlap_tokens

        if self._enc:
            toks = self._enc.encode(text)
            chunks, start = [], 0
            while start < len(toks):
                end = min(start + max_t, len(toks))
                piece = self._enc.decode(toks[start:end]).strip()
                if piece:
                    chunks.append(piece)
                if end >= len(toks):
                    break
                start = end - ovlp
            return chunks

        words = text.split()
        chunks, start = [], 0
        while start < len(words):
            end = min(start + max_t, len(words))
            piece = " ".join(words[start:end]).strip()
            if piece:
                chunks.append(piece)
            if end >= len(words):
                break
            start = end - ovlp
        return chunks

    # ------------------------------------------------------------------
    # Section detection  (splits full page text into labeled sections)
    # ------------------------------------------------------------------

    def _split_into_sections(self, text: str) -> list[tuple[str | None, str]]:
        """
        Returns list of (heading_or_None, body_text).
        A "section" is everything between two headings.
        """
        lines = text.splitlines()
        sections: list[tuple[str | None, str]] = []
        current_heading: str | None = None
        buffer: list[str] = []

        def flush():
            body = "\n".join(buffer).strip()
            if body:
                sections.append((current_heading, body))

        for line in lines:
            if _is_heading(line):
                flush()
                current_heading = line.strip()
                buffer = []
            else:
                buffer.append(line)

        flush()

        # Edge-case: no headings found → single section
        if not sections:
            sections = [(None, text.strip())]

        return sections

    # ------------------------------------------------------------------
    # Pack sentences into chunks (with overlap)
    # ------------------------------------------------------------------

    def _pack_sentences(self, sentences: list[str]) -> list[str]:
        max_t = self.cfg.max_tokens
        ovlp  = self.cfg.overlap_tokens
        chunks: list[str] = []
        buf: list[str] = []
        buf_tokens = 0

        def flush():
            piece = " ".join(buf).strip()
            if piece:
                chunks.append(piece)

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            st = self._count(sent)

            # oversized single sentence → hard split
            if st > max_t:
                if buf:
                    flush()
                    buf, buf_tokens = [], 0
                chunks.extend(self._hard_split(sent))
                continue

            if buf and buf_tokens + st > max_t:
                flush()
                # carry-over overlap from tail of current buffer
                tail: list[str] = []
                tail_tokens = 0
                for s in reversed(buf):
                    t = self._count(s)
                    if tail_tokens + t > ovlp:
                        break
                    tail.insert(0, s)
                    tail_tokens += t
                buf = tail
                buf_tokens = tail_tokens

            buf.append(sent)
            buf_tokens += st

        if buf:
            flush()

        return chunks

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk(self, parsed: ParsedDocument) -> list[dict]:
        results: list[dict] = []
        global_index = 0
        total_pages = max(1, len(parsed.pages))

        # Gộp toàn bộ text, bỏ header/footer trước
        full_text = ""
        for page_no, page_text in parsed.pages:
            page_text = (page_text or "").strip()
            page_text = _PAGE_HEADER_STRIP_RE.sub("", page_text).strip()
            if page_text:
                full_text += "\n\n" + page_text

        full_text = full_text.strip()
        if not full_text:
            return results

        sections = self._split_into_sections(full_text)
        total_sections = max(1, len(sections))

        for sec_idx, (heading, body) in enumerate(sections):
            sentences = self._split_sentences(body)
            if not sentences:
                continue

            raw_chunks = self._pack_sentences(sentences)

            for local_i, piece in enumerate(raw_chunks):
                piece = piece.strip()
                if not piece:
                    continue

                token_count = self._count(piece)
                if token_count < self.cfg.min_chunk_tokens:
                    continue

                position_ratio = round(sec_idx / total_sections, 4)
                embed_text = f"{heading}\n\n{piece}" if heading else piece

                results.append({
                    "chunk_index":   global_index,
                    "chunk_text":    piece,
                    "embed_text":    embed_text,
                    "page_start":    1,   # không còn track per-page
                    "page_end":      total_pages,
                    "token_count":   token_count,
                    "chunk_hash":    hashlib.sha256(piece.encode()).hexdigest(),
                    "metadata_json": {
                        "section_index":    sec_idx,
                        "section_heading":  heading or "",
                        "position_ratio":   position_ratio,
                        "local_chunk_index": local_i,
                    },
                })
                global_index += 1

        return results


# Singleton
chunker_service = ChunkerService(
    ChunkConfig(
        max_tokens=512,
        overlap_tokens=40,
        min_chunk_tokens=20,
    )
)