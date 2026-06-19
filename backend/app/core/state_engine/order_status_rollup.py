"""Order Status Rollup — propaga lo stato bottom-up lungo la gerarchia BOM.

Quando lo stato di un'operazione o di un ProductionOrder cambia, l'effetto
deve propagarsi verso l'alto nella BOM:

    COMPONENT (nessuno stato proprio, solo is_arrived)
       └─ GROUP        ← deriva da: stato proprie operazioni + missing_components
            └─ AGGREGATE    ← deriva da: stato dei GROUP figli
                 └─ MACROAGGREGATE  ← deriva da: stato degli AGGREGATE figli
                      └─ MACHINE        ← deriva da: stato dei MACROAGGREGATE figli

La relazione padre/figlio è materializzata in due posti nello schema:
  - ProductionOrder.parent_order_id (self-FK diretta)
  - z_orders_link (tabella di linking esplicita, usata per i casi con
    parent_material che non mappano 1:1 su un FK — vedi GUIDA_TECNICA)

Questo modulo usa `parent_order_id` come fonte primaria (è la relazione
diretta usata anche dal resto dello scheduler in `reschedule_engine.py` per
`children_map`), con fallback su `z_orders_link` se `parent_order_id` è NULL
per quel nodo (può succedere per import SAP legacy).

ECCEZIONE MISSING
------------------
Se un GROUP ha componenti mancanti non arrivati (missing_components con
is_arrived=False), il suo stato è MISSING e questo modulo lo lascia MISSING
(sticky) finché `missing_components.mark-arrived` non lo sblocca — vedi
`transitions.compute_rollup_status`.

USO TIPICO
----------
    rollup = OrderStatusRollup(session)
    changed = rollup.propagate_from(operation.routing.production_order_id)
    # `changed` è la lista ordinata (bottom-up) di ProductionOrder il cui
    # stato è stato effettivamente modificato — utile per il WebSocket diff.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import OperationStatus, ProductionOrderLevel, ProductionOrderStatus
from app.models.production import ProductionOrder, ZOrdersLink
from app.models.routing import Operation, Routing
from app.models.missing import MissingComponent
from app.core.state_engine.transitions import compute_rollup_status

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RollupChange:
    production_order_id: uuid.UUID
    material_code: str
    old_status: ProductionOrderStatus
    new_status: ProductionOrderStatus


class OrderStatusRollup:
    """Propaga lo stato di un ProductionOrder verso l'alto nella BOM."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ──────────────────────────────────────────────────────────────────────
    # API pubblica
    # ──────────────────────────────────────────────────────────────────────

    def propagate_from(self, production_order_id: uuid.UUID) -> list[RollupChange]:
        """Ricalcola lo stato di `production_order_id` e di TUTTI i suoi
        antenati nella BOM, fino alla MACHINE root.

        Ritorna la lista ordinata (dal nodo più vicino alla foglia fino alla
        radice) dei nodi il cui stato è effettivamente cambiato.
        """
        changes: list[RollupChange] = []
        current_id: uuid.UUID | None = production_order_id
        visited: set[uuid.UUID] = set()

        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            order = self.session.get(ProductionOrder, current_id)
            if order is None:
                break

            new_status = self._compute_status_for(order)
            if new_status != order.status:
                old_status = order.status
                order.status = new_status
                self.session.flush()
                changes.append(
                    RollupChange(
                        production_order_id=order.id,
                        material_code=order.material_code,
                        old_status=old_status,
                        new_status=new_status,
                    )
                )
                logger.info(
                    "Rollup stato: %s (%s) %s → %s",
                    order.material_code, order.level.value,
                    old_status.value, new_status.value,
                )

            current_id = self._parent_id_of(order)

        return changes

    # ──────────────────────────────────────────────────────────────────────
    # Calcolo dello stato per un singolo nodo
    # ──────────────────────────────────────────────────────────────────────

    def _compute_status_for(self, order: ProductionOrder) -> ProductionOrderStatus:
        """Determina lo stato corretto per `order` in base al suo livello.

        - COMPONENT: non gestito qui (i componenti non hanno un rollup status
          proprio nello schema attuale — sono o "missing" o "ok").
        - GROUP: deriva dalle proprie OPERAZIONI (ha routing, niente RP) +
          dai missing_components collegati.
        - AGGREGATE / MACROAGGREGATE / MACHINE: deriva dai ProductionOrder
          figli diretti nella BOM (stesso identico criterio di
          compute_rollup_status, riusato).
        """
        if order.level == ProductionOrderLevel.COMPONENT:
            return order.status  # i componenti restano gestiti da missing_components

        # MISSING è sticky indipendentemente dal livello — controllato per
        # primo per coerenza con compute_rollup_status.
        if order.status == ProductionOrderStatus.MISSING:
            if order.level == ProductionOrderLevel.GROUP:
                if self._has_unarrived_missing(order.id):
                    return ProductionOrderStatus.MISSING
                # i mancanti sono arrivati: ricalcola normalmente sotto
            else:
                return ProductionOrderStatus.MISSING

        if order.level == ProductionOrderLevel.GROUP:
            return self._status_from_operations(order)

        # AGGREGATE, MACROAGGREGATE, MACHINE → status dai figli BOM diretti
        child_statuses = self._direct_children_statuses(order.id)
        return compute_rollup_status(child_statuses, order.status)

    def _status_from_operations(self, group_order: ProductionOrder) -> ProductionOrderStatus:
        """Stato di un GROUP derivato dalle operazioni del proprio routing."""
        # Missing component non ancora arrivato → MISSING (sticky)
        if self._has_unarrived_missing(group_order.id):
            return ProductionOrderStatus.MISSING

        routing = self.session.execute(
            select(Routing).where(Routing.production_order_id == group_order.id)
        ).scalar_one_or_none()
        if routing is None:
            return group_order.status

        op_statuses = list(
            self.session.execute(
                select(Operation.status).where(Operation.routing_id == routing.id)
            ).scalars()
        )
        if not op_statuses:
            return group_order.status

        if all(s == OperationStatus.COMPLETED for s in op_statuses):
            return ProductionOrderStatus.COMPLETED
        if any(s == OperationStatus.BLOCKED for s in op_statuses):
            return ProductionOrderStatus.BLOCKED
        if any(
            s in (OperationStatus.IN_PROGRESS, OperationStatus.COMPLETED, OperationStatus.INTERRUPTED)
            for s in op_statuses
        ):
            return ProductionOrderStatus.IN_PROGRESS
        return ProductionOrderStatus.PLANNED

    def _has_unarrived_missing(self, production_order_id: uuid.UUID) -> bool:
        result = self.session.execute(
            select(MissingComponent.id).where(
                MissingComponent.production_order_id == production_order_id,
                MissingComponent.is_arrived.is_(False),
            ).limit(1)
        ).first()
        return result is not None

    # ──────────────────────────────────────────────────────────────────────
    # Navigazione BOM
    # ──────────────────────────────────────────────────────────────────────

    def _direct_children_statuses(self, parent_id: uuid.UUID) -> list[ProductionOrderStatus]:
        """Stati dei figli diretti, via parent_order_id con fallback z_orders_link."""
        children = list(
            self.session.execute(
                select(ProductionOrder.status).where(
                    ProductionOrder.parent_order_id == parent_id
                )
            ).scalars()
        )
        if children:
            return children

        # Fallback: alcuni import legacy popolano solo z_orders_link
        linked_child_ids = list(
            self.session.execute(
                select(ZOrdersLink.child_order_id).where(
                    ZOrdersLink.parent_order_id == parent_id
                )
            ).scalars()
        )
        if not linked_child_ids:
            return []
        return list(
            self.session.execute(
                select(ProductionOrder.status).where(
                    ProductionOrder.id.in_(linked_child_ids)
                )
            ).scalars()
        )

    def _parent_id_of(self, order: ProductionOrder) -> uuid.UUID | None:
        if order.parent_order_id is not None:
            return order.parent_order_id
        # Fallback via z_orders_link (questo nodo come child_order_id)
        link = self.session.execute(
            select(ZOrdersLink.parent_order_id).where(
                ZOrdersLink.child_order_id == order.id
            ).limit(1)
        ).scalar_one_or_none()
        return link