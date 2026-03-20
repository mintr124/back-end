from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.storage_object import StorageObject
from app.repositories.storage_repository import StorageRepository


class StorageService:
    def __init__(self):
        self.repo = StorageRepository()

    def ensure_buckets(self):
        self.repo.ensure_buckets()

    def checksum(self, data: bytes) -> str:
        return self.repo.checksum(data)

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

    def download(self, bucket: str, object_key: str) -> bytes:
        return self.repo.get_bytes(bucket, object_key)


storage_service = StorageService()
