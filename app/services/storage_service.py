"""
Service facade for MinIO object storage: upload raw files and processed text, download bytes.
"""
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.storage_object import StorageObject
from app.repositories.storage_repository import StorageRepository


class StorageService:
    def __init__(self):
        self.repo = StorageRepository()

    # Create the raw and processed MinIO buckets if they do not exist.
    def ensure_buckets(self):
        self.repo.ensure_buckets()

    # Return the SHA-256 hex digest of a byte payload.
    def checksum(self, data: bytes) -> str:
        return self.repo.checksum(data)

    # Upload raw file bytes to the raw bucket and persist a StorageObject record.
    def upload_raw(
        self,
        db: Session,
        *,
        data: bytes,
        object_key: str,
        original_filename: str,
        content_type: str,
    ) -> StorageObject:
        return self.repo.put_bytes(
            db,
            data=data,
            bucket=settings.minio_bucket_raw,
            object_key=object_key,
            original_filename=original_filename,
            content_type=content_type,
            object_kind="raw_source",
        )

    # Upload UTF-8-encoded text to the processed bucket and persist a StorageObject record.
    def upload_processed_text(
        self,
        db: Session,
        *,
        text: str,
        object_key: str,
        original_filename: str,
    ) -> StorageObject:
        return self.repo.put_text(
            db,
            text=text,
            bucket=settings.minio_bucket_processed,
            object_key=object_key,
            original_filename=original_filename,
            content_type="text/plain",
            object_kind="normalized_text",
        )

    # Download and return the raw bytes for an object.
    def download(self, bucket: str, object_key: str) -> bytes:
        return self.repo.get_bytes(bucket, object_key)


# Module-level singleton; imported by the ingest pipeline and document service.
storage_service = StorageService()
