"""007_add_resource_types

Pivot a scheduling per capacità di gruppo:
  * crea `resource_types` (workcenter, skill, ore/giorno, count) — risorse senza nome;
  * `schedule_entries.operator_id` → nullable (le entries ora referenziano un gruppo);
  * aggiunge `schedule_entries.resource_type_id` (FK nullable).

Revision ID: 007
Revises: 006
Create Date: 2026-06-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

# skilltype enum esiste già (usato da operators) → non ricrearlo.
_skilltype = postgresql.ENUM("ELECTRICAL", "MECHANICAL", "MULTI", name="skilltype", create_type=False)


def upgrade() -> None:
    op.create_table(
        "resource_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("workcenter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill", _skilltype, nullable=False),
        sa.Column("daily_capacity_hours", sa.Float(), nullable=False, server_default="8.0"),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["workcenter_id"], ["workcenters.id"]),
        sa.UniqueConstraint("workcenter_id", "skill", name="uq_resource_type_wc_skill"),
    )
    op.create_index("ix_resource_types_workcenter_id", "resource_types", ["workcenter_id"])

    op.add_column(
        "schedule_entries",
        sa.Column("resource_type_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_schedule_entries_resource_type_id",
        "schedule_entries", "resource_types",
        ["resource_type_id"], ["id"],
    )
    op.alter_column("schedule_entries", "operator_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("schedule_entries", "operator_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_constraint("fk_schedule_entries_resource_type_id", "schedule_entries", type_="foreignkey")
    op.drop_column("schedule_entries", "resource_type_id")
    op.drop_index("ix_resource_types_workcenter_id", table_name="resource_types")
    op.drop_table("resource_types")
