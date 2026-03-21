# RAG-ROLE-ENTERPRISE

## Project Structure

- `api/` - REST API layer
- `services/` - Business logic
- `repositories/` - Database access layer
- `models/` - ORM models
- `workers/` - Celery tasks
- `utils/` - Helper utilities

## Tech Stack

FastAPI, MySQL, Redis, Celery, MinIO, ChromaDB, Docker Compose

## Run the Project

```bash
docker-compose up --build
```

## Access Links

- API Backend: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- MinIO: http://localhost:9001

## Data Storage

- MySQL: database storage
- ChromaDB: stored in the API service volume at `data/chromadb/chroma.sqlite3`
