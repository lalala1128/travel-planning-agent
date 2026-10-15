import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.database import engine
from app.models import Trip, TripPlanHistory
from app.routers.trips import save_plan_history


@pytest.mark.skipif(
    os.getenv("RUN_MYSQL_MIGRATION_TESTS") != "1",
    reason="requires a disposable MySQL database",
)
def test_existing_trip_migrates_to_legacy_tenant(monkeypatch):
    if engine.dialect.name != "mysql":
        pytest.fail("migration integration test requires MySQL")

    with engine.connect() as connection:
        if sa.inspect(connection).get_table_names():
            pytest.fail("migration integration database must be empty")

    config = Config("alembic.ini")
    command.upgrade(config, "2ad9cad121d0")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO trips "
            "(destination, start_date, days, budget, interests, status) "
            "VALUES ('test', '2026-10-20', 2, 1000, '[]', 'draft')"
        ))

    monkeypatch.setenv(
        "LEGACY_TENANT_API_KEY", "integration-test-secret-longer-than-32-chars"
    )
    command.upgrade(config, "head")

    with engine.connect() as connection:
        assert sa.inspect(connection).has_table("users")
        assert sa.inspect(connection).has_table("user_sessions")
        assert connection.scalar(sa.text(
            "SELECT COUNT(*) FROM trips AS t "
            "JOIN tenants AS o ON o.id = t.tenant_id "
            "WHERE o.name = 'legacy'"
        )) == 1

    with Session(engine) as session:
        tenant_id = session.scalar(sa.text(
            "SELECT id FROM tenants WHERE name = 'legacy'"
        ))
        trip = Trip(
            tenant_id=tenant_id,
            destination="version-test",
            start_date=date(2026, 10, 21),
            days=1,
            budget=1000,
            interests=[],
            status="draft",
        )
        session.add(trip)
        session.commit()
        trip_id = trip.id

    barrier = Barrier(2)

    def write_version(label):
        with Session(engine, autoflush=False) as session:
            trip = session.get(Trip, trip_id)
            barrier.wait(timeout=10)
            history = save_plan_history(session, trip, {"summary": label})
            session.commit()
            return history.version

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_version, label) for label in ("a", "b")]
        assert sorted(future.result(timeout=20) for future in futures) == [1, 2]

    with Session(engine) as session:
        versions = session.scalars(
            sa.select(TripPlanHistory.version)
            .where(TripPlanHistory.trip_id == trip_id)
            .order_by(TripPlanHistory.version)
        ).all()
        assert versions == [1, 2]
