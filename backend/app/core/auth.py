from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _local_access_enabled() -> bool:
    settings = get_settings()
    return settings.local_access_enabled and settings.app_env.lower() in {"development", "test"}


async def _local_user(db: AsyncSession) -> User:
    """Return a non-persistent Elite/admin identity for local development only."""
    result = await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.id).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        return User(
            id=0,
            email="local-admin@localhost",
            hashed_password="",
            name="Local Admin",
            tier="elite",
            subscription_status="active",
            subscription_expires_at=datetime.now(timezone.utc) + timedelta(days=3650),
            is_active=True,
            is_admin=True,
        )
    # Build a detached request identity; do not rewrite account data.
    return User(
        id=user.id,
        email=user.email,
        hashed_password="",
        name=user.name,
        tier="elite",
        subscription_status="active",
        subscription_expires_at=datetime.now(timezone.utc) + timedelta(days=3650),
        phone=user.phone,
        timezone=user.timezone,
        is_active=True,
        is_admin=True,
    )


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def jwt_is_valid(token: str) -> bool:
    settings = get_settings()
    try:
        jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return True
    except JWTError:
        return False


def create_access_token(user_id: int, email: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_user(
    token: Optional[str] = Depends(optional_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if _local_access_enabled() and not token and db is not None:
        return await _local_user(db)
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    settings = get_settings()
    try:
        if not token:
            raise credentials_exc
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id: Optional[str] = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exc
    return user


async def get_current_user_optional(
    token: Optional[str] = Depends(optional_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Returns the user if a valid token is provided, otherwise None."""
    if not token and _local_access_enabled() and db is not None:
        return await _local_user(db)
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id: Optional[str] = payload.get("sub")
        if user_id is None:
            return None
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
        return user if (user and user.is_active) else None
    except JWTError:
        return None


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Shared mutations require an active account with the persisted admin flag."""
    if not current_user.is_active or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
