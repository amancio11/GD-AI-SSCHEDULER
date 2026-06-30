"""006_add_last_run_summary_to_scenario

Aggiunge last_run_summary (JSON) a schedule_scenarios: struttura dati
completa dell'ultimo run del solver CP-SAT — operazioni schedulate,
vincoli applicati, tempo di risoluzione, ecc.

Revision ID: 006
Revises: 005
Create Date: 2026-06-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedule_scenarios",
        sa.Column("last_run_summary", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("schedule_scenarios", "last_run_summary")
