from app.utils.file_parser import ParsedDocument, parse_file_bytes

# mime_type is like 'application/pdf' or 'text/plain'
class ParserService:
    def parse(self, raw_bytes: bytes, filename: str, mime_type: str) -> ParsedDocument:
        return parse_file_bytes(raw_bytes, filename, mime_type)


parser_service = ParserService()
