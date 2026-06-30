"""008_add_weekday_schedule

Aggiunge resource_types.weekday_schedule (JSON): disponibilità per giorno della
settimana (count + ore per giorno). Se null → default lun–ven = (count, ore), weekend = 0.

Revision ID: 008
Revises: 007
Create Date: 2026-06-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resource_types",
        sa.Column("weekday_schedule", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resource_types", "weekday_schedule")
