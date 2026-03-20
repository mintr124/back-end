# Ingest → Embedding Backend

Backend FastAPI cho phase `ingest → embedding`, thi?t k? ?? x? l? upload t?i li?u, l?u file g?c, parse, chunk, embedding, v? l?u vector v?o ChromaDB.

Ki?n tr?c n?y ???c t?ch theo c?c l?p r? r?ng:

- `api/`: REST API
- `services/`: business logic
- `repositories/`: truy c?p d? li?u
- `models/`: ORM models
- `workers/`: Celery tasks
- `utils/`: helper thu?n k? thu?t

## M?c ti?u phase n?y

Phase n?y t?p trung v?o c?c n?ng l?c sau:

- t?o document
- upload file theo version
- ki?m tra quy?n update file theo role
- l?u file g?c v?o MinIO
- parse file
- chunk text
- t?o embedding
- l?u vector v?o ChromaDB
- theo d?i job ingest
- ghi audit log
- gi? s?n contract ?? n?i sang retrieval/generation sau n?y

## Stack c?ng ngh?

- FastAPI
- MySQL
- Redis
- Celery
- MinIO
- ChromaDB
- Docker Compose

## C?u tr?c th? m?c

```text
app/
  main.py

  core/
    config.py
    security.py
    logging.py
    deps.py
    exceptions.py

  db/
    base.py
    session.py
    init_db.py

  models/
    __init__.py
    user.py
    department.py
    project.py
    storage_object.py
    document.py
    document_version.py
    document_chunk.py
    chunk_embedding.py
    job.py
    job_step.py
    audit_log.py
    outbox_event.py
    policy_snapshot.py

  schemas/
    __init__.py
    auth.py
    user.py
    document.py
    job.py
    audit.py
    health.py

  repositories/
    __init__.py
    user_repository.py
    department_repository.py
    project_repository.py
    document_repository.py
    version_repository.py
    chunk_repository.py
    job_repository.py
    audit_repository.py
    storage_repository.py
    chroma_repository.py

  services/
    __init__.py
    auth_service.py
    permission_service.py
    storage_service.py
    parser_service.py
    chunker_service.py
    embedding_service.py
    chroma_service.py
    audit_service.py
    job_service.py
    document_service.py
    ingest_pipeline_service.py
    bootstrap_service.py

  api/
    __init__.py
    v1/
      __init__.py
      auth.py
      documents.py
      jobs.py
      health.py
      admin.py

  workers/
    __init__.py
    tasks.py
    ingest_tasks.py
    maintenance_tasks.py

  utils/
    __init__.py
    checksum.py
    text_normalizer.py
    file_parser.py
    chunking.py
    time.py
    ids.py

tests/
  test_auth.py
  test_permissions.py
  test_documents.py
  test_ingest_pipeline.py

alembic/
  versions/
  env.py
  script.py.mako
```
