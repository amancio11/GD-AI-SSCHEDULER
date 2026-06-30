"""Capacity-based greedy scheduler (RCPSP-style list scheduling).

Sostituisce il modello CP-SAT a segmenti (intrattabile su questa scala) con uno
scheduler costruttivo a regole di dispatching, allineato al modello di risorse a
**capacità di gruppo**:

  * Una risorsa NON è un individuo con nome, ma un tipo: (workcenter, skill, ore/giorno).
    `count` risorse dello stesso tipo = `count` "corsie" (lanes) parallele, ognuna con
    `single_daily_minutes` di capacità al giorno.
  * Un'operazione è lavorata da **una risorsa alla volta**: al più `single_daily_minutes`
    al giorno; se dura di più si spalma su più giorni / corsie (hand-off).
  * Le operazioni si piazzano in ordine topologico (precedenze + RP + parent-wait),
    earliest-fit: la prima capacità disponibile a partire dal loro earliest-start.
    Earliest-fit ⇒ piano compatto = "massimizza utilizzo risorse", senza buchi inutili.

Sempre fattibile (se esiste capacità entro l'orizzonte), deterministico, in millisecondi.
Niente solver: nessun INFEASIBLE/UNKNOWN spurio.
"""
from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.scheduler.cpsat_types import SchedulableOperation, _SKILL_CAN_DO
from app.enums import OperationType, SkillType

import logging

logger = logging.getLogger(__name__)

DAY_MINUTES = 1440


@dataclass
class ResourceGroup:
    """Capacità di un gruppo (workcenter + skill), variabile per GIORNO della settimana.

    `weekday_count[wd]`   = numero di risorse quel giorno (wd: 0=lun … 6=dom);
    `weekday_minutes[wd]` = finestra in minuti di UNA risorsa quel giorno (0 = giorno non lavorato).
    Orario d'inizio comune (08:00) gestito dallo scheduler.
    """

    workcenter_id: uuid.UUID
    skill: SkillType
    resource_type_id: uuid.UUID | None = None
    weekday_count: dict[int, int] = field(default_factory=dict)
    weekday_minutes: dict[int, int] = field(default_factory=dict)

    def count_for(self, wd: int) -> int:
        return max(0, self.weekday_count.get(wd, 0))

    def minutes_for(self, wd: int) -> int:
        return max(0, self.weekday_minutes.get(wd, 0))

    @property
    def max_count(self) -> int:
        return max(self.weekday_count.values(), default=0)

    @property
    def max_minutes(self) -> int:
        return max(self.weekday_minutes.values(), default=0)

    @property
    def weekly_capacity_minutes(self) -> int:
        return sum(self.count_for(wd) * self.minutes_for(wd) for wd in range(7))

    @classmethod
    def uniform(
        cls, workcenter_id: uuid.UUID, skill: SkillType, single_daily_minutes: int,
        count: int, resource_type_id: uuid.UUID | None = None,
        working_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
    ) -> "ResourceGroup":
        """Costruttore comodo: stessa capacità lun–ven, weekend a zero."""
        wc = {wd: (count if wd in working_weekdays else 0) for wd in range(7)}
        wm = {wd: (single_daily_minutes if wd in working_weekdays else 0) for wd in range(7)}
        return cls(workcenter_id=workcenter_id, skill=skill, resource_type_id=resource_type_id,
                   weekday_count=wc, weekday_minutes=wm)


@dataclass
class CapacityEntry:
    """Un blocco di lavoro piazzato: una porzione di un'operazione su una corsia."""

    operation_id: uuid.UUID
    workcenter_id: uuid.UUID
    resource_type_id: uuid.UUID | None
    skill: SkillType
    lane_index: int
    start_minutes: int
    end_minutes: int

    @property
    def minutes(self) -> int:
        return self.end_minutes - self.start_minutes


@dataclass
class CapacityResult:
    status: str                                   # "OPTIMAL" | "INFEASIBLE"
    entries: list[CapacityEntry] = field(default_factory=list)
    makespan_minutes: int | None = None
    conflicts: list[str] = field(default_factory=list)
    completion: dict[uuid.UUID, int] = field(default_factory=dict)  # op_id → end minute


class CapacityScheduler:
    def __init__(
        self,
        operations: list[SchedulableOperation],
        resource_groups: list[ResourceGroup],
        horizon_minutes: int,
        epoch: datetime,
        precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]] | None = None,
        rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] | None = None,
        parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] | None = None,
        missing_constraints: dict[uuid.UUID, int] | None = None,
        op_priority: dict[uuid.UUID, int] | None = None,
        day_start_offset_minutes: int = 8 * 60,
        working_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4}),
    ) -> None:
        self.operations = operations
        self.horizon = horizon_minutes
        self.epoch = epoch
        self.precedence_pairs = precedence_pairs or []
        self.rp_order_constraints = rp_order_constraints or []
        self.parent_wait_constraints = parent_wait_constraints or []
        self.missing_constraints = missing_constraints or {}
        self._op_priority: dict[uuid.UUID, int] = op_priority or {}
        self.day_start = day_start_offset_minutes
        self.working_weekdays = working_weekdays

        self._op_index = {op.id: op for op in operations}
        self._groups: dict[tuple[uuid.UUID, SkillType], ResourceGroup] = {
            (g.workcenter_id, g.skill): g for g in resource_groups
        }
        # Uso per (gruppo, giorno, corsia) → minuti consumati dall'inizio finestra.
        self._lane_used: dict[tuple[tuple, int, int], int] = {}

    # ── helpers ────────────────────────────────────────────────────────────────

    def _residual(self, op: SchedulableOperation) -> int:
        import math
        return max(0, int(math.ceil(op.planned_duration_minutes * (1.0 - op.progress_pct / 100.0))))

    def _weekday(self, day_index: int) -> int:
        return (self.epoch + timedelta(days=day_index)).weekday()

    def _candidate_groups(self, op: SchedulableOperation) -> list[ResourceGroup]:
        """Gruppi nello stesso workcenter la cui skill può fare il tipo operazione."""
        out: list[ResourceGroup] = []
        for (wc, skill), g in self._groups.items():
            if wc == op.workcenter_id and op.operation_type in _SKILL_CAN_DO.get(skill, set()):
                out.append(g)
        return out

    def _earliest_slot(self, group: ResourceGroup, cursor: int):
        """Prima capacità disponibile ≥ cursor: (start, day, lane, window_end) o None.

        Scansiona i giorni dal cursor: per ogni giorno usa `count_for`/`minutes_for`
        del gruppo (variabili per giorno della settimana). Sceglie la corsia con lo
        start più precoce in quel giorno."""
        gkey = (group.workcenter_id, group.skill)
        day = cursor // DAY_MINUTES
        max_day = (self.horizon // DAY_MINUTES) + 2
        while day <= max_day:
            wd = self._weekday(day)
            c = group.count_for(wd)
            mins = group.minutes_for(wd)
            if c > 0 and mins > 0:
                ds = day * DAY_MINUTES + self.day_start
                we = ds + mins
                best_lane: int | None = None
                best_start: int | None = None
                for lane in range(c):
                    used = self._lane_used.get((gkey, day, lane), 0)
                    cand = max(cursor, ds + used)
                    if cand < we and (best_start is None or cand < best_start):
                        best_start, best_lane = cand, lane
                if best_lane is not None:
                    return (best_start, day, best_lane, we)
            day += 1
        return None

    # ── precedence / topo order ──────────────────────────────────────────────────

    def _build_preds(self) -> dict[uuid.UUID, set[uuid.UUID]]:
        ids = set(self._op_index)
        preds: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        # UNICO vincolo HARD: BOM ordine-livello.
        # Ogni op di un ordine padre aspetta che TUTTI i figli BOM (ricorsivi) siano
        # completati. Questo è l'unico vincolo bloccante.
        #
        # precedence_pairs (rp_direct_pairs) NON è hard: l'ordinamento RP (intra-routing
        # e cross-subtree) è gestito SOLO tramite op_priority come priorità di dispatch.
        # Se op1 è bloccata (mancanti) si può lavorare op2 senza aspettare op1.
        for ops_target, parent_op_id in self.parent_wait_constraints:
            if parent_op_id in ids:
                for target_id in (t for t in ops_target if t in ids):
                    preds[parent_op_id].add(target_id)
        return preds

    def _topo_order(self, preds: dict[uuid.UUID, set[uuid.UUID]]) -> list[uuid.UUID]:
        ids = list(self._op_index)
        indeg = {oid: len(preds.get(oid, ())) for oid in ids}
        succ: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for oid in ids:
            for p in preds.get(oid, ()):
                succ[p].append(oid)
        # ready set ordinato per (earliest_start, op_priority).
        # op_priority = rp_level * 10000 + seq_number: op con priorità minore
        # (RP level basso, seq basso) vengono dispatchate prima. Con più risorse
        # disponibili le op a priorità alta e bassa lavorano comunque in parallelo
        # (il vincolo è solo su chi parte prima quando la capacità è limitata).
        def dispatch_key(oid: uuid.UUID) -> tuple:
            op = self._op_index[oid]
            est = max(op.earliest_start_minutes, self.missing_constraints.get(oid, 0))
            return (est, self._op_priority.get(oid, 0))

        ready = sorted([oid for oid in ids if indeg[oid] == 0], key=dispatch_key)
        order: list[uuid.UUID] = []
        ready_q = deque(ready)
        while ready_q:
            cur = ready_q.popleft()
            order.append(cur)
            newly: list[uuid.UUID] = []
            for s in succ[cur]:
                indeg[s] -= 1
                if indeg[s] == 0:
                    newly.append(s)
            if newly:
                merged = sorted(list(ready_q) + newly, key=dispatch_key)
                ready_q = deque(merged)
        if len(order) != len(ids):  # ciclo: appende i rimanenti
            logger.warning("Capacity scheduler: ciclo nelle precedenze, %d op non ordinate", len(ids) - len(order))
            order.extend(oid for oid in ids if oid not in set(order))
        return order

    # ── main ─────────────────────────────────────────────────────────────────────

    def solve(self) -> CapacityResult:
        preds = self._build_preds()
        order = self._topo_order(preds)

        entries: list[CapacityEntry] = []
        completion: dict[uuid.UUID, int] = {}
        conflicts: list[str] = []

        for oid in order:
            op = self._op_index[oid]
            D = self._residual(op)
            est = max(op.earliest_start_minutes, self.missing_constraints.get(oid, 0))
            for p in preds.get(oid, ()):
                if p in completion:
                    est = max(est, completion[p])

            if D <= 0:
                completion[oid] = est
                continue

            groups = self._candidate_groups(op)
            if not groups:
                conflicts.append(
                    f"Op {op.id} ({op.operation_type.value}): nessun gruppo risorse "
                    f"(workcenter/skill) configurato"
                )
                continue

            remaining = D
            cursor = est
            op_completion = est
            guard = 0
            max_iter = 10000
            while remaining > 0 and guard < max_iter:
                guard += 1
                # tra tutti i gruppi candidati, scegli la capacità con lo start più precoce
                best = None  # (start, group, day, lane, window_end)
                for g in groups:
                    slot = self._earliest_slot(g, cursor)
                    if slot is None:
                        continue
                    if best is None or slot[0] < best[0]:
                        best = (slot[0], g, slot[1], slot[2], slot[3])
                if best is None:
                    conflicts.append(
                        f"Op {op.id}: capacità esaurita entro l'orizzonte "
                        f"({remaining} min residui non allocati)"
                    )
                    break
                start, g, day, lane, we = best
                gkey = (g.workcenter_id, g.skill)
                ds = day * DAY_MINUTES + self.day_start
                m = min(remaining, we - start)
                end = start + m
                entries.append(CapacityEntry(
                    operation_id=op.id, workcenter_id=op.workcenter_id,
                    resource_type_id=g.resource_type_id, skill=g.skill,
                    lane_index=lane, start_minutes=start, end_minutes=end,
                ))
                self._lane_used[(gkey, day, lane)] = end - ds   # corsia occupata fino a end
                cursor = end             # una risorsa alla volta → prossimo blocco dopo end
                remaining -= m
                op_completion = end

            completion[oid] = op_completion

        makespan = max(completion.values(), default=0)
        status = "OPTIMAL" if not any("capacità esaurita" in c or "nessun gruppo" in c for c in conflicts) else "INFEASIBLE"
        return CapacityResult(
            status=status, entries=entries, makespan_minutes=makespan if entries else None,
            conflicts=conflicts, completion=completion,
        )
