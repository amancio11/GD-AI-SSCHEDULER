"""initial_schema — all 19 tables.

Revision ID: 001
Revises:
Create Date: 2026-06-12 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Enum types ────────────────────────────────────────────────────────────
    # Usiamo DO-block PostgreSQL nativo invece di checkfirst=True di SQLAlchemy,
    # che non funziona correttamente con asyncpg. Il DO-block cattura
    # l'eccezione duplicate_object se il tipo esiste già, rendendo la
    # migrazione idempotente anche su re-esecuzioni parziali.
    enums = [
        ("machineorderstatus",    "'PLANNED','IN_PROGRESS','COMPLETED','BLOCKED'"),
        ("productionorderlevel",  "'MACHINE','MACROAGGREGATE','AGGREGATE','GROUP','COMPONENT'"),
        ("productionorderstatus", "'PLANNED','IN_PROGRESS','COMPLETED','BLOCKED','MISSING'"),
        ("executionmode",         "'SIMULTANEOUS'"),
        ("operationtype",         "'ELECTRICAL','MECHANICAL','GENERAL'"),
        ("operationstatus",       "'PENDING','IN_PROGRESS','COMPLETED','BLOCKED','INTERRUPTED'"),
        ("targetlevel",           "'MACROAGGREGATE','AGGREGATE','GROUP'"),
        ("skilltype",             "'ELECTRICAL','MECHANICAL','MULTI'"),
        ("scheduleentrystatus",   "'SCHEDULED','IN_PROGRESS','COMPLETED','INTERRUPTED','DELAYED','STALE'"),
        ("objectivemode",         "'FINISH_BY_DATE','MAXIMIZE_RESOURCE_UTILIZATION','MINIMIZE_OPERATORS','CUSTOM'"),
        ("delayeventtype",        "'OPERATOR_ABSENCE','COMPONENT_DELAY','MANUAL_OPERATION_DELAY','OTHER'"),
        ("aisuggestiontype",      "'ON_DEMAND','PROACTIVE','DELAY_ANALYSIS','HISTORICAL_PATTERN','WHAT_IF','EXPLAIN_ENTRY'"),
    ]
    for name, values in enums:
        op.execute(
            f"DO $$ BEGIN CREATE TYPE {name} AS ENUM ({values}); "
            f"EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )

    # ── 1. workcenters ────────────────────────────────────────────────────────
    op.create_table(
        "workcenters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_workcenters_code", "workcenters", ["code"])

    # ── 2. machine_models ─────────────────────────────────────────────────────
    op.create_table(
        "machine_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
    )
    op.create_index("ix_machine_models_code", "machine_models", ["code"])

    # ── 3. shifts ─────────────────────────────────────────────────────────────
    op.create_table(
        "shifts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("break_duration_minutes", sa.Integer, nullable=False, server_default="30"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )

    # ── 4. skill_workcenter_mapping ───────────────────────────────────────────
    op.create_table(
        "skill_workcenter_mapping",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("skill", postgresql.ENUM("ELECTRICAL", "MECHANICAL", "MULTI", name="skilltype", create_type=False), nullable=False),
        sa.Column("workcenter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workcenters.id"), nullable=False),
        sa.Column("can_do_electrical", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("can_do_mechanical", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("can_do_general", sa.Boolean, nullable=False, server_default="false"),
        sa.UniqueConstraint("skill", "workcenter_id", name="uq_skill_workcenter"),
    )

    # ── 5. operators ──────────────────────────────────────────────────────────
    op.create_table(
        "operators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", sa.String(64), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("skill", postgresql.ENUM("ELECTRICAL", "MECHANICAL", "MULTI", name="skilltype", create_type=False), nullable=False),
        sa.Column("workcenter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workcenters.id"), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_operators_employee_id", "operators", ["employee_id"])
    op.create_index("ix_operators_workcenter_id", "operators", ["workcenter_id"])

    # ── 6. machine_orders ─────────────────────────────────────────────────────
    op.create_table(
        "machine_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sap_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("machine_model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_models.id"), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", postgresql.ENUM("PLANNED", "IN_PROGRESS", "COMPLETED", "BLOCKED", name="machineorderstatus", create_type=False), nullable=False, server_default="PLANNED"),
        sa.Column("workcenter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workcenters.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_machine_orders_sap_order_id", "machine_orders", ["sap_order_id"])

    # ── 7. production_orders ──────────────────────────────────────────────────
    op.create_table(
        "production_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sap_order_id", sa.String(64), nullable=False, unique=True),
        sa.Column("parent_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_orders.id"), nullable=True),
        sa.Column("parent_material", sa.String(128), nullable=True),
        sa.Column("machine_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_orders.id"), nullable=False),
        sa.Column("level", postgresql.ENUM("MACHINE", "MACROAGGREGATE", "AGGREGATE", "GROUP", "COMPONENT", name="productionorderlevel", create_type=False), nullable=False),
        sa.Column("material_code", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("unit", sa.String(16), nullable=False, server_default="PZ"),
        sa.Column("workcenter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workcenters.id"), nullable=True),
        sa.Column("progress_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", postgresql.ENUM("PLANNED", "IN_PROGRESS", "COMPLETED", "BLOCKED", "MISSING", name="productionorderstatus", create_type=False), nullable=False, server_default="PLANNED"),
        sa.Column("missing_arrival_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_purchase_component", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_production_component_untracked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_production_orders_sap_order_id", "production_orders", ["sap_order_id"])
    op.create_index("ix_production_orders_machine_order_id", "production_orders", ["machine_order_id"])
    op.create_index("ix_production_orders_material_code", "production_orders", ["material_code"])

    # ── 8. z_orders_link ──────────────────────────────────────────────────────
    op.create_table(
        "z_orders_link",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("child_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("parent_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("parent_material", sa.String(128), nullable=True),
        sa.Column("child_material", sa.String(128), nullable=True),
        sa.Column("level", sa.String(32), nullable=True),
        sa.Column("link_type", sa.String(32), nullable=True),
        sa.UniqueConstraint("child_order_id", "parent_order_id", name="uq_zol_child_parent"),
    )
    op.create_index("ix_z_orders_link_child_order_id", "z_orders_link", ["child_order_id"])
    op.create_index("ix_z_orders_link_parent_order_id", "z_orders_link", ["parent_order_id"])

    # ── 9. reference_points ───────────────────────────────────────────────────
    op.create_table(
        "reference_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("machine_model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_models.id"), nullable=False),
        sa.Column("target_level", postgresql.ENUM("MACROAGGREGATE", "AGGREGATE", name="targetlevel", create_type=False), nullable=False),
        sa.Column("target_order_material", sa.String(128), nullable=True),
        sa.UniqueConstraint("code", "machine_model_id", name="uq_rp_code_model"),
    )
    op.create_index("ix_reference_points_code", "reference_points", ["code"])
    op.create_index("ix_reference_points_machine_model_id", "reference_points", ["machine_model_id"])

    # ── 10. reference_point_precedences ───────────────────────────────────────
    op.create_table(
        "reference_point_precedences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reference_point_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reference_points.id"), nullable=False),
        sa.Column("predecessor_reference_point_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reference_points.id"), nullable=False),
        sa.Column("machine_model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_models.id"), nullable=False),
        sa.UniqueConstraint("reference_point_id", "predecessor_reference_point_id", name="uq_rpp_pair"),
    )
    op.create_index("ix_rpp_reference_point_id", "reference_point_precedences", ["reference_point_id"])
    op.create_index("ix_rpp_predecessor_id", "reference_point_precedences", ["predecessor_reference_point_id"])

    # ── 11. routings ──────────────────────────────────────────────────────────
    op.create_table(
        "routings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("production_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_orders.id"), nullable=False, unique=True),
        sa.Column("sap_routing_id", sa.String(64), nullable=True),
        sa.Column("execution_mode", postgresql.ENUM("SIMULTANEOUS", name="executionmode", create_type=False), nullable=False, server_default="SIMULTANEOUS"),
    )
    op.create_index("ix_routings_production_order_id", "routings", ["production_order_id"])

    # ── 12. operations ────────────────────────────────────────────────────────
    op.create_table(
        "operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("routing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("routings.id"), nullable=False),
        sa.Column("sap_operation_id", sa.String(64), nullable=True),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("operation_type", postgresql.ENUM("ELECTRICAL", "MECHANICAL", "GENERAL", name="operationtype", create_type=False), nullable=False),
        sa.Column("workcenter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workcenters.id"), nullable=True),
        sa.Column("planned_duration_minutes", sa.Integer, nullable=False),
        sa.Column("actual_duration_minutes", sa.Integer, nullable=True),
        sa.Column("progress_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", postgresql.ENUM("PENDING", "IN_PROGRESS", "COMPLETED", "BLOCKED", "INTERRUPTED", name="operationstatus", create_type=False), nullable=False, server_default="PENDING"),
        sa.Column("reference_point_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reference_points.id"), nullable=True),
        sa.Column("can_be_interrupted", sa.Boolean, nullable=False, server_default="true"),
        sa.UniqueConstraint("routing_id", "sequence_number", name="uq_routing_seq"),
    )
    op.create_index("ix_operations_routing_id", "operations", ["routing_id"])

    # ── 13. operator_calendar ─────────────────────────────────────────────────
    op.create_table(
        "operator_calendar",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("shift_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("shifts.id"), nullable=True),
        sa.Column("is_available", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("override_reason", sa.String(128), nullable=True),
        sa.UniqueConstraint("operator_id", "date", name="uq_operator_calendar_date"),
    )
    op.create_index("ix_operator_calendar_operator_id", "operator_calendar", ["operator_id"])

    # ── 14. missing_components ────────────────────────────────────────────────
    op.create_table(
        "missing_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("production_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("component_material", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("expected_arrival_date", sa.Date, nullable=True),
        sa.Column("is_arrived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("arrival_confirmed_date", sa.Date, nullable=True),
        sa.Column("manually_flagged", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.UniqueConstraint("production_order_id", "component_material", name="uq_missing_po_material"),
    )
    op.create_index("ix_missing_components_production_order_id", "missing_components", ["production_order_id"])

    # ── 15. schedule_scenarios ────────────────────────────────────────────────
    op.create_table(
        "schedule_scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("machine_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_orders.id"), nullable=False),
        sa.Column("objective_mode", postgresql.ENUM("FINISH_BY_DATE", "MAXIMIZE_RESOURCE_UTILIZATION", "MINIMIZE_OPERATORS", "CUSTOM", name="objectivemode", create_type=False), nullable=False, server_default="FINISH_BY_DATE"),
        sa.Column("target_finish_date", sa.Date, nullable=True),
        sa.Column("resource_set_json", postgresql.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_baseline", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("ai_explanation", sa.Text, nullable=True),
        sa.UniqueConstraint("name", "machine_order_id", name="uq_scenario_name_order"),
    )
    op.create_index("ix_schedule_scenarios_machine_order_id", "schedule_scenarios", ["machine_order_id"])

    # ── 16. schedule_entries ──────────────────────────────────────────────────
    op.create_table(
        "schedule_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schedule_scenarios.id"), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("workcenter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workcenters.id"), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", postgresql.ENUM("SCHEDULED", "IN_PROGRESS", "COMPLETED", "INTERRUPTED", "DELAYED", "STALE", name="scheduleentrystatus", create_type=False), nullable=False, server_default="SCHEDULED"),
        sa.Column("interruption_reason", sa.Text, nullable=True),
        sa.Column("delay_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_manual_override", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("ix_schedule_entries_scenario_id", "schedule_entries", ["scenario_id"])
    op.create_index("ix_schedule_entries_operation_id", "schedule_entries", ["operation_id"])

    # ── 17. delay_events ─────────────────────────────────────────────────────
    op.create_table(
        "delay_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("machine_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_orders.id"), nullable=False),
        sa.Column("event_type", postgresql.ENUM("OPERATOR_ABSENCE", "COMPONENT_DELAY", "MANUAL_OPERATION_DELAY", "OTHER", name="delayeventtype", create_type=False), nullable=False),
        sa.Column("affected_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("affected_entity_type", sa.String(64), nullable=True),
        sa.Column("delay_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delay_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requires_reschedule", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_delay_events_machine_order_id", "delay_events", ["machine_order_id"])

    # ── 18. ai_suggestions ────────────────────────────────────────────────────
    op.create_table(
        "ai_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schedule_scenarios.id"), nullable=True),
        sa.Column("machine_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_orders.id"), nullable=False),
        sa.Column("suggestion_type", postgresql.ENUM("ON_DEMAND", "PROACTIVE", "DELAY_ANALYSIS", "HISTORICAL_PATTERN", "WHAT_IF", "EXPLAIN_ENTRY", name="aisuggestiontype", create_type=False), nullable=False),
        sa.Column("suggestion_text", sa.Text, nullable=True),
        sa.Column("suggested_actions_json", postgresql.JSON, nullable=True),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("accepted", sa.Boolean, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_suggestions_scenario_id", "ai_suggestions", ["scenario_id"])
    op.create_index("ix_ai_suggestions_machine_order_id", "ai_suggestions", ["machine_order_id"])

    # ── 19. ai_chat_sessions ──────────────────────────────────────────────────
    op.create_table(
        "ai_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schedule_scenarios.id"), nullable=True),
        sa.Column("machine_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("machine_orders.id"), nullable=False),
        sa.Column("messages_json", postgresql.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_chat_sessions_scenario_id", "ai_chat_sessions", ["scenario_id"])
    op.create_index("ix_ai_chat_sessions_machine_order_id", "ai_chat_sessions", ["machine_order_id"])


def downgrade() -> None:
    op.drop_table("ai_chat_sessions")
    op.drop_table("ai_suggestions")
    op.drop_table("delay_events")
    op.drop_table("schedule_entries")
    op.drop_table("schedule_scenarios")
    op.drop_table("missing_components")
    op.drop_table("operator_calendar")
    op.drop_table("operations")
    op.drop_table("routings")
    op.drop_table("reference_point_precedences")
    op.drop_table("reference_points")
    op.drop_table("z_orders_link")
    op.drop_table("production_orders")
    op.drop_table("machine_orders")
    op.drop_table("operators")
    op.drop_table("skill_workcenter_mapping")
    op.drop_table("shifts")
    op.drop_table("machine_models")
    op.drop_table("workcenters")

    # Drop enum types
    for enum_name in [
        "machineorderstatus", "productionorderlevel", "productionorderstatus",
        "executionmode", "operationtype", "operationstatus", "targetlevel",
        "skilltype", "scheduleentrystatus", "objectivemode",
        "delayeventtype", "aisuggestiontype",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
