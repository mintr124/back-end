from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from PIL import Image
import fitz
import pytesseract

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
