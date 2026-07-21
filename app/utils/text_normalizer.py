"""
Text normalisation utilities: NFKC unicode, whitespace collapsing, hyphen-break removal, bullet stripping.
"""
from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200D\uFEFF]")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_HYPHEN_LINEBREAK_RE = re.compile(r"(?<=\w)-\n(?=\w)")
# Match bullet-list markers (hyphen/bullet/single-asterisk + space) but NOT
# markdown bold (**text**) or italic (*text*) which have no trailing space.
_BULLET_RE = re.compile(
    r"^[ \t]*[-\u2022\u00B7\u2219][ \t]+|^[ \t]*\*(?!\*)[ \t]+",
    re.MULTILINE,
)


# Normalise whitespace, unicode, hyphen line-breaks, and bullet markers in extracted text.
def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _HYPHEN_LINEBREAK_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)

    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()
