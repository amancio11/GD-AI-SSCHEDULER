"""Models package — imports all models so that Alembic sees them."""
from app.models.ai import AiChatSession, AiSuggestion
from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.delay import DelayEvent
from app.models.machine import MachineModel, MachineOrder
from app.models.missing import MissingComponent
from app.models.operator import Operator, OperatorCalendar, Shift
from app.models.production import ProductionOrder, ZOrdersLink
from app.models.reference import ReferencePoint, ReferencePointPrecedence
from app.models.resource import ResourceType
from app.models.routing import Operation, Routing
from app.models.schedule import ScheduleEntry, ScheduleScenario
from app.models.workcenter import SkillWorkcenterMapping, Workcenter
from app.core.state_engine.models_audit import OperationStatusAudit  # noqa: F401

__all__ = [
    "Base",
    "UUIDMixin",
    "TimestampMixin",
    "MachineModel",
    "MachineOrder",
    "ProductionOrder",
    "ZOrdersLink",
    "Routing",
    "Operation",
    "ReferencePoint",
    "ReferencePointPrecedence",
    "Workcenter",
    "SkillWorkcenterMapping",
    "Operator",
    "Shift",
    "OperatorCalendar",
    "ResourceType",
    "MissingComponent",
    "ScheduleScenario",
    "ScheduleEntry",
    "DelayEvent",
    "AiSuggestion",
    "AiChatSession",
    "OperationStatusAudit",
]
