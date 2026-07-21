"""
Service facade for parsing raw file bytes into a ParsedDocument.
"""
from app.utils.file_parser import ParsedDocument, parse_file_bytes


class ParserService:
    # Parse raw bytes using the file_parser utility; mime_type is e.g. 'application/pdf'.
    def parse(self, raw_bytes: bytes, filename: str, mime_type: str) -> ParsedDocument:
        return parse_file_bytes(raw_bytes, filename, mime_type)


# Module-level singleton; imported by the ingest pipeline.
parser_service = ParserService()
