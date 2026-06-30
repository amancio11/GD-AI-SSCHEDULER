"""005_add_start_date_to_scenario

Aggiunge start_date a schedule_scenarios: data di partenza dello scheduling.
Se NULL, il reschedule_engine usa date.today() come fallback.

Revision ID: 005
Revises: 004
Create Date: 2026-06-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedule_scenarios",
        sa.Column("start_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("schedule_scenarios", "start_date")
