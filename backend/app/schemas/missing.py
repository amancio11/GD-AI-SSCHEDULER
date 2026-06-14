"""Pydantic v2 schemas for MissingComponent."""
from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict


class MissingComponentBase(BaseModel):
    production_order_id: uuid.UUID
    component_material: str
    description: str | None = None
    expected_arrival_date: date | None = None
    is_arrived: bool = False
    arrival_confirmed_date: date | None = None
    manually_flagged: bool = False
    notes: str | None = None


class MissingComponentCreate(MissingComponentBase):
    pass


class MissingComponentUpdate(BaseModel):
    description: str | None = None
    expected_arrival_date: date | None = None
    is_arrived: bool | None = None
    arrival_confirmed_date: date | None = None
    manually_flagged: bool | None = None
    notes: str | None = None


class MissingComponentRead(MissingComponentBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
