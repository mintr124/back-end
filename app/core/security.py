"""
Security utilities: JWT token creation/decoding and bcrypt password hashing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


# Return the current UTC datetime as a timezone-aware object.
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# Create a signed JWT with the given payload and an optional custom expiry.
def create_access_token(data: dict[str, Any], expires_delta_minutes: int | None = None) -> str:
    payload = data.copy()
    expire = now_utc() + timedelta(minutes=expires_delta_minutes or settings.access_token_exp_minutes)
    payload.update({"exp": expire, "iat": now_utc()})
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


# Decode and verify a JWT; raise ValueError if the token is invalid or expired.
def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("Invalid token") from exc


# Hash a plaintext password with bcrypt and return the UTF-8 encoded digest.
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# Return True if the plaintext password matches the stored bcrypt hash.
def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))