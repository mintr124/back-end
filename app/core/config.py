from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ingest-embedding-api"
    env: str = "dev"

    database_url: str = "mysql+pymysql://rag:rag@mysql:3306/ragdb?charset=utf8mb4"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret_key: str = "change-me-please"
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 1440

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket_raw: str = "raw"
    minio_bucket_processed: str = "processed"

    chroma_path: str = "/data/chroma"
    chroma_collection: str = "document_chunks"

    embedding_dims: int = 384
    max_upload_size_mb: int = 50
    default_policy_version: str = "v1"


settings = Settings()
