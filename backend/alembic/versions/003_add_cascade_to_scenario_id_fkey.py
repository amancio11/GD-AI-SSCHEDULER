"""Add CASCADE delete to schedule_entries + last_run_* columns to schedule_scenarios

Revision ID: 003
Revises: 002
Create Date: 2026-06-15

COSA FA QUESTA MIGRATION:
1. Ricrea la FK schedule_entries.scenario_id con ON DELETE CASCADE
   (prima era senza cascade → DELETE scenario con entries → FK violation)
2. Aggiunge colonne last_run_* a schedule_scenarios per esporre
   il risultato del solver al frontend senza dipendere dal polling Celery
3. Tenta lo stesso CASCADE su ai_suggestions (potrebbe non esistere la FK)
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. CASCADE DELETE su schedule_entries.scenario_id ────────────────────
    # Nota: il nome esatto del constraint dipende da come è stato creato
    # nella migration 001. Se il nome è diverso, questa migration fallirà
    # con "constraint not found" — in quel caso usare il nome dalla query:
    #   SELECT constraint_name FROM information_schema.table_constraints
    #   WHERE table_name = 'schedule_entries' AND constraint_type = 'FOREIGN KEY';
    op.drop_constraint(
        'schedule_entries_scenario_id_fkey',
        'schedule_entries',
        type_='foreignkey',
    )
    op.create_foreign_key(
        'schedule_entries_scenario_id_fkey',
        'schedule_entries',
        'schedule_scenarios',
        ['scenario_id'],
        ['id'],
        ondelete='CASCADE',
    )

    # ── 2. CASCADE su ai_suggestions.scenario_id (best-effort) ───────────────
    # Wrapped in try/except perché la FK potrebbe avere nome diverso o non esistere
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE table_name = 'ai_suggestions'
          AND constraint_type = 'FOREIGN KEY'
          AND constraint_name LIKE '%scenario_id%'
    """))
    row = result.fetchone()
    if row:
        ai_fk_name = row[0]
        op.drop_constraint(ai_fk_name, 'ai_suggestions', type_='foreignkey')
        op.create_foreign_key(
            ai_fk_name,
            'ai_suggestions',
            'schedule_scenarios',
            ['scenario_id'],
            ['id'],
            ondelete='SET NULL',  # ai_suggestions.scenario_id è nullable
        )

    # ── 3. Colonne last_run_* su schedule_scenarios ───────────────────────────
    op.add_column('schedule_scenarios',
        sa.Column('last_run_status', sa.String(32), nullable=True)
    )
    op.add_column('schedule_scenarios',
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('schedule_scenarios',
        sa.Column('last_run_makespan_days', sa.Float(), nullable=True)
    )
    op.add_column('schedule_scenarios',
        sa.Column('last_run_operators_used', sa.Integer(), nullable=True)
    )
    op.add_column('schedule_scenarios',
        sa.Column('last_run_conflicts', postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('schedule_scenarios', 'last_run_conflicts')
    op.drop_column('schedule_scenarios', 'last_run_operators_used')
    op.drop_column('schedule_scenarios', 'last_run_makespan_days')
    op.drop_column('schedule_scenarios', 'last_run_at')
    op.drop_column('schedule_scenarios', 'last_run_status')

    # Ripristina FK senza CASCADE
    op.drop_constraint(
        'schedule_entries_scenario_id_fkey',
        'schedule_entries',
        type_='foreignkey',
    )
    op.create_foreign_key(
        'schedule_entries_scenario_id_fkey',
        'schedule_entries',
        'schedule_scenarios',
        ['scenario_id'],
        ['id'],
    )