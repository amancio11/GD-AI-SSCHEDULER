"""Centralised enumerations — imported by models, schemas and business logic."""
from __future__ import annotations

import enum


class MachineOrderStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class ProductionOrderLevel(str, enum.Enum):
    MACHINE = "MACHINE"
    MACROAGGREGATE = "MACROAGGREGATE"
    AGGREGATE = "AGGREGATE"
    GROUP = "GROUP"
    COMPONENT = "COMPONENT"


class ProductionOrderStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    MISSING = "MISSING"


class ExecutionMode(str, enum.Enum):
    SIMULTANEOUS = "SIMULTANEOUS"


class OperationType(str, enum.Enum):
    ELECTRICAL = "ELECTRICAL"
    MECHANICAL = "MECHANICAL"
    GENERAL = "GENERAL"


class OperationStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"


class TargetLevel(str, enum.Enum):
    MACROAGGREGATE = "MACROAGGREGATE"
    AGGREGATE = "AGGREGATE"
    GROUP = "GROUP"


class SkillType(str, enum.Enum):
    ELECTRICAL = "ELECTRICAL"
    MECHANICAL = "MECHANICAL"
    MULTI = "MULTI"


class ScheduleEntryStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    DELAYED = "DELAYED"
    STALE = "STALE"


class ObjectiveMode(str, enum.Enum):
    FINISH_BY_DATE = "FINISH_BY_DATE"
    MAXIMIZE_RESOURCE_UTILIZATION = "MAXIMIZE_RESOURCE_UTILIZATION"
    MINIMIZE_OPERATORS = "MINIMIZE_OPERATORS"
    CUSTOM = "CUSTOM"


class DelayEventType(str, enum.Enum):
    OPERATOR_ABSENCE = "OPERATOR_ABSENCE"
    COMPONENT_DELAY = "COMPONENT_DELAY"
    MANUAL_OPERATION_DELAY = "MANUAL_OPERATION_DELAY"
    OTHER = "OTHER"


class AiSuggestionType(str, enum.Enum):
    ON_DEMAND = "ON_DEMAND"
    PROACTIVE = "PROACTIVE"
    DELAY_ANALYSIS = "DELAY_ANALYSIS"
    HISTORICAL_PATTERN = "HISTORICAL_PATTERN"
    WHAT_IF = "WHAT_IF"
    EXPLAIN_ENTRY = "EXPLAIN_ENTRY"
