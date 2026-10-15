import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

import app.services.auth_rate_limit as rate_limit
from app.services.auth_rate_limit import AuthRateLimiter


def test_memory_limiter_resets_after_window(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(rate_limit, "monotonic", lambda: now[0])
    limiter = AuthRateLimiter()

    limiter.enforce("login:account", "alice@example.com", 2, 60)
    limiter.enforce("login:account", "alice@example.com", 2, 60)
    with pytest.raises(HTTPException) as error:
        limiter.enforce("login:account", "alice@example.com", 2, 60)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "60"
    assert "alice@example.com" not in next(iter(limiter._buckets))

    now[0] += 60
    limiter.enforce("login:account", "alice@example.com", 2, 60)


def test_redis_limiter_uses_hashed_identity_and_retry_after():
    class FakeRedis:
        def __init__(self):
            self.calls = []

        def eval(self, *args):
            self.calls.append(args)
            return 17

    redis = FakeRedis()
    limiter = AuthRateLimiter(redis)
    with pytest.raises(HTTPException) as error:
        limiter.enforce("login:account", "alice@example.com", 10, 900)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "17"
    script, key_count, key, period, limit = redis.calls[0]
    assert script == rate_limit.LIMIT_SCRIPT
    assert key_count == 1
    assert "alice@example.com" not in key
    assert (period, limit) == (900, 10)


def test_redis_failure_fails_closed():
    class FailedRedis:
        def eval(self, *args):
            raise RedisError("unavailable")

    with pytest.raises(HTTPException) as error:
        AuthRateLimiter(FailedRedis()).enforce("login:ip", "127.0.0.1", 50, 900)
    assert error.value.status_code == 503


def test_register_limit_applies_before_database_write(client, db_session):
    from app.models import User

    for index in range(5):
        response = client.post("/auth/register", json={
            "email": f"user{index}@example.com",
            "password": "correct-horse-battery-staple",
        })
        assert response.status_code == 201

    response = client.post("/auth/register", json={
        "email": "user5@example.com",
        "password": "correct-horse-battery-staple",
    })
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 3600
    assert db_session.query(User).count() == 5


def test_login_limit_is_shared_across_case_variants(client):
    for index in range(10):
        email = "ALICE@example.com" if index % 2 else "alice@example.com"
        assert client.post("/auth/login", json={
            "email": email,
            "password": "incorrect-password",
        }).status_code == 401

    response = client.post("/auth/login", json={
        "email": "alice@example.com",
        "password": "incorrect-password",
    })
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 900
