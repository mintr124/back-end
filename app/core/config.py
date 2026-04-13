from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "rag-role-enterprise-api"
    env: str = "dev"

    database_url: str = "mysql+pymysql://rag:rag@mysql:3306/ragdb?charset=utf8mb4"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret_key: str = "secret"
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 1440; 
    
    openfga_url: str = "http://openfga:8080"; 
    openfga_store_id: str = "01KNGYDDJ052B55E56R9SSJY4W"  
    openfga_model_id: str = "01KNGYDDKXYJF7C73FZKGHF7K2"

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_public_endpoint: str = "localhost:9000"
    minio_bucket_raw: str = "raw"
    minio_bucket_processed: str = "processed"

    chroma_path: str = "/data/chroma"
    chroma_collection: str = "document_chunks"
    chroma_host: str = "chroma"       
    chroma_port: int = 8000  

    max_upload_size_mb: int = 50
    default_policy_version: str = "v1"


    # LLM configuration
    # Set to 'openai' or 'olama' to enable an LLM backend. If None, LLM calls are disabled.
    llm_provider: str | None = None

    # OpenAI settings
    openai_api_key: str | None = None
    openai_api_base: str | None = None
    openai_model: str | None = "gpt-4o-mini"
    openai_embedding_model: str | None = "text-embedding-3-small"
    openai_embedding_dims: int = 1536


    # Olama local server settings (e.g. http://localhost:11434)
    olama_url: str | None = None
    olama_model: str | None = None

    # Timeout for LLM HTTP calls
    llm_timeout_seconds: int = 30


settings = Settings()
