from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn as docx_qn
from PIL import Image
import fitz
import pytesseract
import logging
logger = logging.getLogger(__name__)

from app.utils.text_normalizer import normalize_text


@dataclass
class ParsedDocument:
    pages: list[tuple[int, str]]
    full_text: str


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _rects_overlap(r1: tuple, r2: tuple, margin: float = 2.0) -> bool:
    """Return True if two (x0, y0, x1, y1) rectangles overlap."""
    return not (
        r1[2] + margin < r2[0]
        or r2[2] + margin < r1[0]
        or r1[3] + margin < r2[1]
        or r2[3] + margin < r1[1]
    )


def _merge_complementary_cols(rows: list[list[str]]) -> list[list[str]]:
    """Merge adjacent column pairs caused by PDF merged-cell artifacts.

    Two adjacent columns are mergeable when they never simultaneously contain
    *different* non-empty values — i.e., each row either:
      • has at most one non-empty cell (true complementary split), OR
      • has the same non-empty value in both (duplicate from a spanning cell).

    This handles both the simple case (empty | value) and the duplicate case
    ((b) | (b)) that PyMuPDF produces for some merged-cell layouts.
    """
    if not rows or len(rows[0]) < 2:
        return rows

    n_rows = len(rows)
    n_cols = len(rows[0])
    col_groups: list[list[str]] = []

    col = 0
    while col < n_cols:
        if col + 1 < n_cols:
            # Mergeable = never have two *different* non-empty values in same row
            mergeable = all(
                not (rows[r][col] and rows[r][col + 1]
                     and rows[r][col] != rows[r][col + 1])
                for r in range(n_rows)
            )
            if mergeable:
                merged = [rows[r][col] or rows[r][col + 1] for r in range(n_rows)]
                col_groups.append(merged)
                col += 2
                continue
        col_groups.append([rows[r][col] for r in range(n_rows)])
        col += 1

    n_out = len(col_groups)
    return [[col_groups[c][r] for c in range(n_out)] for r in range(n_rows)]


def _table_to_markdown(table) -> str:
    """Convert a PyMuPDF Table object to a Markdown table string.
    Handles merged-cell artifacts: removes fully-empty columns, then merges
    adjacent complementary column pairs caused by spanned cells.
    """
    try:
        rows = table.extract()
        if not rows:
            return ""

        # Clean and pad all rows to same column count
        ncols = max(len(r) for r in rows)
        cleaned: list[list[str]] = []
        for row in rows:
            padded = (list(row) + [None] * ncols)[:ncols]
            cleaned.append([str(cell or "").strip().replace("\n", " ") for cell in padded])

        if not cleaned:
            return ""

        # Step 1: drop columns that are entirely empty
        non_empty_cols = [
            col for col in range(ncols)
            if any(cleaned[r][col] for r in range(len(cleaned)))
        ]
        if not non_empty_cols:
            return ""
        filtered = [[row[col] for col in non_empty_cols] for row in cleaned]

        # Step 2: merge adjacent complementary column pairs (merged-cell artifact)
        filtered = _merge_complementary_cols(filtered)

        n = len(filtered[0])
        lines = [
            "| " + " | ".join(filtered[0]) + " |",
            "| " + " | ".join("---" for _ in range(n)) + " |",
        ]
        for row in filtered[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("_table_to_markdown failed: %s", e)
        return ""


def _apply_markdown_fmt(text: str, is_bold: bool, is_italic: bool) -> str:
    """Wrap non-empty text with **bold** / *italic* markers, preserving surrounding spaces."""
    stripped = text.strip()
    if not stripped:
        return text
    prefix = text[: len(text) - len(text.lstrip())]
    suffix = text[len(text.rstrip()) :]
    if is_bold and is_italic:
        return f"{prefix}***{stripped}***{suffix}"
    if is_bold:
        return f"{prefix}**{stripped}**{suffix}"
    if is_italic:
        return f"{prefix}*{stripped}*{suffix}"
    return text


_BOLD_FONT_HINTS = frozenset({"bold", "black", "heavy", "demi", "semibold", "extrabold"})


def _block_to_formatted_text(block: dict) -> str:
    """Convert a PyMuPDF dict-mode text block to a string with Markdown formatting.

    Detects bold/italic from span flags (bit 16 = bold, bit 2 = italic) and
    from font name keywords as a fallback for PDFs that don't set flags correctly.
    """
    line_texts: list[str] = []
    for line in block.get("lines", []):
        span_parts: list[str] = []
        for span in line.get("spans", []):
            raw = span.get("text", "")
            if not raw:
                continue
            flags: int = span.get("flags", 0)
            font: str = (span.get("font") or "").lower()
            is_bold = bool(flags & 16) or any(h in font for h in _BOLD_FONT_HINTS)
            is_italic = bool(flags & 2) or "italic" in font or "oblique" in font
            span_parts.append(_apply_markdown_fmt(raw, is_bold, is_italic))
        line_text = "".join(span_parts).rstrip("\n")
        if line_text.strip():
            line_texts.append(line_text)
    return "\n".join(line_texts).strip()


def _extract_pdf_page(page: fitz.Page) -> str:
    """
    Extract text from one PDF page.
    - Detected tables → Markdown table syntax.
    - Other text → Markdown bold/italic based on font flags from get_text("dict").
    Content inside table bounding boxes is not duplicated.
    """
    try:
        tab_finder = page.find_tables()
        tables = tab_finder.tables if tab_finder else []
    except Exception:
        tables = []

    logger.info("PDF page %s: find_tables detected %d table(s)", page.number + 1, len(tables))
    table_rects = [tuple(t.bbox) for t in tables]

    # Use dict mode to get per-span font/flag information
    page_dict = page.get_text("dict")
    items: list[tuple[float, str]] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:      # skip image/drawing blocks
            continue
        bx0, by0, bx1, by1 = block["bbox"]
        if table_rects and any(_rects_overlap((bx0, by0, bx1, by1), tr) for tr in table_rects):
            continue                    # covered by table markdown below
        block_text = _block_to_formatted_text(block)
        if block_text:
            items.append((by0, block_text))

    # Add each table as Markdown at its vertical position
    for table in tables:
        md = _table_to_markdown(table)
        if md:
            items.append((table.bbox[1], md))

    # Sort by vertical position to preserve reading order
    items.sort(key=lambda x: x[0])
    return "\n\n".join(text for _, text in items)


# ---------------------------------------------------------------------------
# DOCX helpers
# ---------------------------------------------------------------------------

def _docx_to_text(raw_bytes: bytes) -> str:
    """
    Parse a DOCX file preserving both paragraph text and table content
    (as Markdown tables) in document order.
    """
    doc = DocxDocument(BytesIO(raw_bytes))

    parts: list[str] = []
    for child in doc.element.body:
        tag = child.tag

        if tag == docx_qn("w:p"):
            # Top-level paragraph: collect runs with bold/italic detection
            run_parts: list[str] = []
            for run in child.iter(docx_qn("w:r")):
                rpr = run.find(docx_qn("w:rPr"))
                is_bold = rpr is not None and rpr.find(docx_qn("w:b")) is not None
                is_italic = rpr is not None and rpr.find(docx_qn("w:i")) is not None
                raw = "".join(node.text or "" for node in run.iter(docx_qn("w:t")))
                if raw:
                    run_parts.append(_apply_markdown_fmt(raw, is_bold, is_italic))
            text = "".join(run_parts).strip()
            if text:
                parts.append(text)

        elif tag == docx_qn("w:tbl"):
            # Table: convert directly from XML to avoid nested-para confusion
            rows: list[list[str]] = []
            for row_elem in child.iter(docx_qn("w:tr")):
                cells: list[str] = []
                for cell_elem in row_elem.findall(".//" + docx_qn("w:tc")):
                    cell_text = "".join(
                        node.text or "" for node in cell_elem.iter(docx_qn("w:t"))
                    )
                    cells.append(cell_text.strip().replace("\n", " "))
                if cells:
                    rows.append(cells)

            if rows:
                ncols = max(len(r) for r in rows)
                header = (rows[0] + [""] * ncols)[:ncols]
                lines = [
                    "| " + " | ".join(header) + " |",
                    "| " + " | ".join("---" for _ in range(ncols)) + " |",
                ]
                for row in rows[1:]:
                    padded = (row + [""] * ncols)[:ncols]
                    lines.append("| " + " | ".join(padded) + " |")
                parts.append("\n".join(lines))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_file_bytes(raw_bytes: bytes, filename: str, mime_type: str) -> ParsedDocument:
    ext = Path(filename).suffix.lower()
    pages: list[tuple[int, str]] = []

    if ext in {".txt", ".md", ".csv", ".log"} or mime_type.startswith("text/"):
        text = raw_bytes.decode("utf-8", errors="ignore")
        pages = [(1, text)]

    elif ext == ".pdf" or mime_type == "application/pdf":
        doc = fitz.open(stream=BytesIO(raw_bytes), filetype="pdf")
        for i, page in enumerate(doc, start=1):
            pages.append((i, _extract_pdf_page(page)))
        doc.close()

    elif ext == ".docx" or mime_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }:
        text = _docx_to_text(raw_bytes)
        pages = [(1, text)]

    elif ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} or mime_type.startswith("image/"):
        img = Image.open(BytesIO(raw_bytes))
        text = pytesseract.image_to_string(img)
        pages = [(1, text)]

    else:
        text = raw_bytes.decode("utf-8", errors="ignore")
        pages = [(1, text)]

    pages = [(p, normalize_text(t)) for p, t in pages if t and normalize_text(t)]
    full_text = normalize_text("\n\n".join(t for _, t in pages))
    if not full_text:
        raise ValueError("No extractable text in file")
    return ParsedDocument(pages=pages, full_text=full_text)


def _strip_headers_footers(pages: list[tuple[int, str]], threshold: int = 3) -> list[tuple[int, str]]:
    """Detect và strip các dòng lặp lại ở đầu/cuối nhiều trang (header/footer).
    threshold: xuất hiện ở ít nhất N trang thì coi là header/footer.
    """
    if len(pages) < 2:
        return pages

    from collections import Counter

    N = 3
    top_lines: list[list[str]] = []
    bot_lines: list[list[str]] = []
    for _, text in pages:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        top_lines.append(lines[:N])
        bot_lines.append(lines[-N:])

    top_counter: Counter = Counter()
    for lines in top_lines:
        for line in lines:
            if len(line) > 5:
                top_counter[line] += 1

    bot_counter: Counter = Counter()
    for lines in bot_lines:
        for line in lines:
            if len(line) > 5:
                bot_counter[line] += 1

    noise_lines = (
        {line for line, count in top_counter.items() if count >= threshold}
        | {line for line, count in bot_counter.items() if count >= threshold}
    )
    if not noise_lines:
        return pages

    logger.info("Detected %d header/footer patterns to strip", len(noise_lines))
    return [
        (page_no, "\n".join(l for l in text.splitlines() if l.strip() not in noise_lines).strip())
        for page_no, text in pages
    ]
