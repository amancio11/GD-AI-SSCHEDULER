"""Context Extractor — serialises DB state for AI prompt construction.

Keeps output under ~4 000 tokens by truncating long lists.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_ENTRIES = 30   # max schedule entries to include in context
_MAX_MISSING = 20   # max missing component entries


class ContextExtractor:
    """Reads DB data and returns structured dicts suitable for AI prompts."""

    # ── Schedule context ──────────────────────────────────────────────────────

    async def get_schedule_context(
        self,
        scenario_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        """Return a rich context dict for the given scenario.

        Returns:
            {
              scenario: {id, name, objective, target_date},
              machine:  {order_id, description, status},
              schedule_summary: {total_ops, completed, in_progress, blocked, delayed},
              utilization_by_operator: {op_id: pct_float},
              active_delays: [...],
              missing_components: [...],
            }
        """
        from app.models.delay import DelayEvent
        from app.models.missing import MissingComponent
        from app.models.operator import Operator
        from app.models.schedule import ScheduleEntry, ScheduleScenario

        # Scenario
        scenario = await db.get(ScheduleScenario, scenario_id)
        if scenario is None:
            return {}

        machine_order = scenario.machine_order

        # Schedule entries
        entries_result = await db.execute(
            select(ScheduleEntry)
            .where(ScheduleEntry.scenario_id == scenario_id)
            .limit(_MAX_ENTRIES * 5)
        )
        entries = entries_result.scalars().all()

        total  = len(entries)
        by_status: dict[str, int] = defaultdict(int)
        for e in entries:
            by_status[e.status.value] += 1

        # Utilisation per operator (minutes_worked / total_minutes in snapshot)
        worked_min: dict[str, int] = defaultdict(int)
        for e in entries:
            mins = int((e.scheduled_end - e.scheduled_start).total_seconds() // 60)
            worked_min[str(e.operator_id)] += mins

        # Active delays
        delays_result = await db.execute(
            select(DelayEvent)
            .where(DelayEvent.machine_order_id == scenario.machine_order_id)
            .order_by(DelayEvent.delay_from)
        )
        delays = delays_result.scalars().all()

        # Missing components
        mc_result = await db.execute(
            select(MissingComponent)
            .where(MissingComponent.is_arrived.is_(False))
            .limit(_MAX_MISSING)
        )
        missing = mc_result.scalars().all()

        return {
            "scenario": {
                "id":          str(scenario_id),
                "name":        scenario.name,
                "objective":   scenario.objective_mode.value,
                "target_date": str(scenario.target_finish_date) if scenario.target_finish_date else None,
            },
            "machine": {
                "order_id":    str(scenario.machine_order_id),
                "description": machine_order.description if machine_order else None,
                "status":      machine_order.status.value if machine_order else None,
            },
            "schedule_summary": {
                "total_ops":   total,
                "completed":   by_status.get("COMPLETED", 0),
                "in_progress": by_status.get("IN_PROGRESS", 0),
                "blocked":     by_status.get("BLOCKED", 0),
                "delayed":     by_status.get("DELAYED", 0),
                "scheduled":   by_status.get("SCHEDULED", 0),
            },
            "utilization_by_operator": {
                op_id: round(mins / max(480, 1), 3)
                for op_id, mins in worked_min.items()
            },
            "active_delays": [
                {
                    "id":          str(d.id),
                    "type":        d.event_type.value,
                    "from":        str(d.delay_from),
                    "until":       str(d.delay_until),
                    "description": d.description,
                }
                for d in delays[:10]
            ],
            "missing_components": [
                {
                    "id":               str(mc.id),
                    "material":         mc.component_material,
                    "description":      mc.description,
                    "arrival_date":     str(mc.expected_arrival_date) if mc.expected_arrival_date else None,
                    "production_order": str(mc.production_order_id),
                }
                for mc in missing
            ],
        }

    # ── Delay context ─────────────────────────────────────────────────────────

    async def get_delay_context(
        self,
        delay_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        """Return context for a specific delay event."""
        from app.models.delay import DelayEvent

        delay = await db.get(DelayEvent, delay_id)
        if delay is None:
            return {}
        return {
            "id":          str(delay.id),
            "type":        delay.event_type.value,
            "from":        str(delay.delay_from),
            "until":       str(delay.delay_until),
            "description": delay.description,
            "requires_reschedule": delay.requires_reschedule,
            "machine_order_id": str(delay.machine_order_id),
        }

    # ── Entry context (explain-entry) ─────────────────────────────────────────

    async def get_entry_context(
        self,
        entry_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        """Return the scheduling constraints that determined an entry's
        start/end/operator assignment.
        """
        from app.models.schedule import ScheduleEntry

        entry = await db.get(ScheduleEntry, entry_id)
        if entry is None:
            return {}

        op = entry.operation
        routing = op.routing if op else None
        po = routing.production_order if routing else None

        return {
            "entry": {
                "id":              str(entry.id),
                "scheduled_start": str(entry.scheduled_start),
                "scheduled_end":   str(entry.scheduled_end),
                "status":          entry.status.value,
                "delay_minutes":   entry.delay_minutes,
                "is_manual_override": entry.is_manual_override,
            },
            "operation": {
                "id":               str(op.id) if op else None,
                "type":             op.operation_type.value if op else None,
                "description":      op.description if op else None,
                "planned_duration": op.planned_duration_minutes if op else None,
                "progress_pct":     op.progress_pct if op else None,
                "reference_point":  str(op.reference_point_id) if op and op.reference_point_id else None,
            },
            "production_order": {
                "id":          str(po.id) if po else None,
                "sap_id":      po.sap_order_id if po else None,
                "description": po.description if po else None,
                "level":       po.level.value if po else None,
            },
            "operator": {
                "id":   str(entry.operator_id),
                "name": entry.operator.full_name if entry.operator else None,
                "skill": entry.operator.skill.value if entry.operator else None,
            },
        }
