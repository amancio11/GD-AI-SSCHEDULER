"""Router: ResourceType — configurazione delle risorse a capacità di gruppo.

Una risorsa NON è un individuo, ma un tipo: (workcenter, skill, ore/giorno, count).
Lo scheduler greedy somma la capacità del gruppo (count × ore/giorno) e vi alloca
le operazioni rispettando le precedenze. Questa è la "sezione calendario risorse".
"""
from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.resource import ResourceType
from app.models.workcenter import Workcenter
from app.schemas.resource import (
    ResourceTypeCreate,
    ResourceTypeRead,
    ResourceTypeUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resource-types", tags=["resource-types"])

# Router ausiliario: lista workcenter per i dropdown della UI risorse.
workcenters_router = APIRouter(prefix="/workcenters", tags=["workcenters"])


@workcenters_router.get("")
async def list_workcenters(db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(Workcenter).order_by(Workcenter.code))
    return [
        {"id": str(w.id), "code": w.code, "name": w.name, "is_active": w.is_active}
        for w in result.scalars().all()
    ]


@router.get("", response_model=list[ResourceTypeRead])
async def list_resource_types(
    workcenter_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[ResourceType]:
    query = select(ResourceType)
    if workcenter_id:
        query = query.where(ResourceType.workcenter_id == workcenter_id)
    if active_only:
        query = query.where(ResourceType.is_active.is_(True))
    query = query.order_by(ResourceType.workcenter_id, ResourceType.skill)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{resource_type_id}", response_model=ResourceTypeRead)
async def get_resource_type(
    resource_type_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ResourceType:
    obj = await db.get(ResourceType, resource_type_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Tipo risorsa non trovato")
    return obj


@router.post("", response_model=ResourceTypeRead, status_code=201)
async def create_resource_type(
    payload: ResourceTypeCreate,
    db: AsyncSession = Depends(get_db),
) -> ResourceType:
    # Unicità (workcenter, skill): un solo tipo per combinazione.
    exists = await db.execute(
        select(ResourceType).where(
            ResourceType.workcenter_id == payload.workcenter_id,
            ResourceType.skill == payload.skill,
        )
    )
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="Esiste già un tipo risorsa per questo workcenter e skill",
        )
    obj = ResourceType(**payload.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{resource_type_id}", response_model=ResourceTypeRead)
async def update_resource_type(
    resource_type_id: uuid.UUID,
    payload: ResourceTypeUpdate,
    db: AsyncSession = Depends(get_db),
) -> ResourceType:
    obj = await db.get(ResourceType, resource_type_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Tipo risorsa non trovato")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{resource_type_id}", status_code=204, response_class=Response)
async def delete_resource_type(
    resource_type_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    obj = await db.get(ResourceType, resource_type_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Tipo risorsa non trovato")
    await db.delete(obj)
    await db.commit()
    return Response(status_code=204)
