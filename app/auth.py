from hashlib import sha256

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Tenant


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_api_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def get_current_tenant(
    api_key: str | None = Depends(api_key_header),
    db: Session = Depends(get_db),
) -> Tenant:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    tenant = db.scalar(
        select(Tenant).where(Tenant.api_key_hash == hash_api_key(api_key))
    )
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return tenant
