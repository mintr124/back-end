from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
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


def parse_file_bytes(raw_bytes: bytes, filename: str, mime_type: str) -> ParsedDocument:
    ext = Path(filename).suffix.lower()
    pages: list[tuple[int, str]] = []

    if ext in {".txt", ".md", ".csv", ".log"} or mime_type.startswith("text/"):
        text = raw_bytes.decode("utf-8", errors="ignore")
        pages = [(1, text)]

    elif ext == ".pdf" or mime_type == "application/pdf":
        doc = fitz.open(stream=BytesIO(raw_bytes), filetype="pdf")
        for i, page in enumerate(doc, start=1):
            pages.append((i, page.get_text("text") or ""))
        doc.close()

    elif ext == ".docx" or mime_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }:
        doc = DocxDocument(BytesIO(raw_bytes))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
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
    """
    Detect và strip các dòng lặp lại ở đầu/cuối nhiều trang (header/footer).
    threshold: xuất hiện ở ít nhất N trang thì coi là header/footer.
    """
    if len(pages) < 2:
        return pages

    # Lấy N dòng đầu và N dòng cuối của mỗi trang
    N = 3
    top_lines: list[list[str]] = []
    bot_lines: list[list[str]] = []

    for _, text in pages:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        top_lines.append(lines[:N])
        bot_lines.append(lines[-N:])

    # Đếm tần suất từng dòng ở đầu trang
    from collections import Counter
    top_counter: Counter = Counter()
    for lines in top_lines:
        for line in lines:
            if len(line) > 5:  # bỏ qua dòng quá ngắn
                top_counter[line] += 1

    bot_counter: Counter = Counter()
    for lines in bot_lines:
        for line in lines:
            if len(line) > 5:
                bot_counter[line] += 1

    # Dòng nào xuất hiện >= threshold trang → là header/footer
    header_lines = {line for line, count in top_counter.items() if count >= threshold}
    footer_lines = {line for line, count in bot_counter.items() if count >= threshold}
    noise_lines = header_lines | footer_lines

    if not noise_lines:
        return pages

    logger.info("Detected %d header/footer patterns to strip", len(noise_lines))

    # Strip khỏi từng trang
    cleaned: list[tuple[int, str]] = []
    for page_no, text in pages:
        lines = text.splitlines()
        filtered = [l for l in lines if l.strip() not in noise_lines]
        cleaned.append((page_no, "\n".join(filtered).strip()))

    return cleaned
