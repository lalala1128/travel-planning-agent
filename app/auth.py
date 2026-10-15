from datetime import datetime, timezone
from hashlib import scrypt, sha256
from hmac import compare_digest
from secrets import token_bytes

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Tenant, User, UserSession


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SUPPORTED_SCRYPT_PARAMS = {(SCRYPT_N, SCRYPT_R, SCRYPT_P)}


def utc_now() -> datetime:
    # MySQL DATETIME stores UTC values without timezone information.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_api_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = token_bytes(16)
    digest = scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored_hash.split("$")
        params = (int(n), int(r), int(p))
        if algorithm != "scrypt" or params not in SUPPORTED_SCRYPT_PARAMS:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = scrypt(
            password.encode("utf-8"), salt=salt,
            n=params[0], r=params[1], p=params[2], dklen=32,
        )
        return compare_digest(actual, expected)
    except ValueError:
        return False


def require_session(db: Session, credentials: HTTPAuthorizationCredentials | None) -> UserSession:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required")
    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_api_key(credentials.credentials),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utc_now(),
        )
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return session


def get_current_user_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> UserSession:
    return require_session(db, credentials)


def get_current_user(
    session: UserSession = Depends(get_current_user_session),
) -> User:
    return session.user


def get_current_tenant(
    api_key: str | None = Depends(api_key_header),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Tenant:
    if authorization is not None:
        session = require_session(db, credentials)
        return session.user.tenant

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
