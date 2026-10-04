"""initial schema

Revision ID: 2ad9cad121d0
Revises:
Create Date: 2026-10-02 17:09:57.714717

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ad9cad121d0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the initial application schema."""
    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("destination", sa.String(length=50), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("budget", sa.Float(), nullable=False),
        sa.Column("interests", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trips_destination",
        "trips",
        ["destination"],
        unique=False,
    )
    op.create_index(
        "ix_trips_status",
        "trips",
        ["status"],
        unique=False,
    )

    op.create_table(
        "trip_plan_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trip_id",
            "version",
            name="uq_trip_plan_history_version",
        ),
    )
    op.create_index(
        "ix_trip_plan_history_trip_id",
        "trip_plan_history",
        ["trip_id"],
        unique=False,
    )

    op.create_table(
        "trip_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_node", sa.String(length=50), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trip_executions_trip_id",
        "trip_executions",
        ["trip_id"],
        unique=False,
    )
    op.create_index(
        "ix_trip_executions_status",
        "trip_executions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the initial application schema."""
    op.drop_index(
        "ix_trip_executions_status",
        table_name="trip_executions",
    )
    op.drop_index(
        "ix_trip_executions_trip_id",
        table_name="trip_executions",
    )
    op.drop_table("trip_executions")

    op.drop_index(
        "ix_trip_plan_history_trip_id",
        table_name="trip_plan_history",
    )
    op.drop_table("trip_plan_history")

    op.drop_index(
        "ix_trips_status",
        table_name="trips",
    )
    op.drop_index(
        "ix_trips_destination",
        table_name="trips",
    )
    op.drop_table("trips")
