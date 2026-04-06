from io import BytesIO

from minio import Minio
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.storage_object import StorageObject
from app.utils.checksum import sha256_bytes


class StorageRepository:
    def __init__(self):
        self.client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_buckets(self):
        for bucket in [settings.minio_bucket_raw, settings.minio_bucket_processed]:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    def checksum(self, data: bytes) -> str:
        return sha256_bytes(data)

    def put_bytes(
        self,
        db: Session,
        *,
        data: bytes,
        bucket: str,
        object_key: str,
        original_filename: str,
        content_type: str,
        object_kind: str,
    ) -> StorageObject:
        self.ensure_buckets()
        self.client.put_object(
            bucket_name=bucket,
            object_name=object_key,
            data=BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        obj = StorageObject(
            provider="minio",
            bucket=bucket,
            object_key=object_key,
            object_kind=object_kind,
            original_filename=original_filename,
            content_type=content_type,
            size_bytes=len(data),
            checksum=self.checksum(data),
        )
        db.add(obj)
        db.flush()
        return obj

    def put_text(
        self,
        db: Session,
        *,
        text: str,
        bucket: str,
        object_key: str,
        original_filename: str,
        content_type: str = "text/plain",
        object_kind: str = "normalized_text",
    ) -> StorageObject:
        return self.put_bytes(
            db,
            data=text.encode("utf-8"),
            bucket=bucket,
            object_key=object_key,
            original_filename=original_filename,
            content_type=content_type,
            object_kind=object_kind,
        )

    def get_bytes(self, bucket: str, object_key: str) -> bytes:
        self.ensure_buckets()
        response = self.client.get_object(bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
            
    def get_presigned_url(self, bucket: str, object_key: str, expires_minutes: int = 15) -> str:
        from datetime import timedelta
        import re

        # Ký bằng internal endpoint (minio:9000) — container có thể kết nối được
        url = self.client.presigned_get_object(
            bucket_name=bucket,
            object_name=object_key,
            expires=timedelta(minutes=expires_minutes),
        )

        # Replace internal host → public host để browser truy cập được
        # Signature vẫn hợp lệ vì MinIO không kiểm tra Host trong chữ ký
        public_endpoint = settings.minio_public_endpoint  # "localhost:9000"
        url = re.sub(r"https?://[^/]+", f"http://{public_endpoint}", url)

        return url
