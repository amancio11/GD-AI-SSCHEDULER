"""Add CASCADE delete + scenario run result columns

Revision ID: 003
Revises: 002
"""
from alembic import op

revision = '003_cascade_and_run_status'
down_revision = '002_add_group_to_targetlevel'


def upgrade() -> None:
    # ── 1. CASCADE DELETE su schedule_entries.scenario_id ──
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

    # CASCADE anche su ai_suggestions.scenario_id (se presente)
    try:
        op.drop_constraint(
            'ai_suggestions_scenario_id_fkey',
            'ai_suggestions',
            type_='foreignkey',
        )
        op.create_foreign_key(
            'ai_suggestions_scenario_id_fkey',
            'ai_suggestions',
            'schedule_scenarios',
            ['scenario_id'],
            ['id'],
            ondelete='CASCADE',
        )
    except Exception:
        pass  # FK potrebbe non esistere o avere nome diverso

    # ── 2. Colonne risultato ultimo run dello scenario ──
    op.add_column('schedule_scenarios',
        op.column('last_run_status', op.sa.String(), nullable=True)
    )
    op.add_column('schedule_scenarios',
        op.column('last_run_at', op.sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('schedule_scenarios',
        op.column('last_run_makespan_days', op.sa.Float(), nullable=True)
    )
    op.add_column('schedule_scenarios',
        op.column('last_run_operators_used', op.sa.Integer(), nullable=True)
    )
    op.add_column('schedule_scenarios',
        op.column('last_run_conflicts', op.sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('schedule_scenarios', 'last_run_conflicts')
    op.drop_column('schedule_scenarios', 'last_run_operators_used')
    op.drop_column('schedule_scenarios', 'last_run_makespan_days')
    op.drop_column('schedule_scenarios', 'last_run_at')
    op.drop_column('schedule_scenarios', 'last_run_status')

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