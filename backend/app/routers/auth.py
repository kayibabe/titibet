from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
import jwt
from jwt import PyJWTError as JWTError
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, get_current_user, hash_password, verify_password
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import User
from app.services.email import send_welcome, send_password_reset

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    tier: str
    subscription_status: str
    subscription_expires_at: datetime | None
    timezone: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# ── Reset token helpers ───────────────────────────────────────────────────────

def _create_reset_token(user_id: int, email: str) -> str:
    s = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode(
        {"sub": str(user_id), "email": email, "exp": expire, "type": "reset"},
        s.jwt_secret, algorithm=s.jwt_algorithm,
    )


def _decode_reset_token(token: str) -> tuple[int, str]:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        if payload.get("type") != "reset":
            raise ValueError("Invalid token type")
        return int(payload["sub"]), payload["email"]
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        name=body.name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    background_tasks.add_task(send_welcome, user.email, user.name or "")
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")
    user.last_active_at = datetime.now(timezone.utc)
    await db.commit()
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    allowed = {"name", "timezone", "phone"}
    for field, value in body.items():
        if field in allowed:
            setattr(current_user, field, value)
    current_user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/change-password", response_model=UserOut)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.hashed_password = hash_password(body.new_password)
    current_user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Always return 200 to avoid exposing whether email is registered
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user and user.is_active:
        token = _create_reset_token(user.id, user.email)
        s = get_settings()
        reset_url = f"{s.app_url}/reset-password?token={token}"
        background_tasks.add_task(send_password_reset, user.email, reset_url)
    return {"message": "If that email is registered you will receive a reset link shortly."}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    user_id, email = _decode_reset_token(body.token)
    user = await db.get(User, user_id)
    if not user or user.email != email or not user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    user.hashed_password = hash_password(body.password)
    user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Password updated. You can now log in with your new password."}
