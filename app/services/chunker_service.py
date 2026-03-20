from app.utils.chunking import chunk_parsed_document
from app.utils.file_parser import ParsedDocument


class ChunkerService:
    def __init__(self, max_chars: int = 1200, overlap: int = 150):
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk(self, parsed: ParsedDocument) -> list[dict]:
        return chunk_parsed_document(parsed, max_chars=self.max_chars, overlap=self.overlap)


chunker_service = ChunkerService()
