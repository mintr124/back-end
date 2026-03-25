import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1.auth import router as auth_router
from app.api.v1.documents import router as documents_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.health import router as health_router
from app.api.v1.admin import router as admin_router
from app.api.v1.chat import router as chat_router
from app.api.v1.users import router as users_router
from app.core.exceptions import register_exception_handlers
from app.db.init_db import init_db
from app.db.session import SessionLocal, engine, get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.health import HealthResponse
from app.services.auth_service import auth_service
from app.services.bootstrap_service import bootstrap_service
from app.services.document_service import document_service
from app.services.job_service import job_service
from app.services.storage_service import storage_service
from app.workers.ingest_tasks import process_ingest_job
from app.core.logging import configure_logging

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

configure_logging()

app = FastAPI(title="rag-role-enterprise-api", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8083",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    request.state.trace_id = request.headers.get("X-Trace-Id", uuid.uuid4().hex)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


def get_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", uuid.uuid4().hex)


def wait_for_database():
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("MySQL is not ready")


@app.on_event("startup")
def startup_event():
    wait_for_database()
    init_db()
    with SessionLocal() as db:
        bootstrap_service.seed_defaults(db)
    for _ in range(30):
        try:
            storage_service.ensure_buckets()
            break
        except Exception:
            time.sleep(2)


def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    payload = auth_service.decode_access_token(token)
    user = db.get(User, payload["sub"])
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(documents_router, prefix="/documents", tags=["documents"])
app.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])
app.include_router(chat_router, prefix="", tags=["chat"])
app.include_router(users_router, prefix="", tags=["users"])
