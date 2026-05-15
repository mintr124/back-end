"""
chunker_service.py  –  v3  (Docling integration)
=================================================
Mode routing:
  legacy       → ChunkerService cũ (sentence-pack, tiktoken)   [default]
  hierarchical → Docling HierarchicalChunker
  hybrid       → Docling HybridChunker + HuggingFaceTokenizer

Tất cả mode đều trả về cùng list[dict] schema để ingest_pipeline_service
không cần biết mode nào đang chạy.

Output schema (mỗi dict):
  chunk_index       int
  chunk_text        str    — clean text lưu DB / Chroma documents
  embed_text        str    — có thể prefix section heading, dùng để embed
  page_start        int
  page_end          int
  token_count       int
  chunk_hash        str    (sha256 của chunk_text)
  metadata_json     dict   — section_heading, section_index, position_ratio, ...
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

try:
    import tiktoken
except ImportError:
    tiktoken = None

from app.utils.file_parser import ParsedDocument

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ChunkConfig:
    mode: str = "legacy"            # "legacy" | "hierarchical" | "hybrid"
    max_tokens: int = 512
    overlap_tokens: int = 80
    min_chunk_tokens: int = 60
    encoding_name: str = "cl100k_base"
    ocr: bool = False
    # Docling-specific
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Heading detection (dùng cho legacy mode)
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

_PAGE_HEADER_STRIP_RE = re.compile(
    r"^.{0,150}?(CÔNG TY TNHH TMGROUP\s*\|\s*Quy định|Mã tài liệu|Lần ban hành|Ngày hiệu lực).*$",
    re.MULTILINE | re.IGNORECASE,
)

_PAGE_HEADER_CHECK_RE = re.compile(
    r"CÔNG TY TNHH|Mã tài liệu|Lần ban hành|Ngày hiệu lực",
    re.IGNORECASE,
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
# Helpers dùng chung
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Docling helpers
# ---------------------------------------------------------------------------

def _build_docling_doc(parsed: ParsedDocument):
    """
    Tạo Docling Document từ ParsedDocument.
    Dùng HTML wrapper để Docling parse heading/table từ markdown-like text.
    Fallback: nếu không load được Docling thì raise ImportError sớm.
    """
    from docling.document_converter import DocumentConverter

    # Ghi text ra BytesIO dạng .txt rồi convert — cách đơn giản nhất
    # để tái dùng parser của Docling mà không cần file vật lý.
    # Docling hỗ trợ stream qua DocumentConverter.convert_bytes().
    text_bytes = parsed.full_text.encode("utf-8")
    try:
        converter = DocumentConverter()
        result = converter.convert_bytes(text_bytes, filename="document.txt")
        return result.document
    except Exception as exc:
        logger.warning("Docling convert_bytes failed (%s), fallback to raw text doc", exc)
        # Fallback: tạo DoclingDocument thủ công từ text
        from docling_core.types.doc import DoclingDocument, TextItem, DocItemLabel, ProvenanceItem
        doc = DoclingDocument(name="document")
        doc.add_text(label=DocItemLabel.PARAGRAPH, text=parsed.full_text)
        return doc


def _to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    for method in ("export_json_dict", "model_dump", "dict"):
        fn = getattr(obj, method, None)
        if callable(fn):
            try:
                return _to_jsonable(fn())
            except Exception:
                pass
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    return str(obj)


_SECTION_PATH_KEYS = (
    "section_path", "heading_path", "title_path", "headings",
    "heading", "title", "section", "outline", "outline_path",
    "breadcrumb", "breadcrumbs", "path",
)


def _extract_section_path(meta: Any) -> str:
    data = _to_jsonable(meta)
    if not isinstance(data, dict):
        return ""
    for key in _SECTION_PATH_KEYS:
        if key in data:
            value = data[key]
            if isinstance(value, list):
                parts = [str(p).strip() for p in value if str(p).strip()]
                if parts:
                    return " > ".join(parts)
            elif isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _extract_page_numbers(meta: Any) -> list[int]:
    data = _to_jsonable(meta)
    if not isinstance(data, dict):
        return []
    nums: set[int] = set()
    for key in ("page_start", "page_end", "page", "page_number", "page_no",
                "page_idx", "pages", "page_numbers"):
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, int):
            nums.add(val)
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, int):
                    nums.add(v)
    return sorted(nums)


# ---------------------------------------------------------------------------
# Docling chunker wrapper
# ---------------------------------------------------------------------------

def _chunk_with_docling(
    parsed: ParsedDocument,
    cfg: ChunkConfig,
) -> list[dict]:
    """
    Dùng Docling để chunk.
    Trả về list[dict] cùng schema với legacy chunker.
    """
    try:
        doc = _build_docling_doc(parsed)
    except ImportError as exc:
        raise RuntimeError(
            "Docling chưa được cài. Chạy: pip install docling docling-core"
        ) from exc

    # Build chunker theo mode
    if cfg.mode == "hierarchical":
        try:
            from docling.chunking import HierarchicalChunker
        except ImportError:
            from docling_core.transforms.chunker import HierarchicalChunker
        chunker = HierarchicalChunker()
        tokenizer = None
    else:  # hybrid
        from docling.chunking import HybridChunker
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
        from transformers import AutoTokenizer

        try:
            hf_tok = AutoTokenizer.from_pretrained(cfg.embed_model)
        except Exception as exc:
            raise RuntimeError(
                f"Không load được tokenizer '{cfg.embed_model}': {exc}"
            ) from exc

        tokenizer = HuggingFaceTokenizer(
            tokenizer=hf_tok,
            max_tokens=cfg.max_tokens,
        )
        chunker = HybridChunker(tokenizer=tokenizer)

    raw_chunks = list(chunker.chunk(doc))

    # Merge chunks cùng section nếu còn chỗ
    merged = _merge_same_section(raw_chunks, cfg.max_tokens)

    total_pages = max(1, len(parsed.pages))
    results: list[dict] = []

    for idx, item in enumerate(merged):
        text = item["text"]
        if not text:
            continue

        section_path = item["section_path"]
        page_numbers = item["page_numbers"]
        page_start = min(page_numbers) if page_numbers else 1
        page_end = max(page_numbers) if page_numbers else total_pages

        # Token count — dùng ước tính nếu không có tokenizer
        token_count = _estimate_tokens(text, tokenizer)

        embed_text = f"{section_path}\n\n{text}" if section_path else text
        position_ratio = round(idx / max(1, len(merged)), 4)

        results.append({
            "chunk_index":   idx,
            "chunk_text":    text,
            "embed_text":    embed_text,
            "page_start":    page_start,
            "page_end":      page_end,
            "token_count":   token_count,
            "chunk_hash":    _sha256(text),
            "metadata_json": {
                "section_index":     idx,
                "section_heading":   section_path,
                "position_ratio":    position_ratio,
                "local_chunk_index": 0,
                "chunker_mode":      cfg.mode,
            },
        })

    return results


def _merge_same_section(raw_chunks: list[Any], max_tokens: int) -> list[dict]:
    """Gộp các chunk nhỏ liên tiếp cùng section."""
    merged: list[dict] = []
    buffer: dict | None = None

    def flush(buf: dict | None) -> None:
        if buf:
            merged.append(buf)

    for chunk in raw_chunks:
        text = _normalize_text(getattr(chunk, "text", "") or "")
        if not text:
            continue

        meta_data = _to_jsonable(getattr(chunk, "meta", None))
        section_path = _extract_section_path(meta_data)
        page_numbers = _extract_page_numbers(meta_data)

        if buffer is None:
            buffer = {"text": text, "section_path": section_path, "page_numbers": page_numbers[:]}
            continue

        same_section = bool(section_path) and buffer["section_path"] == section_path
        candidate = buffer["text"] + "\n\n" + text
        candidate_tokens = _estimate_tokens(candidate)

        if same_section and candidate_tokens <= max_tokens:
            buffer["text"] = candidate
            buffer["page_numbers"].extend(page_numbers)
        else:
            flush(buffer)
            buffer = {"text": text, "section_path": section_path, "page_numbers": page_numbers[:]}

    flush(buffer)
    return merged


def _estimate_tokens(text: str, tokenizer: Any = None) -> int:
    if not text:
        return 0
    if tokenizer is not None:
        try:
            return int(tokenizer.count_tokens(text=text))
        except Exception:
            pass
    return max(1, (len(text) + 3) // 4)


# ---------------------------------------------------------------------------
# Legacy chunker (giữ nguyên từ v2, chỉ extract thành class riêng)
# ---------------------------------------------------------------------------

class _LegacyChunker:
    def __init__(self, cfg: ChunkConfig):
        self.cfg = cfg
        self._enc = None
        if tiktoken is not None:
            try:
                self._enc = tiktoken.get_encoding(cfg.encoding_name)
            except Exception:
                pass

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

    def _split_sentences(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
        return [p.strip() for p in parts if p.strip()]

    def _hard_split(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        max_t = self.cfg.max_tokens
        ovlp = self.cfg.overlap_tokens

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

    def _split_into_sections(self, text: str) -> list[tuple[str | None, str]]:
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
        return sections or [(None, text.strip())]

    def _pack_sentences(self, sentences: list[str]) -> list[str]:
        max_t = self.cfg.max_tokens
        ovlp = self.cfg.overlap_tokens
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

            if st > max_t:
                if buf:
                    flush()
                    buf, buf_tokens = [], 0
                chunks.extend(self._hard_split(sent))
                continue

            if buf and buf_tokens + st > max_t:
                flush()
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

    def chunk(self, parsed: ParsedDocument) -> list[dict]:
        results: list[dict] = []
        global_index = 0
        total_pages = max(1, len(parsed.pages))

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
                if token_count < self.cfg.min_chunk_tokens and results:
                    prev = results[-1]
                    prev["chunk_text"] += "\n\n" + piece
                    prev["embed_text"] += "\n\n" + piece
                    prev["token_count"] += token_count
                    continue

                position_ratio = round(sec_idx / total_sections, 4)
                embed_text = f"{heading}\n\n{piece}" if heading else piece

                results.append({
                    "chunk_index":   global_index,
                    "chunk_text":    piece,
                    "embed_text":    embed_text,
                    "page_start":    1,
                    "page_end":      total_pages,
                    "token_count":   token_count,
                    "chunk_hash":    _sha256(piece),
                    "metadata_json": {
                        "section_index":     sec_idx,
                        "section_heading":   heading or "",
                        "position_ratio":    position_ratio,
                        "local_chunk_index": local_i,
                        "chunker_mode":      "legacy",
                    },
                })
                global_index += 1

        return results


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------

class ChunkerService:
    """
    Facade thống nhất — chọn backend dựa trên cfg.mode.
    """

    def __init__(self, config: ChunkConfig | None = None):
        self.cfg = config or ChunkConfig()

    def chunk(self, parsed: ParsedDocument, config: ChunkConfig | None = None) -> list[dict]:
        """
        chunk() nhận optional config để override per-request.
        ingest_pipeline_service gọi:
            chunker_service.chunk(parsed, config=ChunkConfig.from_json(version.chunk_config_json))
        """
        cfg = config or self.cfg

        if cfg.mode in ("hierarchical", "hybrid"):
            logger.info("Chunking với Docling mode=%s max_tokens=%d", cfg.mode, cfg.max_tokens)
            try:
                return _chunk_with_docling(parsed, cfg)
            except Exception as exc:
                logger.error(
                    "Docling chunking thất bại (mode=%s): %s. Fallback về legacy.",
                    cfg.mode, exc,
                )
                # Fallback về legacy nếu Docling lỗi
                fallback_cfg = ChunkConfig(
                    mode="legacy",
                    max_tokens=cfg.max_tokens,
                    overlap_tokens=cfg.overlap_tokens,
                    min_chunk_tokens=60,
                )
                return _LegacyChunker(fallback_cfg).chunk(parsed)

        # mode == "legacy" (default)
        return _LegacyChunker(cfg).chunk(parsed)


# Singleton — dùng cho backward compat (code cũ gọi chunker_service.chunk(parsed))
chunker_service = ChunkerService(
    ChunkConfig(
        mode="legacy",
        max_tokens=512,
        overlap_tokens=40,
        min_chunk_tokens=10,
    )
)