from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200D\uFEFF]")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_HYPHEN_LINEBREAK_RE = re.compile(r"(?<=\w)-\n(?=\w)")
_BULLET_RE = re.compile(r"^[\s\-\*\u2022\u00B7\u2219]+", re.MULTILINE)


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
