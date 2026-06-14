"""Infeasibility Analyzer — explains *why* a CP-SAT model is INFEASIBLE.

All output strings are in Italian, as required by the planner UX spec.
"""
from __future__ import annotations

import uuid

import networkx as nx

from app.core.scheduler.cpsat_types import (
    QualifiedOperator,
    SchedulableOperation,
    operator_can_do,
)


class InfeasibilityAnalyzer:
    """Produces human-readable Italian explanations for INFEASIBLE schedules."""

    def analyze(
        self,
        model,                                      # cp_model.CpModel (unused directly)
        operations: list[SchedulableOperation],
        operators: list[QualifiedOperator],
        missing_constraints: dict[uuid.UUID, int],  # op_id → earliest_start_minute
        precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]],
        infeasibility_reasons: list[str],           # pre-collected by the builder
        horizon_minutes: int = 0,
        target_finish_minutes: int | None = None,
    ) -> list[str]:
        """Return a deduplicated list of Italian explanations for infeasibility.

        Checks performed:
          1. Operations with no qualified operators (workcenter/skill mismatch).
          2. Missing-component constraints that push an op past the horizon.
          3. Cyclic dependencies (safeguard).
          4. Target finish date impossible given total load.
        """
        reasons: list[str] = list(infeasibility_reasons)  # copy pre-collected

        # ── Check 1: no qualified operators ───────────────────────────────────
        oper_by_wc: dict[uuid.UUID, list[QualifiedOperator]] = {}
        for oper in operators:
            oper_by_wc.setdefault(oper.workcenter_id, []).append(oper)

        for op in operations:
            qualified = [
                o for o in oper_by_wc.get(op.workcenter_id, [])
                if operator_can_do(o, op.operation_type)
            ]
            if not qualified:
                msg = (
                    f"L'operazione {op.id} (tipo {op.operation_type.value}) "
                    f"non ha operatori qualificati nel workcenter {op.workcenter_id}"
                )
                if msg not in reasons:
                    reasons.append(msg)

        # ── Check 2: missing-component constraint pushes op past horizon ───────
        if horizon_minutes > 0:
            for op_id, earliest_start in missing_constraints.items():
                op = next((o for o in operations if o.id == op_id), None)
                if op is None:
                    continue
                min_duration = op.planned_duration_minutes  # worst case
                if earliest_start + min_duration > horizon_minutes:
                    msg = (
                        f"L'operazione {op_id} ha un vincolo di mancante che richiede "
                        f"inizio al minuto {earliest_start}, ma con la durata minima "
                        f"({min_duration} min) supera l'horizon ({horizon_minutes} min)."
                    )
                    if msg not in reasons:
                        reasons.append(msg)

        # ── Check 3: cyclic dependencies (safeguard) ──────────────────────────
        dag: nx.DiGraph = nx.DiGraph()
        for pred_id, succ_id in precedence_pairs:
            dag.add_edge(pred_id, succ_id)

        try:
            nx.find_cycle(dag)
            cycle_msg = "Le precedenze tra operazioni contengono un ciclo. Verificare il DAG dei reference point."
            if cycle_msg not in reasons:
                reasons.append(cycle_msg)
        except nx.NetworkXNoCycle:
            pass

        # ── Check 4: target finish date irraggiungibile ───────────────────────
        if target_finish_minutes is not None and operations:
            total_work = sum(
                max(
                    int((o.planned_duration_minutes * (1 - o.progress_pct / 100))),
                    1,
                )
                for o in operations
            )
            if total_work > target_finish_minutes:
                msg = (
                    f"Il carico totale residuo ({total_work} min) supera la data target "
                    f"({target_finish_minutes} min). Aumentare la data target o aggiungere risorse."
                )
                if msg not in reasons:
                    reasons.append(msg)

        return reasons

    def suggest_fixes(self, conflicts: list[str]) -> list[str]:
        """Return one actionable fix suggestion per conflict (in Italian)."""
        fixes: list[str] = []
        for conflict in conflicts:
            cl = conflict.lower()

            if "non ha operatori qualificati" in cl:
                # Extract op_type from the message
                op_type = "UNKNOWN"
                for t in ("ELECTRICAL", "MECHANICAL", "GENERAL"):
                    if t.lower() in cl:
                        op_type = t
                        break
                # Extract workcenter hint
                wc = "il workcenter indicato"
                if "nel workcenter" in cl:
                    idx = cl.find("nel workcenter")
                    wc_part = conflict[idx + len("nel workcenter"):].strip()
                    wc = wc_part.split()[0] if wc_part else wc
                fixes.append(
                    f"Aggiungi almeno un operatore con skill {op_type} "
                    f"(o MULTI) al workcenter {wc}."
                )

            elif "horizon" in cl or "supera l'horizon" in cl:
                fixes.append(
                    "Estendi l'horizon di schedulazione oppure rimuovi / posticipa "
                    "il vincolo del componente mancante."
                )

            elif "ciclo" in cl:
                fixes.append(
                    "Verifica e correggi le precedenze tra reference point: "
                    "il DAG non deve contenere cicli."
                )

            elif "carico totale" in cl or "data target" in cl:
                fixes.append(
                    "Posticipa la data di completamento target oppure aggiungi "
                    "operatori per ridurre il makespan stimato."
                )

            else:
                fixes.append(
                    f"Revisiona manualmente il vincolo: «{conflict[:120]}»."
                )

        return fixes
