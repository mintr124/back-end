from app.utils.chunking import chunk_parsed_document
from app.utils.file_parser import ParsedDocument
from app.services.embedding_service import EmbeddingService


def test_chunking():
    parsed = ParsedDocument(pages=[(1, "hello world " * 200)], full_text="hello world " * 200)
    chunks = chunk_parsed_document(parsed, max_chars=100, overlap=20)
    assert len(chunks) > 0


def test_embedding_dims():
    svc = EmbeddingService(dims=128)
    vec = svc.embed("hello world")
    assert len(vec) == 128
