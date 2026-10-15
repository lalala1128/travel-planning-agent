"""add tenant ownership to trips

Revision ID: 8c17b2f6a901
Revises: 2ad9cad121d0
"""

import os
from hashlib import sha256

from alembic import op
import sqlalchemy as sa


revision = "8c17b2f6a901"
down_revision = "2ad9cad121d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    existing_trips = connection.scalar(sa.text("SELECT COUNT(*) FROM trips"))
    legacy_key = os.getenv("LEGACY_TENANT_API_KEY")

    if existing_trips and (not legacy_key or len(legacy_key) < 32):
        raise RuntimeError(
            "Existing trips require LEGACY_TENANT_API_KEY "
            "(at least 32 characters) before migration"
        )

    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("api_key_hash"),
    )
    op.add_column("trips", sa.Column("tenant_id", sa.Integer(), nullable=True))

    if existing_trips:
        result = connection.execute(
            sa.text(
                "INSERT INTO tenants (name, api_key_hash) "
                "VALUES (:name, :api_key_hash)"
            ),
            {
                "name": "legacy",
                "api_key_hash": sha256(legacy_key.encode("utf-8")).hexdigest(),
            },
        )
        connection.execute(
            sa.text("UPDATE trips SET tenant_id = :tenant_id"),
            {"tenant_id": result.lastrowid},
        )

    with op.batch_alter_table("trips") as batch_op:
        batch_op.alter_column(
            "tenant_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.create_index("ix_trips_tenant_id", ["tenant_id"])
        batch_op.create_foreign_key(
            "fk_trips_tenant_id", "tenants", ["tenant_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("trips") as batch_op:
        batch_op.drop_constraint("fk_trips_tenant_id", type_="foreignkey")
        batch_op.drop_index("ix_trips_tenant_id")
        batch_op.drop_column("tenant_id")
    op.drop_table("tenants")
