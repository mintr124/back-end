"""
Database engine and session factory. Provides the get_db dependency for FastAPI.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# SQLAlchemy engine with connection health checks and hourly pool recycling.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    future=True,
    execution_options={"isolation_level": "READ COMMITTED"},
)

# Session factory; expire_on_commit=False avoids lazy-load errors after commit.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


# FastAPI dependency that yields a DB session and closes it after the request.
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
