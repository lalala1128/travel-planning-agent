from datetime import datetime, timedelta

from app.auth import hash_api_key, hash_password, utc_now, verify_password
from app.models import Tenant, User, UserSession
from app.schemas import SearchQueryAnalysis


PASSWORD = "correct-horse-battery-staple"


def test_password_hashes_are_salted_and_reject_malformed_values():
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong", first)
    assert not verify_password(PASSWORD, "scrypt$999999999$8$1$00$00")
    assert not verify_password(PASSWORD, "invalid")


def create_account(client, email):
    response = client.post("/auth/register", json={
        "email": email,
        "password": PASSWORD,
    })
    assert response.status_code == 201
    response = client.post("/auth/login", json={
        "email": email,
        "password": PASSWORD,
    })
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert datetime.fromisoformat(response.json()["expires_at"]).tzinfo is not None
    return response.json()["access_token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_logout_and_password_storage(client, db_session):
    registered = client.post("/auth/register", json={
        "email": "  Alice@Example.com ",
        "password": PASSWORD,
    })
    assert registered.status_code == 201
    assert registered.json()["email"] == "alice@example.com"
    assert "password" not in registered.json()

    user = db_session.query(User).filter_by(email="alice@example.com").one()
    assert user.password_hash.startswith("scrypt$")
    assert PASSWORD not in user.password_hash
    assert user.tenant_id != db_session.query(Tenant).filter_by(
        name="test-tenant"
    ).one().id
    assert client.post("/auth/register", json={
        "email": "ALICE@example.com", "password": PASSWORD,
    }).status_code == 409
    assert db_session.query(Tenant).count() == 2
    assert client.post("/auth/login", json={
        "email": "alice@example.com", "password": "wrong-password",
    }).status_code == 401
    assert client.post("/auth/login", json={
        "email": "missing@example.com", "password": PASSWORD,
    }).status_code == 401

    token = client.post("/auth/login", json={
        "email": "alice@example.com", "password": PASSWORD,
    }).json()["access_token"]
    assert token.startswith("tps_")
    stored_session = db_session.query(UserSession).one()
    assert stored_session.token_hash == hash_api_key(token)
    assert token != stored_session.token_hash
    assert client.get("/auth/me", headers=bearer(token)).json()["id"] == user.id
    assert client.get("/auth/me").status_code == 401
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 204
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401
    assert client.get("/trips", headers=bearer(token)).status_code == 401


def test_expired_or_invalid_bearer_never_falls_back_to_api_key(client, db_session):
    token = create_account(client, "alice@example.com")
    assert client.get("/trips", headers=bearer("invalid")).status_code == 401
    assert client.get("/trips", headers={
        "Authorization": "Basic invalid",
    }).status_code == 401

    session = db_session.query(UserSession).one()
    session.expires_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    assert client.get("/trips", headers=bearer(token)).status_code == 401


def test_registration_rejects_short_password_and_bad_email(client):
    for payload in (
        {"email": "alice@example.com", "password": "short"},
        {"email": "not-an-email", "password": PASSWORD},
    ):
        assert client.post("/auth/register", json=payload).status_code == 422


def test_personal_accounts_isolate_trip_routes(client, db_session):
    alice = bearer(create_account(client, "alice@example.com"))
    bob = bearer(create_account(client, "bob@example.com"))
    created = client.post("/trips", headers=alice, json={
        "destination": "成都",
        "start_date": "2026-10-20",
        "days": 2,
        "budget": 1000,
        "interests": [],
    })
    assert created.status_code == 201
    trip_id = created.json()["id"]
    assert client.get("/trips", headers=bob).json() == []
    assert client.get("/trips").json() == []
    assert client.get(f"/trips/{trip_id}", headers=alice).status_code == 200
    for path in (
        f"/trips/{trip_id}",
        f"/trips/{trip_id}/plans",
        f"/trips/{trip_id}/executions",
        f"/trips/{trip_id}/task",
    ):
        assert client.get(path, headers=bob).status_code == 404
    assert client.delete(f"/trips/{trip_id}", headers=bob).status_code == 404
    assert client.post(
        f"/trips/{trip_id}/generate-plan", headers=bob
    ).status_code == 404
    assert client.post(
        f"/trips/{trip_id}/generate-plan/stream", headers=bob
    ).status_code == 404
    assert client.post(
        f"/trips/{trip_id}/plans/1/restore", headers=bob
    ).status_code == 404
    assert client.get(f"/trips/{trip_id}", headers=alice).status_code == 200
    assert db_session.query(User).count() == 2
    assert db_session.query(User.tenant_id).distinct().count() == 2


def test_personal_accounts_isolate_knowledge(client, db_session, monkeypatch):
    import app.routers.knowledge as knowledge_router

    alice = bearer(create_account(client, "alice@example.com"))
    bob = bearer(create_account(client, "bob@example.com"))
    seen = []

    class FakeRagService:
        def ingest(self, **kwargs):
            seen.append(("ingest", kwargs["tenant_id"]))
            return 1

        def search(self, **kwargs):
            seen.append(("search", kwargs["tenant_id"]))
            return []

    monkeypatch.setattr(knowledge_router, "get_rag_service", lambda: FakeRagService())
    monkeypatch.setattr(
        knowledge_router, "rewrite_search_query_safely",
        lambda query, requested_city: SearchQueryAnalysis(
            original_query=query, rewritten_query=query,
        ),
    )
    for headers in (alice, bob):
        assert client.post(
            "/knowledge/documents",
            files={"file": ("same.md", b"sample text")},
            headers=headers,
        ).status_code == 201
        assert client.get(
            "/knowledge/search", params={"query": "sample"}, headers=headers,
        ).status_code == 200

    assert seen[0][1] == seen[1][1]
    assert seen[2][1] == seen[3][1]
    assert seen[0][1] != seen[2][1]
