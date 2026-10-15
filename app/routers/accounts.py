from datetime import timedelta, timezone
from secrets import token_hex, token_urlsafe

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import (
    get_current_user,
    get_current_user_session,
    hash_api_key,
    hash_password,
    utc_now,
    verify_password,
)
from ..database import get_db
from ..models import Tenant, User, UserSession
from ..schemas import SessionTokenResponse, UserLogin, UserRegister, UserResponse


router = APIRouter(prefix="/auth", tags=["Accounts"])
SESSION_LIFETIME = timedelta(hours=24)
UNKNOWN_USER_HASH = hash_password("unknown-user-placeholder")


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    if db.scalar(select(User.id).where(User.email == payload.email)) is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Personal-space API keys are not exposed; users authenticate with sessions.
    tenant = Tenant(
        name=f"personal-{token_hex(16)}",
        api_key_hash=hash_api_key(token_urlsafe(32)),
    )
    user = User(
        tenant=tenant,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from exc
    db.refresh(user)
    return user


@router.post("/login", response_model=SessionTokenResponse)
def login(
    payload: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == payload.email))
    stored_hash = user.password_hash if user is not None else UNKNOWN_USER_HASH
    if not verify_password(payload.password, stored_hash) or user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = f"tps_{token_urlsafe(32)}"
    expires_at = utc_now() + SESSION_LIFETIME
    db.add(UserSession(
        user_id=user.id,
        token_hash=hash_api_key(token),
        expires_at=expires_at,
    ))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.replace(tzinfo=timezone.utc),
    }


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    session: UserSession = Depends(get_current_user_session),
    db: Session = Depends(get_db),
):
    session.revoked_at = utc_now()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
