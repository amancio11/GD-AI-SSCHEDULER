"""Pydantic v2 schemas for ScheduleScenario, ScheduleEntry and related types."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.enums import ObjectiveMode, ScheduleEntryStatus


# ── ScheduleScenario ──────────────────────────────────────────────────────────

class ScheduleScenarioBase(BaseModel):
    name: str
    description: str | None = None
    machine_order_id: uuid.UUID
    objective_mode: ObjectiveMode = ObjectiveMode.FINISH_BY_DATE
    start_date: date | None = None
    target_finish_date: date | None = None
    resource_set_json: dict | None = None
    is_active: bool = False
    is_baseline: bool = False
    ai_explanation: str | None = None


class ScheduleScenarioCreate(ScheduleScenarioBase):
    pass


class ScheduleScenarioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    machine_order_id: uuid.UUID | None = None
    objective_mode: ObjectiveMode | None = None
    start_date: date | None = None
    target_finish_date: date | None = None
    resource_set_json: dict | None = None
    is_active: bool | None = None
    is_baseline: bool | None = None
    ai_explanation: str | None = None


class ScheduleScenarioRead(ScheduleScenarioBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    last_run_status: str | None = None
    last_run_at: datetime | None = None
    last_run_makespan_days: float | None = None
    last_run_operators_used: int | None = None
    last_run_conflicts: list | None = None
    last_run_summary: dict | None = None

# ── ScheduleEntry ─────────────────────────────────────────────────────────────

class ScheduleEntryBase(BaseModel):
    scenario_id: uuid.UUID
    operation_id: uuid.UUID
    operator_id: uuid.UUID
    workcenter_id: uuid.UUID
    scheduled_start: datetime
    scheduled_end: datetime
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    status: ScheduleEntryStatus = ScheduleEntryStatus.SCHEDULED
    interruption_reason: str | None = None
    delay_minutes: int = 0
    is_manual_override: bool = False


class ScheduleEntryCreate(ScheduleEntryBase):
    pass


class ScheduleEntryUpdate(BaseModel):
    status: ScheduleEntryStatus | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    interruption_reason: str | None = None
    delay_minutes: int | None = None


class ScheduleEntryRead(ScheduleEntryBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── GanttEntry ────────────────────────────────────────────────────────────────

class GanttEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    operation_id: uuid.UUID
    operation_desc: str | None
    order_id: uuid.UUID
    order_desc: str | None
    operator_id: uuid.UUID
    operator_name: str
    workcenter_id: uuid.UUID
    start: datetime
    end: datetime
    status: ScheduleEntryStatus
    color: str
    is_critical_path: bool = False
    is_manual_override: bool = False


# ── Run request ───────────────────────────────────────────────────────────────

class ScheduleRunRequest(BaseModel):
    scenario_id: uuid.UUID
    objective_mode: ObjectiveMode
    objective_params_json: dict | None = None


# ── Scenario comparison ───────────────────────────────────────────────────────

class ScenarioComparisonResult(BaseModel):
    delta_makespan_days: float | None
    delta_operators: int | None
    delta_utilization: float | None
    gantt_a: list[GanttEntry]
    gantt_b: list[GanttEntry]


# ── Override operation ────────────────────────────────────────────────────────

class OverrideOperationRequest(BaseModel):
    operation_id: uuid.UUID
    new_start: datetime
    new_end: datetime
    operator_id: uuid.UUID
