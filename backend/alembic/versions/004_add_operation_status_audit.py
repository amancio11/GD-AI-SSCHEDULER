"""004_add_operation_status_audit

Crea la tabella operation_status_audit (audit trail del nuovo state engine
per Operation/ProductionOrder — vedi app/core/state_engine/).

Revision ID: 004
Revises: 003
Create Date: 2026-06-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_status_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("old_status", sa.String(32), nullable=False),
        sa.Column("new_status", sa.String(32), nullable=False),
        sa.Column("is_unusual", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("delay_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reschedule_urgency", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("audit_message", sa.Text, nullable=False),
        sa.Column("warnings_json", sa.Text, nullable=True),
        sa.Column("triggered_by", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_operation_status_audit_entity_id", "operation_status_audit", ["entity_id"]
    )
    op.create_index(
        "ix_operation_status_audit_entity_type", "operation_status_audit", ["entity_type"]
    )
    op.create_index(
        "ix_operation_status_audit_created_at", "operation_status_audit", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_operation_status_audit_created_at", table_name="operation_status_audit")
    op.drop_index("ix_operation_status_audit_entity_type", table_name="operation_status_audit")
    op.drop_index("ix_operation_status_audit_entity_id", table_name="operation_status_audit")
    op.drop_table("operation_status_audit")