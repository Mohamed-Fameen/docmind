"""
Wraps `bcrypt` directly rather than through `passlib` — passlib (last released 2020, now
unmaintained) has a broken internal self-test against modern bcrypt package versions (>=4.1),
which surfaces as a confusing "password cannot be longer than 72 bytes" error even for a
short password like "testpass123", because the crash happens during passlib's own internal
test vector, not on the actual user input. Calling bcrypt directly avoids the broken
compatibility shim entirely, and is a thinner, more maintained dependency anyway.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models_db import User

# Tells FastAPI's OpenAPI docs where to send username/password to get a token — also used
# to extract the "Authorization: Bearer <token>" header from incoming requests automatically.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# bcrypt has a hard 72-BYTE input limit (not 72 characters — a password with multi-byte
# UTF-8 characters could hit this limit with fewer than 72 characters). Enforced explicitly
# here rather than silently truncating, since silent truncation would mean two different
# passwords past the limit hash identically without the user ever knowing why.
_MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at most {_MAX_PASSWORD_BYTES} bytes.",
        )
    hashed = bcrypt.hashpw(encoded, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def create_access_token(user_id: str) -> str:
    """
    A JWT is a signed (not encrypted) blob — anyone can read its contents, but only someone
    holding jwt_secret_key can have PRODUCED a validly-signed one. That's what lets the server
    trust a token's claims (here, just the user id) without a database round-trip on every
    request: verifying the signature is enough to know the token wasn't forged or tampered
    with, as long as the secret never leaks.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """
    FastAPI dependency — add `current_user: User = Depends(get_current_user)` to any route
    that should require authentication. Raises 401 for a missing, malformed, expired, or
    forged token, or one referencing a user that no longer exists.
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_error
    except jwt.PyJWTError:
        raise credentials_error

    user = db.get(User, user_id)
    if user is None:
        raise credentials_error
    return user
