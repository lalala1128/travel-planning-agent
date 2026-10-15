import importlib
from hashlib import sha256

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


migration = importlib.import_module(
    "migrations.versions.8c17b2f6a901_tenant_isolation"
)
initial_migration = importlib.import_module(
    "migrations.versions.2ad9cad121d0_initial_schema"
)


def old_trip_database():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            initial_migration.upgrade()
        connection.execute(sa.text(
            "INSERT INTO trips "
            "(id, destination, start_date, days, budget, interests, status) "
            "VALUES (1, 'test', '2026-10-20', 2, 1000, '[]', 'draft')"
        ))
    return engine


def run_upgrade(connection):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.upgrade()


def test_migration_rejects_existing_data_without_key(monkeypatch):
    monkeypatch.delenv("LEGACY_TENANT_API_KEY", raising=False)
    engine = old_trip_database()
    try:
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="LEGACY_TENANT_API_KEY"):
                run_upgrade(connection)
            assert not sa.inspect(connection).has_table("tenants")
    finally:
        engine.dispose()


def test_migration_assigns_existing_trips_to_legacy_tenant(monkeypatch):
    secret = "test-legacy-key-with-at-least-32-characters"
    monkeypatch.setenv("LEGACY_TENANT_API_KEY", secret)
    engine = old_trip_database()
    try:
        with engine.begin() as connection:
            run_upgrade(connection)
            tenant = connection.execute(
                sa.text("SELECT id, api_key_hash FROM tenants WHERE name='legacy'")
            ).one()
            assert connection.scalar(
                sa.text("SELECT tenant_id FROM trips WHERE id=1")
            ) == tenant.id
            assert tenant.api_key_hash == sha256(
                secret.encode("utf-8")
            ).hexdigest()
    finally:
        engine.dispose()


def test_migration_supports_empty_database(monkeypatch):
    monkeypatch.delenv("LEGACY_TENANT_API_KEY", raising=False)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                initial_migration.upgrade()
            run_upgrade(connection)
            assert connection.scalar(
                sa.text("SELECT COUNT(*) FROM tenants")
            ) == 0
            columns = sa.inspect(connection).get_columns("trips")
            assert next(
                column for column in columns if column["name"] == "tenant_id"
            )["nullable"] is False
    finally:
        engine.dispose()
