from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_api_key
from app.database import Base, get_db, get_session_factory
from app.main import create_app
from app.models import Tenant
from app.services.auth_rate_limit import AuthRateLimiter


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    with testing_session() as session:
        session.add(Tenant(name="test-tenant", api_key_hash=hash_api_key("test-key")))
        session.commit()
        yield session

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app = create_app()
    app.state.auth_rate_limiter = AuthRateLimiter()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory] = (
        lambda: lambda: db_session
    )

    with TestClient(app) as test_client:
        test_client.headers.update({"X-API-Key": "test-key"})
        yield test_client
