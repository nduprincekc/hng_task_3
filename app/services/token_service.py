import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.models import RefreshToken, User
import os
import uuid


JWT_SECRET = os.getenv("JWT_SECRET", "fallback_secret_change_this")
ACCESS_TOKEN_EXPIRE_SECONDS = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", 86400))
REFRESH_TOKEN_EXPIRE_SECONDS = int(os.getenv("REFRESH_TOKEN_EXPIRE_SECONDS", 604800))
ALGORITHM = "HS256"


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=ACCESS_TOKEN_EXPIRE_SECONDS)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "email": user.email,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def generate_refresh_token(user_id: str, db: Session) -> str:
    raw_token = secrets.token_hex(64)
    token_hash = hash_token(raw_token)
    expires_at = utcnow() + timedelta(seconds=REFRESH_TOKEN_EXPIRE_SECONDS)

    db_token = RefreshToken(
        id=str(uuid.uuid4()),
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(db_token)
    db.commit()

    return raw_token


def rotate_refresh_token(raw_token: str, db: Session):
    token_hash = hash_token(raw_token)

    stored = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash
    ).first()

    if not stored:
        raise ValueError("Invalid refresh token")

    if stored.used_at is not None:
        raise ValueError("Refresh token already used")

    if stored.expires_at < utcnow():
        raise ValueError("Refresh token expired")

    stored.used_at = utcnow()
    db.commit()

    user = db.query(User).filter(User.id == stored.user_id).first()
    if not user:
        raise ValueError("User not found")

    if not user.is_active:
        raise ValueError("Account is disabled")

    access_token = generate_access_token(user)
    new_refresh_token = generate_refresh_token(user.id, db)

    return access_token, new_refresh_token, user


def invalidate_refresh_token(token: str, db: Session):
    token_hash = hash_token(token)
    stored = db.query(RefreshToken).filter(
        RefreshToken.token_hash == token_hash
    ).first()
    if stored:
        stored.used_at = utcnow()
        db.commit()


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise ValueError("Invalid or expired access token")
