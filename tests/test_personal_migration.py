import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


initial = importlib.import_module(
    "migrations.versions.2ad9cad121d0_initial_schema"
)
tenants = importlib.import_module(
    "migrations.versions.8c17b2f6a901_tenant_isolation"
)
accounts = importlib.import_module(
    "migrations.versions.f6a0c2d4e801_personal_accounts"
)


def test_personal_account_migration_preserves_existing_tenants(monkeypatch):
    monkeypatch.delenv("LEGACY_TENANT_API_KEY", raising=False)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                initial.upgrade()
                tenants.upgrade()
            connection.execute(sa.text(
                "INSERT INTO tenants (name, api_key_hash) VALUES ('demo', 'hash')"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                accounts.upgrade()

            inspector = sa.inspect(connection)
            assert inspector.has_table("users")
            assert inspector.has_table("user_sessions")
            assert connection.scalar(sa.text(
                "SELECT COUNT(*) FROM tenants WHERE name = 'demo'"
            )) == 1
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM users")) == 0
            user_uniques = inspector.get_unique_constraints("users")
            assert any(item["column_names"] == ["tenant_id"] for item in user_uniques)

            with Operations.context(MigrationContext.configure(connection)):
                accounts.downgrade()
            assert not sa.inspect(connection).has_table("users")
            assert sa.inspect(connection).has_table("tenants")
    finally:
        engine.dispose()
