"""CP-SAT cumulativo sul modello a CAPACITÀ DI GRUPPO (RCPSP a minuti).

Perché ora CP-SAT è trattabile (mentre il vecchio modello a segmenti per-operatore
era UNKNOWN): il modello a capacità elimina i due killer —
  1. niente operatori "con nome" → niente simmetria;
  2. la contesa risorse è un singolo `AddCumulative` per gruppo (capacità = count
     operazioni in parallelo), NON migliaia di `NoOverlap` a coppie.

Modello (precisione al minuto):
  * Ogni operazione → pochi segmenti opzionali (preemption su più giorni),
    `Σ size == durata_residua`, `NoOverlap` per-operazione (una risorsa alla volta).
  * Ogni gruppo (workcenter+skill) → `AddCumulative(segmenti+blocker, demand, count)`.
  * Calendario: intervalli "bloccanti" a piena capacità fuori orario → i segmenti
    restano nelle finestre lavorative.
  * Precedenze / componenti mancanti / IN_PROGRESS come nel resto del motore.
  * Obiettivo: minimizza makespan (e `makespan ≤ target` per FINISH_BY_DATE).

Pensato per girare DOPO il greedy (`capacity_scheduler.py`): il greedy fornisce un
orizzonte stretto e un warm-start (hint), così CP-SAT ottimizza invece di cercare
una prima soluzione da zero. Se va in timeout senza soluzione, l'orchestratore usa
il greedy come fallback.
"""
from __future__ import annotations

import logging
import math
import os
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from app.core.scheduler.capacity_scheduler import (
    CapacityEntry,
    CapacityResult,
    ResourceGroup,
)
from app.core.scheduler.cpsat_types import SchedulableOperation, _SKILL_CAN_DO

logger = logging.getLogger(__name__)

DAY_MINUTES = 1440


@dataclass
class _Seg:
    group: ResourceGroup
    present: cp_model.IntVar
    start: cp_model.IntVar
    size: cp_model.IntVar
    end: cp_model.IntVar
    interval: cp_model.IntervalVar


class CapacityCpsatScheduler:
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
        objective_mode: str = "MAXIMIZE_RESOURCE_UTILIZATION",
        target_finish_minutes: int | None = None,
        day_start_offset_minutes: int = 8 * 60,
        working_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4}),
        timeout_seconds: float = 30.0,
        max_segments_per_op_group: int = 6,
        warm_start: CapacityResult | None = None,
        bucket_minutes: int = 1,
    ) -> None:
        self.operations = operations
        self.groups = resource_groups
        self.horizon = max(1, int(horizon_minutes))
        self.epoch = epoch
        self.precedence_pairs = precedence_pairs or []
        self.rp_order_constraints = rp_order_constraints or []
        self.parent_wait_constraints = parent_wait_constraints or []
        self.missing_constraints = missing_constraints or {}
        # Priorità RP SOFT (rp_level×10000 + intra_routing_depth). Valore PIÙ BASSO
        # = priorità di dispatch PIÙ ALTA. Usata come obiettivo lessicografico
        # secondario (fase 2): a parità di obiettivo primario, anticipa le op a
        # priorità alta — senza mai bloccare il parallelismo (resta SOFT).
        self._op_priority: dict[uuid.UUID, int] = op_priority or {}
        self.objective_mode = objective_mode
        self.target_finish_minutes = target_finish_minutes
        self.day_start = day_start_offset_minutes
        self.working_weekdays = working_weekdays
        self.timeout = timeout_seconds
        self.max_k = max_segments_per_op_group
        self.warm_start = warm_start
        # Granularità temporale del modello CP-SAT: il tempo è discretizzato in
        # "bucket" da `bucket` minuti. bucket=1 → precisione al minuto (modello grande,
        # presolve lento, time-limit non rispettato sulle istanze grandi). bucket=10
        # → orizzonte e domini ~10× più piccoli: presolve rapido, time-limit rispettato,
        # CP-SAT riesce davvero a ottimizzare. Durate e inizi vengono arrotondati al
        # bucket (al rialzo le durate, così non si sotto-alloca mai lavoro).
        self.bucket = max(1, int(bucket_minutes))

        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()
        self._op_index = {op.id: op for op in operations}

    # ── helpers ──────────────────────────────────────────────────────────────

    def _residual(self, op: SchedulableOperation) -> int:
        return max(0, int(math.ceil(op.planned_duration_minutes * (1.0 - op.progress_pct / 100.0))))

    def _candidate_groups(self, op: SchedulableOperation) -> list[ResourceGroup]:
        return [
            g for g in self.groups
            if g.workcenter_id == op.workcenter_id
            and op.operation_type in _SKILL_CAN_DO.get(g.skill, set())
        ]

    def _weekday(self, day_index: int) -> int:
        return (self.epoch + timedelta(days=day_index)).weekday()

    def _capacity_blockers(self, g: ResourceGroup, cap: int) -> list[tuple[int, int, int]]:
        """(start, end, demand) per modellare la capacità VARIABILE per giorno con un
        `AddCumulative` a capacità costante `cap` = max risorse del gruppo:
          * fuori orario o giorni a 0 risorse → demand = cap (blocco pieno);
          * giorni con count < cap → "riduttore" demand = (cap − count) sulla finestra.
        Le ore/giorno variano per giorno (weekday_minutes)."""
        out: list[tuple[int, int, int]] = []
        H = self.horizon
        days = H // DAY_MINUTES + 2
        for d in range(days):
            day0 = d * DAY_MINUTES
            if day0 >= H:
                break
            day_end = min(day0 + DAY_MINUTES, H)
            wd = self._weekday(d)
            c = g.count_for(wd)
            mins = g.minutes_for(wd)
            if c <= 0 or mins <= 0:
                out.append((day0, day_end, cap))           # giorno intero non lavorato
                continue
            ws = day0 + self.day_start
            we = min(ws + mins, day_end)
            if ws > day0:
                out.append((day0, ws, cap))                # mattina prima della finestra
            if day_end > we:
                out.append((we, day_end, cap))             # sera dopo la finestra
            if c < cap and we > ws:
                out.append((ws, we, cap - c))              # riduttore di capacità
        return [(s, e, dem) for (s, e, dem) in out if e > s and dem > 0]

    def _preds(self) -> dict[uuid.UUID, set[uuid.UUID]]:
        ids = set(self._op_index)
        preds: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        # UNICO vincolo HARD: BOM ordine-livello.
        # precedence_pairs (rp_direct_pairs) NON è hard: RP ordering è SOFT priority
        # gestito via op_priority nell'obiettivo. Se op1 è bloccata si può lavorare op2.
        for ops_target, parent in self.parent_wait_constraints:
            if parent in ids:
                for t in (x for x in ops_target if x in ids):
                    preds[parent].add(t)
        return preds

    # ── build & solve ────────────────────────────────────────────────────────

    def solve(self) -> CapacityResult:
        model = self.model
        B = self.bucket
        H = math.ceil(self.horizon / B)   # orizzonte in BUCKET (non minuti)

        op_start: dict[uuid.UUID, cp_model.IntVar] = {}
        op_end: dict[uuid.UUID, cp_model.IntVar] = {}
        op_segs: dict[uuid.UUID, list[_Seg]] = {}
        group_intervals: dict[tuple, list[cp_model.IntervalVar]] = defaultdict(list)
        group_demands: dict[tuple, list[int]] = defaultdict(list)
        conflicts: list[str] = []

        # Quanti blocchi ha il greedy per (op, gruppo): serve a dimensionare i segmenti
        # in modo che il warm-start ENTRI SEMPRE (copertura 100%). Se allochiamo meno
        # segmenti dei blocchi greedy, quell'op resta senza hint e su modelli grandi
        # il fix-to-hint non riesce a chiudere → CP-SAT torna UNKNOWN.
        ws_blocks: dict[tuple, int] = defaultdict(int)
        if self.warm_start and self.warm_start.entries:
            for e in self.warm_start.entries:
                ws_blocks[(e.operation_id, e.workcenter_id, e.skill)] += 1

        for op in self.operations:
            D_min = self._residual(op)
            est_min = max(op.earliest_start_minutes, self.missing_constraints.get(op.id, 0))
            if D_min <= 0:
                continue
            # Conversione in BUCKET: durata al rialzo (mai sotto-allocare), inizio minimo
            # al rialzo (non partire prima del consentito).
            D = max(1, math.ceil(D_min / B))
            est = math.ceil(est_min / B)
            groups = self._candidate_groups(op)
            if not groups:
                conflicts.append(
                    f"Op {op.id} ({op.operation_type.value}): nessun gruppo risorse compatibile"
                )
                continue

            segs: list[_Seg] = []
            eff_starts: list[cp_model.IntVar] = []
            eff_ends: list[cp_model.IntVar] = []
            for g in groups:
                gkey = (g.workcenter_id, g.skill)
                day_len = max(1, math.ceil(g.max_minutes / B))  # finestra giornaliera (BUCKET)
                seg_cap = min(day_len, D)                 # un segmento ≤ finestra di una risorsa
                # Numero MINIMO di segmenti per allocare interamente D su questo gruppo:
                # ogni segmento porta al più `seg_cap` minuti, quindi servono almeno
                # ceil(D / seg_cap) segmenti. Scendere sotto questa soglia renderebbe
                # `Σ size == D` insoddisfacibile → INFEASIBLE spurio sulle op lunghe
                # (es. D=6000, finestra/gg=480 → servono 13 segmenti: con un cap rigido
                # a 6 il modello non riuscirebbe mai ad allocare il lavoro residuo).
                # `max_segments_per_op_group` resta come SOGLIA DI ALLERTA, non come
                # tetto rigido: la fattibilità ha sempre la precedenza.
                k_need = max(1, math.ceil(D / seg_cap))
                k = k_need + 1                            # +1 segmento di slack per l'hand-off
                # Garantisce abbastanza segmenti per ospitare i blocchi del greedy su
                # questo gruppo (warm-start sempre completo → fix-to-hint affidabile).
                ws_n = ws_blocks.get((op.id, g.workcenter_id, g.skill), 0)
                if ws_n + 1 > k:
                    k = ws_n + 1
                if k > self.max_k:
                    logger.warning(
                        "Op %s: servono %d segmenti sul gruppo %s (D=%d min, finestra/gg=%d) "
                        "oltre max_segments_per_op_group=%d → uso %d per garantire la fattibilità",
                        op.id, k, g.skill.value, D, day_len, self.max_k, k,
                    )
                for i in range(k):
                    tag = f"{op.id}_{g.skill.value}_{i}"
                    pres = model.NewBoolVar(f"p_{tag}")
                    size = model.NewIntVar(0, seg_cap, f"sz_{tag}")
                    start = model.NewIntVar(est, H, f"s_{tag}")
                    end = model.NewIntVar(est, H, f"e_{tag}")
                    iv = model.NewOptionalIntervalVar(start, size, end, pres, f"iv_{tag}")
                    model.Add(size == 0).OnlyEnforceIf(pres.Not())
                    model.Add(size >= 1).OnlyEnforceIf(pres)
                    eff_s = model.NewIntVar(0, H, f"es_{tag}")
                    model.Add(eff_s == start).OnlyEnforceIf(pres)
                    model.Add(eff_s == H).OnlyEnforceIf(pres.Not())
                    eff_e = model.NewIntVar(0, H, f"ee_{tag}")
                    model.Add(eff_e == end).OnlyEnforceIf(pres)
                    model.Add(eff_e == 0).OnlyEnforceIf(pres.Not())
                    eff_starts.append(eff_s)
                    eff_ends.append(eff_e)
                    sv = _Seg(g, pres, start, size, end, iv)
                    segs.append(sv)
                    group_intervals[gkey].append(iv)
                    group_demands[gkey].append(1)

            model.Add(sum(s.size for s in segs) == D)            # tutto il lavoro allocato
            if len(segs) >= 2:
                model.AddNoOverlap([s.interval for s in segs])   # una risorsa alla volta

            os_v = model.NewIntVar(0, H, f"ostart_{op.id}")
            oe_v = model.NewIntVar(0, H, f"oend_{op.id}")
            model.AddMinEquality(os_v, eff_starts)
            model.AddMaxEquality(oe_v, eff_ends)
            op_start[op.id] = os_v
            op_end[op.id] = oe_v
            op_segs[op.id] = segs

        # ── Cumulative per gruppo (+ blocker calendario) ──────────────────────
        for g in self.groups:
            gkey = (g.workcenter_id, g.skill)
            ivs = list(group_intervals.get(gkey, []))
            dems = list(group_demands.get(gkey, []))
            if not ivs:
                continue
            cap = max(1, g.max_count)
            for (b0, b1, dem) in self._capacity_blockers(g, cap):
                # Converte l'intervallo bloccante da minuti a BUCKET: inizio al ribasso,
                # fine al rialzo → il blocco non-lavorativo non si restringe mai (il
                # lavoro resta dentro le finestre anche dopo l'arrotondamento).
                bb0 = b0 // B
                bb1 = math.ceil(b1 / B)
                if bb1 <= bb0:
                    continue
                blk = model.NewIntervalVar(bb0, bb1 - bb0, bb1, f"blk_{g.skill.value}_{bb0}")
                ivs.append(blk)
                dems.append(dem)
            model.AddCumulative(ivs, dems, cap)

        # ── Precedenze ────────────────────────────────────────────────────────
        preds = self._preds()
        for succ, pset in preds.items():
            if succ not in op_start:
                continue
            for p in pset:
                if p in op_end:
                    model.Add(op_start[succ] >= op_end[p])

        # ── Raccoglie 'present' per gruppo (usato dagli obiettivi multi-criterio) ──
        group_pres: dict[tuple, list] = defaultdict(list)
        for segs_list in op_segs.values():
            for s in segs_list:
                group_pres[(s.group.workcenter_id, s.group.skill)].append(s.present)

        # ── Obiettivo PRIMARIO ────────────────────────────────────────────────
        # Costruiamo l'espressione obiettivo primaria (makespan / gruppi usati a
        # seconda del modo) ma la teniamo in `primary_expr` invece di chiamare
        # Minimize "al volo": serve per la fase 2 (priorità RP soft), dove
        # vincoliamo il primario al suo valore ottimo e poi ottimizziamo l'ordine RP.
        primary_expr = None
        if op_end:
            makespan = model.NewIntVar(0, H, "makespan")
            model.AddMaxEquality(makespan, list(op_end.values()))

            def _used_vars() -> list:
                out = []
                for gkey, pres_list in group_pres.items():
                    if not pres_list:
                        continue
                    uid = f"{str(gkey[0])[:8]}_{gkey[1].value}"
                    used_g = model.NewBoolVar(f"used_g_{uid}")
                    model.AddBoolOr(pres_list).OnlyEnforceIf(used_g)
                    model.AddBoolAnd([p.Not() for p in pres_list]).OnlyEnforceIf(used_g.Not())
                    out.append(used_g)
                return out

            if self.objective_mode == "FINISH_BY_DATE":
                # Vincolo hard: finisci entro la data target. Poi minimizza makespan.
                # target in BUCKET, al ribasso → non si concede tempo oltre la deadline.
                if self.target_finish_minutes:
                    target_b = int(self.target_finish_minutes) // B
                    model.Add(makespan <= target_b)
                primary_expr = makespan

            elif self.objective_mode == "MINIMIZE_OPERATORS":
                # Primario: minimizza il numero di gruppi-risorsa distinti usati
                # (concentra lavoro su meno risorse → turni ridotti, crew minima).
                # Secondario interno: minimizza makespan (a parità di gruppi).
                # Scala: 1 gruppo risparmiato > qualsiasi riduzione di makespan.
                used_vars = _used_vars()
                primary_expr = (sum(used_vars) * (H + 1) + makespan) if used_vars else makespan

            elif self.objective_mode == "MAXIMIZE_RESOURCE_UTILIZATION":
                # Primario: minimizza makespan (stessa durata → più throughput).
                # Secondario interno: massimizza i gruppi-risorsa usati (distribuisce
                # il lavoro → nessuna risorsa idle mentre un'altra è satura).
                # Scala: 1 minuto di makespan > 1 gruppo aggiuntivo usato.
                used_vars = _used_vars()
                n_g = max(len(used_vars), 1)
                primary_expr = (makespan * (n_g + 1) - sum(used_vars)) if used_vars else makespan

            else:
                # CUSTOM o qualsiasi valore non riconosciuto → makespan puro
                primary_expr = makespan

            model.Minimize(primary_expr)

        self._apply_warm_start(op_segs)

        # ── Fase 1: ottimizza l'obiettivo primario ────────────────────────────
        # La priorità RP (op_priority) è SOFT: la applichiamo solo come obiettivo
        # lessicografico secondario (fase 2), così non sacrifica MAI makespan/gruppi.
        # Decidiamo se vale la pena la fase 2: serve un primario, almeno 2 op e
        # almeno 2 livelli di priorità distinti (altrimenti non c'è nulla da ordinare).
        distinct_prios = {self._op_priority.get(oid, 0) for oid in op_start}
        do_rp_phase = (
            primary_expr is not None
            and len(op_start) >= 2
            and len(distinct_prios) >= 2
        )
        rp_frac = float(os.getenv("CPSAT_CAPACITY_RP_PHASE_FRAC", "0.35"))
        rp_frac = min(max(rp_frac, 0.0), 0.9)

        self.solver.parameters.num_search_workers = 8
        # Con un warm-start COMPLETO (vedi _apply_warm_start) chiediamo a CP-SAT di
        # ripararlo/usarlo come incumbent iniziale: garantisce almeno una soluzione
        # FEASIBLE (= il greedy) anche se non riesce a migliorare entro il timeout,
        # invece di restituire UNKNOWN su modelli grandi (orizzonti lunghi).
        self.solver.parameters.repair_hint = True
        phase1_timeout = self.timeout * (1.0 - rp_frac) if do_rp_phase else self.timeout
        n_segments = sum(len(s) for s in op_segs.values())
        logger.info(
            "CP-SAT capacità: %d op, %d gruppi, %d segmenti, horizon=%d min, timeout=%gs (rp_phase=%s)",
            len(op_segs), len(self.groups), n_segments, H, self.timeout, do_rp_phase,
        )

        def _status_str(st: int) -> str:
            return {
                cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
                cp_model.INFEASIBLE: "INFEASIBLE", cp_model.UNKNOWN: "UNKNOWN",
            }.get(st, "UNKNOWN")

        def _fix_to_hint_solve(budget: float) -> int:
            """Forza le variabili al valore del warm-start: cattura SEMPRE l'incumbent
            del greedy in una frazione di secondo, anche su modelli dove la ricerca
            libera non riesce a registrarne uno (e tornerebbe UNKNOWN)."""
            self.solver.parameters.fix_variables_to_their_hinted_value = True
            self.solver.parameters.max_time_in_seconds = max(1.0, budget)
            st = self.solver.Solve(self.model)
            self.solver.parameters.fix_variables_to_their_hinted_value = False
            return st

        t_start = time.monotonic()

        # ── Incumbent GARANTITO dal warm-start (fix-to-hint) ──────────────────
        # Su istanze grandi (orizzonti lunghi, molti segmenti) la ricerca libera può
        # non trovare nemmeno una soluzione entro il timeout pur esistendone una (il
        # greedy). La catturiamo prima forzando le variabili al valore suggerito:
        # così CP-SAT non torna MAI UNKNOWN quando il greedy è fattibile, e la fase 1
        # parte avendo già un piano valido da migliorare.
        has_hint = bool(self.warm_start and self.warm_start.entries)
        baseline: tuple[list[CapacityEntry], dict] | None = None
        if has_hint:
            st0 = _fix_to_hint_solve(min(10.0, max(2.0, self.timeout * 0.2)))
            if st0 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                baseline = self._extract(op_segs)
                logger.info(
                    "CP-SAT capacità: incumbent warm-start (fix-to-hint) catturato in %.2fs",
                    self.solver.WallTime(),
                )

        # ── Fase 1: ottimizzazione libera (può migliorare l'incumbent) ────────
        elapsed = time.monotonic() - t_start
        self.solver.parameters.max_time_in_seconds = max(1.0, phase1_timeout - elapsed)
        status = self.solver.Solve(self.model)
        logger.info("CP-SAT capacità fase 1 in %.2fs: status=%s",
                    self.solver.WallTime(), _status_str(status))

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            entries, completion = self._extract(op_segs)
            final_status = status
            solver_live = True
        elif baseline is not None:
            # La ricerca libera non ha registrato soluzioni: ripristina nel solver
            # l'incumbent garantito del greedy (serve anche alla fase 2).
            _fix_to_hint_solve(min(8.0, max(2.0, self.timeout * 0.2)))
            entries, completion = baseline
            final_status = cp_model.FEASIBLE
            solver_live = True
            logger.info("CP-SAT capacità: uso l'incumbent del warm-start (fase 1 senza soluzione libera)")
        else:
            return CapacityResult(status=_status_str(status), entries=[],
                                  makespan_minutes=None, conflicts=conflicts)

        # ── Fase 2: priorità RP soft (lessicografica) ─────────────────────────
        if do_rp_phase and solver_live:
            remaining = self.timeout - (time.monotonic() - t_start)
            if remaining >= 1.0:
                status2 = self._run_rp_phase(
                    model, op_start, primary_expr, op_segs, remaining,
                )
                # La fase 2 vincola il primario al suo ottimo, quindi qualunque
                # soluzione trovata è valida e non peggiora makespan/gruppi.
                if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    entries, completion = self._extract(op_segs)
                    final_status = status2

        makespan_min = max(completion.values(), default=0) if entries else None
        return CapacityResult(
            status="OPTIMAL" if final_status == cp_model.OPTIMAL else "FEASIBLE",
            entries=entries, makespan_minutes=makespan_min, conflicts=conflicts,
            completion=completion,
        )

    def _extract(
        self, op_segs: dict[uuid.UUID, list[_Seg]]
    ) -> tuple[list[CapacityEntry], dict[uuid.UUID, int]]:
        """Estrae entries + completion dallo stato corrente del solver."""
        entries: list[CapacityEntry] = []
        completion: dict[uuid.UUID, int] = {}
        for op_id, segs in op_segs.items():
            last = 0
            for s in segs:
                if self.solver.Value(s.present) == 1 and self.solver.Value(s.size) > 0:
                    # Riconverte da BUCKET a minuti.
                    st = self.solver.Value(s.start) * self.bucket
                    en = self.solver.Value(s.end) * self.bucket
                    entries.append(CapacityEntry(
                        operation_id=op_id, workcenter_id=s.group.workcenter_id,
                        resource_type_id=s.group.resource_type_id, skill=s.group.skill,
                        lane_index=0, start_minutes=st, end_minutes=en,
                    ))
                    last = max(last, en)
            completion[op_id] = last
        return entries, completion

    def _run_rp_phase(
        self,
        model: cp_model.CpModel,
        op_start: dict[uuid.UUID, cp_model.IntVar],
        primary_expr,
        op_segs: dict[uuid.UUID, list[_Seg]],
        budget_seconds: float,
    ) -> int:
        """Fase 2 — priorità RP SOFT come obiettivo lessicografico secondario.

        1. Vincola l'obiettivo primario al valore ottimo trovato in fase 1
           (`primary_expr <= V*`): qualunque soluzione di questa fase ha makespan
           e gruppi-risorsa NON peggiori → la priorità RP non sacrifica mai il
           primario (resta una preferenza, non un vincolo bloccante).
        2. Minimizza la somma pesata degli start: le op a `op_priority` più BASSO
           (priorità di dispatch più ALTA) ricevono peso maggiore → vengono
           anticipate. Surrogato convesso standard del "prima le op prioritarie".
        3. Riparte dalla soluzione di fase 1 come hint.

        Ritorna lo status CP-SAT della Solve di questa fase.
        """
        primary_val = int(round(self.solver.Value(primary_expr)))
        model.Add(primary_expr <= primary_val)

        # Rank denso delle priorità: rank 0 = op_priority minimo = priorità più alta.
        ranks = sorted({self._op_priority.get(oid, 0) for oid in op_start})
        rank_of = {p: i for i, p in enumerate(ranks)}
        r_max = len(ranks) - 1
        rp_terms = []
        for oid, sv in op_start.items():
            w = r_max - rank_of[self._op_priority.get(oid, 0)]  # priorità alta → peso alto
            if w > 0:
                rp_terms.append(w * sv)
        if not rp_terms:
            return cp_model.UNKNOWN

        # Riparti dalla soluzione di fase 1 (hint su tutti i segmenti).
        try:
            model.ClearHints()
        except AttributeError:
            pass  # versioni vecchie di OR-Tools: gli hint verranno semplicemente sovrascritti
        for segs in op_segs.values():
            for s in segs:
                try:
                    model.AddHint(s.present, self.solver.Value(s.present))
                    model.AddHint(s.start, self.solver.Value(s.start))
                    model.AddHint(s.size, self.solver.Value(s.size))
                except Exception:
                    pass

        model.Minimize(sum(rp_terms))
        self.solver.parameters.max_time_in_seconds = max(1.0, budget_seconds)
        status2 = self.solver.Solve(model)
        logger.info(
            "CP-SAT capacità fase 2 (RP soft) in %.2fs: status=%s, primary<=%d",
            self.solver.WallTime(), status2, primary_val,
        )
        return status2

    def _apply_warm_start(self, op_segs: dict[uuid.UUID, list[_Seg]]) -> None:
        """Hint dal greedy: ricostruisce un warm-start COMPLETO e COERENTE.

        Per dare a CP-SAT un incumbent fattibile (e non solo un suggerimento parziale)
        i blocchi del greedy vanno mappati sui segmenti del **gruppo giusto**, non
        accoppiati per posizione. Inoltre i segmenti NON usati ricevono `present=0`,
        così l'hint descrive un'assegnazione completa: CP-SAT parte da una soluzione
        valida e può solo migliorarla (niente più `UNKNOWN` per hint incoerente).
        """
        if not self.warm_start or not self.warm_start.entries:
            return
        # Blocchi greedy indicizzati per (op, gruppo = workcenter+skill).
        by_op_group: dict[tuple, list] = defaultdict(list)
        for e in self.warm_start.entries:
            by_op_group[(e.operation_id, e.workcenter_id, e.skill)].append(e)

        applied = 0
        hinted_ops = 0
        for op_id, segs in op_segs.items():
            # Segmenti di quest'op raggruppati per gruppo candidato.
            segs_by_group: dict[tuple, list[_Seg]] = defaultdict(list)
            for s in segs:
                segs_by_group[(s.group.workcenter_id, s.group.skill)].append(s)

            # Verifica che i blocchi del greedy entrino nei segmenti disponibili per
            # ciascun gruppo: se anche un solo gruppo ha più blocchi che segmenti,
            # l'hint per quest'op sarebbe incoerente → meglio non suggerire nulla
            # (CP-SAT completerà da solo: un'op senza hint è ok, una con hint
            # contraddittorio rischia di far scartare l'INTERO warm-start).
            ok = True
            for gkey, group_segs in segs_by_group.items():
                if len(by_op_group.get((op_id, gkey[0], gkey[1]), [])) > len(group_segs):
                    ok = False
                    break
            if not ok:
                continue

            B = self.bucket
            # Raccoglie (seg, start_bucket, size_bucket) nell'ordine temporale dei
            # blocchi greedy (lo start floor, la size arrotondata).
            chosen: list[list] = []
            for gkey, group_segs in segs_by_group.items():
                blocks = sorted(
                    by_op_group.get((op_id, gkey[0], gkey[1]), []),
                    key=lambda b: b.start_minutes,
                )
                for blk, seg in zip(blocks, group_segs):
                    start_b = blk.start_minutes // B
                    size_b = max(1, round((blk.end_minutes - blk.start_minutes) / B))
                    chosen.append([seg, blk.start_minutes, start_b, size_b])
            chosen.sort(key=lambda c: c[1])       # ordine temporale assoluto (cross-gruppo)

            # Durata target in bucket (== quella usata dal modello): l'hint deve avere
            # Σ size == D_b, altrimenti CP-SAT lo scarterebbe come incoerente.
            D_b = max(1, math.ceil(self._residual(self._op_index[op_id]) / B))
            # Se i blocchi sono più dei bucket di durata (op cortissime molto frammentate),
            # tieni solo i primi D_b come presenti: ognuno avrà size ≥ 1.
            if len(chosen) > D_b:
                chosen = chosen[:D_b]
            # Correggi la somma delle size a D_b (gli arrotondamenti la spostano).
            diff = D_b - sum(c[3] for c in chosen)
            i = 0
            while diff > 0:                       # distribuisci i bucket mancanti
                chosen[i % len(chosen)][3] += 1
                diff -= 1
                i += 1
            while diff < 0:                       # togli dai segmenti più grandi (≥1)
                j = max(range(len(chosen)), key=lambda x: chosen[x][3])
                if chosen[j][3] <= 1:
                    break
                chosen[j][3] -= 1
                diff += 1

            # Layout SEQUENZIALE in bucket: lo start di ogni segmento non può precedere
            # la fine del precedente (l'arrotondamento al bucket potrebbe altrimenti
            # far sovrapporre due blocchi della stessa op → hint infeasible, fix-to-hint
            # fallirebbe). Mantiene l'ordine temporale del greedy.
            prev_end = 0
            for c in chosen:
                if c[2] < prev_end:
                    c[2] = prev_end
                prev_end = c[2] + c[3]

            used: set[int] = set()
            for seg, _start_min, start_b, size_b in chosen:
                self.model.AddHint(seg.present, 1)
                self.model.AddHint(seg.start, start_b)
                self.model.AddHint(seg.size, size_b)
                used.add(id(seg))
                applied += 1
            # Segmenti non usati di quest'op → present=0 (hint completo).
            for s in segs:
                if id(s) not in used:
                    self.model.AddHint(s.present, 0)
                    self.model.AddHint(s.size, 0)
            hinted_ops += 1

        if applied:
            logger.info(
                "CP-SAT capacità: warm-start COMPLETO su %d/%d op (%d segmenti attivi)",
                hinted_ops, len(op_segs), applied,
            )
