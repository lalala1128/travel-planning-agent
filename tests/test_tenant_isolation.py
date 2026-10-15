from app.auth import hash_api_key
from app.models import Tenant
from app.schemas import SearchQueryAnalysis
from sqlalchemy.orm import sessionmaker


def add_second_tenant(db_session):
    tenant = Tenant(
        name="other-tenant", api_key_hash=hash_api_key("other-key")
    )
    db_session.add(tenant)
    db_session.commit()
    return tenant


def test_api_requires_a_valid_key(client):
    assert client.get("/trips", headers={"X-API-Key": ""}).status_code == 401
    assert (
        client.get("/knowledge/search", params={"query": "成都"},
                   headers={"X-API-Key": "invalid"}).status_code == 401
    )


def test_trip_resources_are_invisible_to_another_tenant(client, db_session):
    add_second_tenant(db_session)
    created = client.post(
        "/trips",
        json={
            "destination": "成都",
            "start_date": "2026-10-20",
            "days": 2,
            "budget": 1000,
            "interests": [],
        },
    )
    assert created.status_code == 201
    trip_id = created.json()["id"]
    other = {"X-API-Key": "other-key"}

    assert client.get("/trips", headers=other).json() == []
    requests = [
        ("get", f"/trips/{trip_id}"),
        ("get", f"/trips/{trip_id}/plans"),
        ("get", f"/trips/{trip_id}/executions"),
        ("get", f"/trips/{trip_id}/task"),
        ("post", f"/trips/{trip_id}/generate-plan"),
        ("post", f"/trips/{trip_id}/generate-plan/stream"),
        ("post", f"/trips/{trip_id}/plans/1/restore"),
        ("delete", f"/trips/{trip_id}"),
    ]
    for method, path in requests:
        response = getattr(client, method)(path, headers=other)
        assert response.status_code == 404, path

    assert client.put(
        f"/trips/{trip_id}", json=created.json(), headers=other
    ).status_code == 404
    assert client.patch(
        f"/trips/{trip_id}/status",
        json={"status": "failed"},
        headers=other,
    ).status_code == 404

    assert client.get(f"/trips/{trip_id}").status_code == 200


def test_knowledge_routes_pass_tenant_to_rag(client, db_session, monkeypatch):
    import app.routers.knowledge as knowledge_router

    add_second_tenant(db_session)
    calls = []

    class FakeRagService:
        def ingest(self, **kwargs):
            calls.append(("ingest", kwargs["tenant_id"], kwargs["filename"]))
            return 1

        def search(self, **kwargs):
            calls.append(("search", kwargs["tenant_id"], kwargs["query"]))
            return []

    monkeypatch.setattr(
        knowledge_router, "get_rag_service", lambda: FakeRagService()
    )
    monkeypatch.setattr(
        knowledge_router,
        "rewrite_search_query_safely",
        lambda query, requested_city: SearchQueryAnalysis(
            original_query=query,
            rewritten_query=query,
        ),
    )
    original_tenant_id = db_session.query(Tenant).filter_by(
        name="test-tenant"
    ).one().id
    other_tenant_id = db_session.query(Tenant).filter_by(
        name="other-tenant"
    ).one().id

    for headers in ({"X-API-Key": "test-key"}, {"X-API-Key": "other-key"}):
        response = client.post(
            "/knowledge/documents",
            files={"file": ("same.md", b"sample text")},
            headers=headers,
        )
        assert response.status_code == 201
        response = client.get(
            "/knowledge/search",
            params={"query": "sample"},
            headers=headers,
        )
        assert response.status_code == 200

    assert calls == [
        ("ingest", original_tenant_id, "same.md"),
        ("search", original_tenant_id, "sample"),
        ("ingest", other_tenant_id, "same.md"),
        ("search", other_tenant_id, "sample"),
    ]


def test_tenant_key_rotation_invalidates_old_key(
    client, db_session, monkeypatch, capsys
):
    import sys
    from scripts import manage_tenants

    tenant = add_second_tenant(db_session)
    session_factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(manage_tenants, "SessionLocal", session_factory)
    monkeypatch.setattr(
        sys, "argv", ["manage_tenants", "rotate", str(tenant.id)]
    )

    manage_tenants.main()
    new_key = capsys.readouterr().out.split("api_key=", 1)[1].strip()

    assert client.get("/trips", headers={"X-API-Key": "other-key"}).status_code == 401
    assert client.get("/trips", headers={"X-API-Key": new_key}).status_code == 200


def test_cli_creates_tenant_with_usable_key(
    client, db_session, monkeypatch, capsys
):
    import sys
    from scripts import manage_tenants

    session_factory = sessionmaker(bind=db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(manage_tenants, "SessionLocal", session_factory)
    monkeypatch.setattr(sys, "argv", ["manage_tenants", "create", "new-customer"])

    manage_tenants.main()
    api_key = capsys.readouterr().out.split("api_key=", 1)[1].strip()
    tenant = db_session.query(Tenant).filter_by(name="new-customer").one()

    assert tenant.api_key_hash == hash_api_key(api_key)
    assert tenant.api_key_hash != api_key
    assert client.get("/trips", headers={"X-API-Key": api_key}).json() == []
