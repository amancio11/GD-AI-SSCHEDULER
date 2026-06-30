from __future__ import annotations

"""Reschedule Engine — Celery tasks that orchestrate the full rescheduling pipeline.

IMPORTANT: Celery workers do NOT support Python asyncio natively.
All DB access here uses a *synchronous* SQLAlchemy engine (psycopg2 / pg8000).
The async engine defined in app.db.session is used only by FastAPI request handlers.

PATCH reschedule_engine.py
 
Sostituisce lo stub:
    # ── Step 4d: Precedence pairs ─────────────────────────────────────────────
    precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    # (Operation-level pairs are derived from reference-point precedences — stub)
 
Con l'implementazione reale che:
1. Carica il DAG dei RP per il machine_model corrente
2. Risolve ogni RP → production_order (via target_order_material)
3. Per ogni arco RP_pred → RP_succ nel DAG:
   - Raccoglie TUTTE le op_id dell'ordine puntato da RP_pred (ricorsivo sui figli)
   - Raccoglie TUTTE le op_id dell'ordine puntato da RP_succ (ricorsivo)
   - Aggiunge vincoli CP-SAT: per ogni (op_pred, op_succ): op_end[pred] <= op_start[succ]
     tramite una variabile ausiliaria completion_X per efficienza
 
NOTA: non usa blocking_constraints (dict statico), ma precedence_pairs esteso
con la logica "tutti gli end di A <= tutti gli start di B".
Questo funziona correttamente sia per il primo run (nessuna entry preesistente)
sia per i run incrementali (alcune op COMPLETED con end fisso).
"""

from collections import defaultdict
import logging
import os
import uuid
from datetime import date, datetime, timezone

from celery import shared_task
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session, sessionmaker

from celery_worker import celery_app

load_dotenv()

logger = logging.getLogger(__name__)

# ── Synchronous DB engine for Celery workers ─────────────────────────────────
_SYNC_DB_URL: str = (
    os.environ.get("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    .replace("postgresql://", "postgresql+psycopg2://")
    or "postgresql+psycopg2://scheduler:scheduler@localhost:5432/scheduler"
)

_sync_engine = create_engine(_SYNC_DB_URL, pool_pre_ping=True)
_SyncSession = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)


def _get_sync_session() -> Session:
    return _SyncSession()

# ════════════════════════════════════════════════════════════════════════
# HELPER — raccolta ricorsiva di op_id da un ordine e tutti i suoi figli
# ════════════════════════════════════════════════════════════════════════
 
def _collect_ops_recursive(
    order_id: uuid.UUID,
    children_map: dict[uuid.UUID, list[uuid.UUID]],  # parent_id → [child_id, ...]
    ops_by_order: dict[uuid.UUID, list[uuid.UUID]],   # order_id → [op_id, ...]
    schedulable_op_ids: set[uuid.UUID],                # solo op schedulabili (non COMPLETED)
) -> list[uuid.UUID]:
    """Return all schedulable op_ids belonging to order_id and all its descendants.
 
    Uses iterative DFS to avoid Python recursion limits on deep BOMs.
    Stops descending into COMPONENT nodes (they have no ops anyway).
    """
    result: list[uuid.UUID] = []
    stack = [order_id]
    visited: set[uuid.UUID] = set()
 
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
 
        # Add ops of this order that are still schedulable
        for op_id in ops_by_order.get(current, []):
            if op_id in schedulable_op_ids:
                result.append(op_id)
 
        # Recurse into children
        for child_id in children_map.get(current, []):
            if child_id not in visited:
                stack.append(child_id)
 
    return result

# ── Celery task stubs for AI (now delegates to proactive_analyzer) ───────────

@celery_app.task(name="app.core.scheduler.reschedule_engine.analyze_proactive")
def analyze_proactive(scenario_id: str) -> None:
    """Delegate to the real proactive analyzer (Step 18 implementation)."""
    from app.core.ai.proactive_analyzer import analyze_proactive_after_schedule  # noqa: PLC0415
    analyze_proactive_after_schedule.delay(scenario_id)


# ── Main rescheduling task ────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.core.scheduler.reschedule_engine.reschedule_incremental",
    max_retries=1,           # ← al massimo 1 retry
    default_retry_delay=5,
)
def reschedule_incremental(self, scenario_id: str, triggered_by: str = "manual") -> dict:
    """Incrementally reschedule non-COMPLETED operations for *scenario_id*.

    Idempotent: calling this twice for the same scenario produces the same
    result because STALE entries are cleaned up at the end.

    Steps
    -----
    1.  Load scenario and all non-COMPLETED operations (sync DB session).
    2.  Mark all existing schedule_entries for this scenario as STALE.
    3.  Identify IN_PROGRESS operations → use their end time as a lower bound.
    4.  Load missing components, reference-point precedences, operators.
    5.  Pre-process operator shifts (shift_preprocessor).
    6.  Build reference-point precedence DAG (dag_builder — sync wrapper).
    7.  Solve CP-SAT (cpsat_model_builder).
    8.  Persist new schedule_entries.
    9.  Delete STALE entries.
    10. Broadcast RESCHEDULE_COMPLETE via WebSocket (best-effort).
    11. Trigger proactive AI analysis task.
    """
    logger.info(
        "reschedule_incremental START scenario=%s triggered_by=%s",
        scenario_id, triggered_by,
    )

    session = _get_sync_session()
    try:
        result = _run_reschedule(session, uuid.UUID(scenario_id), triggered_by)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("reschedule_incremental FAILED scenario=%s", scenario_id)
        # Retry solo per errori di connessione, non per bug logici
        if "connection" in str(exc).lower() or "timeout" in str(exc).lower():
            raise self.retry(exc=exc)
        raise exc   # ← bug logici falliscono subito, senza loop
    finally:
        session.close()

    # Step 10 — Broadcast (best-effort, non-blocking via asyncio.run in thread)
    _broadcast_complete(scenario_id, result)

    # Step 11 — Trigger proactive AI analysis
    analyze_proactive.delay(scenario_id)

    logger.info("reschedule_incremental DONE scenario=%s result=%s", scenario_id, result)
    return result


def _run_reschedule(session: Session, scenario_id: uuid.UUID, triggered_by: str) -> dict:
    """Core rescheduling logic (synchronous).

    Returns a summary dict with status and makespan info.
    """
    # Import here to avoid circular imports at module level
    from app.enums import OperationStatus, ScheduleEntryStatus
    from app.models.missing import MissingComponent
    from app.models.operator import Operator, OperatorCalendar
    from app.models.production import ProductionOrder
    from app.models.reference import ReferencePoint, ReferencePointPrecedence
    from app.models.routing import Operation, Routing
    from app.models.schedule import ScheduleEntry, ScheduleScenario
    from app.models.machine import MachineModel, MachineOrder

    # ── Step 1: Load scenario ─────────────────────────────────────────────────
    scenario: ScheduleScenario | None = session.get(ScheduleScenario, scenario_id)
    if scenario is None:
        raise ValueError(f"Scenario {scenario_id} not found")

    machine_order_id = scenario.machine_order_id

    # ── Step 2: Mark existing entries STALE ───────────────────────────────────
    session.execute(
        update(ScheduleEntry)
        .where(ScheduleEntry.scenario_id == scenario_id)
        .values(status=ScheduleEntryStatus.STALE)
    )

    # ── Step 3: Identify IN_PROGRESS operations (anchor their earliest start) ──
    # Before marking STALE, collect entries whose actual_start is set but not
    # actual_end — these were running when the reschedule was triggered.
    # We pin their earliest_start to "now" so the solver does not schedule them
    # in the past. Their residual duration is already encoded in progress_pct.
    in_progress_op_ids: set[uuid.UUID] = set()
    in_progress_entries = (
        session.query(ScheduleEntry)
        .filter(
            ScheduleEntry.scenario_id == scenario_id,
            ScheduleEntry.status == ScheduleEntryStatus.STALE,
            ScheduleEntry.actual_start.isnot(None),
            ScheduleEntry.actual_end.is_(None),
        )
        .all()
    )
    for entry in in_progress_entries:
        in_progress_op_ids.add(entry.operation_id)

    # ── Step 4: Load schedulable operations ──────────────────────────────────
    ops_rows = (
        session.query(Operation, Routing, ProductionOrder)
        .join(Routing, Operation.routing_id == Routing.id)
        .join(ProductionOrder, Routing.production_order_id == ProductionOrder.id)
        .filter(
            ProductionOrder.machine_order_id == machine_order_id,
            Operation.status.notin_([OperationStatus.COMPLETED]),
        )
        .all()
    )

    from app.core.scheduler.cpsat_types import QualifiedOperator, SchedulableOperation
    from app.core.scheduler.shift_preprocessor import compute_epoch, datetime_to_minutes
    from app.enums import OperationType

    # Epoch = data di partenza dello scenario (o oggi come fallback).
    # Tutte le variabili CP-SAT sono minuti relativi a questo istante.
    schedule_start_date: date = (
        date.fromisoformat(str(scenario.start_date))
        if scenario.start_date
        else date.today()
    )
    today = schedule_start_date
    epoch = compute_epoch(schedule_start_date)

    # "Ora" in minuti CP-SAT — usata per ancorare le operazioni IN_PROGRESS.
    now_minutes = datetime_to_minutes(datetime.now(timezone.utc), epoch)
    # Non può essere negativo (se start_date è nel futuro, "ora" non esiste ancora).
    now_minutes = max(now_minutes, 0)

    schedulable_ops: list[SchedulableOperation] = []
    for op, routing, po in ops_rows:
        # Priorità: workcenter sull'operazione → workcenter sull'ordine → skip
        wc_id = op.workcenter_id or po.workcenter_id
        if wc_id is None:
            logger.warning("Operazione %s senza workcenter_id — saltata", op.id)
            continue
        # Le operazioni IN_PROGRESS non possono essere riposizionate nel passato:
        # il loro earliest_start è "ora" (o 0 se lo scenario è futuro).
        earliest = now_minutes if op.id in in_progress_op_ids else 0
        schedulable_ops.append(
            SchedulableOperation(
                id=op.id,
                routing_id=routing.id,
                production_order_id=po.id,
                operation_type=OperationType(op.operation_type.value),
                workcenter_id=wc_id,
                planned_duration_minutes=op.planned_duration_minutes,
                progress_pct=op.progress_pct,
                can_be_interrupted=op.can_be_interrupted,
                earliest_start_minutes=earliest,
                reference_point_id=op.reference_point_id,
            )
        )

    if not schedulable_ops:
        logger.info("No schedulable operations for scenario %s — nothing to do", scenario_id)
        _cleanup_stale(session, scenario_id)
        return {"status": "SKIPPED", "reason": "no_schedulable_ops"}

    logger.info(
        "Scenario %s: start_date=%s, epoch=%s, %d IN_PROGRESS ops ancorati a now=%d min",
        scenario_id, schedule_start_date, epoch, len(in_progress_op_ids), now_minutes,
    )

    # ── Step 4b: NO vincolo intra-routing da sequence_number ─────────────────
    # L'ordinamento delle operazioni deriva ESCLUSIVAMENTE dai Reference Point (RP DAG).
    # Non esiste precedenza legata allo stepId/sequence_number delle operazioni.
    # Le coppie hard (rp_direct_pairs) vengono costruite in Step 4d.
    _sched_ids = {op.id for op in schedulable_ops}
    # precedence_pairs sarà valorizzato da rp_direct_pairs in Step 4d
    precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []

    missing_rows = (
        session.query(MissingComponent)
        .filter(
            MissingComponent.is_arrived.is_(False),
            MissingComponent.production_order_id.in_(
                [op.production_order_id for op in schedulable_ops]
            ),
        )
        .all()
    )

    missing_constraints: dict[uuid.UUID, int] = {}
    for mc in missing_rows:
        if mc.expected_arrival_date:
            arrival_dt = datetime(
                mc.expected_arrival_date.year,
                mc.expected_arrival_date.month,
                mc.expected_arrival_date.day,
                tzinfo=timezone.utc,
            )
            arrival_min = datetime_to_minutes(arrival_dt, epoch)
            # Apply to all ops of that production order
            for op_sc in schedulable_ops:
                if op_sc.production_order_id == mc.production_order_id:
                    missing_constraints[op_sc.id] = max(
                        missing_constraints.get(op_sc.id, 0), arrival_min
                    )

    # ── Step 4c: Gruppi risorse a capacità (workcenter + skill) ─────────────────
    # Niente più operatori con nome né slot di calendario: lo scheduler usa SOLO i
    # ResourceType configurati. count risorse × ore/giorno = capacità del gruppo.
    from app.core.scheduler.capacity_scheduler import ResourceGroup
    from app.enums import SkillType
    from app.models.resource import ResourceType

    def _weekday_maps(rt: ResourceType) -> tuple[dict[int, int], dict[int, int]]:
        """Costruisce (weekday_count, weekday_minutes) dal weekday_schedule del tipo
        risorsa; se assente, default: lun–ven = (count, ore), sab/dom = 0."""
        wc: dict[int, int] = {}
        wm: dict[int, int] = {}
        sched = rt.weekday_schedule or {}
        base_min = int(round((rt.daily_capacity_hours or 0) * 60))
        for wd in range(7):
            entry = sched.get(str(wd))
            if entry is not None:
                wc[wd] = max(0, int(entry.get("count", 0) or 0))
                wm[wd] = max(0, int(round(float(entry.get("hours", 0) or 0) * 60)))
            elif wd < 5:
                wc[wd] = max(0, rt.count)
                wm[wd] = base_min
            else:
                wc[wd] = 0
                wm[wd] = 0
        return wc, wm

    resource_type_rows = (
        session.query(ResourceType).filter(ResourceType.is_active.is_(True)).all()
    )
    resource_groups: list[ResourceGroup] = []
    for rt in resource_type_rows:
        wc_map, wm_map = _weekday_maps(rt)
        if not any(wc_map[wd] > 0 and wm_map[wd] > 0 for wd in range(7)):
            continue  # nessuna capacità in nessun giorno
        resource_groups.append(
            ResourceGroup(
                workcenter_id=rt.workcenter_id,
                skill=SkillType(rt.skill.value),
                resource_type_id=rt.id,
                weekday_count=wc_map,
                weekday_minutes=wm_map,
            )
        )

    group_daily_capacity = sum(g.weekly_capacity_minutes for g in resource_groups)
    logger.info(
        "Resource groups loaded: %d gruppi, capacità totale %d min/settimana",
        len(resource_groups), group_daily_capacity,
    )

    # ── DIAGNOSI WORKCENTER ───────────────────────────────────────────────────
    from collections import Counter
    wc_counts = Counter(str(op.workcenter_id) for op in schedulable_ops)
    for wc_str, count in wc_counts.items():
        groups_in_wc = [g for g in resource_groups if str(g.workcenter_id) == wc_str]
        cap = sum(g.weekly_capacity_minutes for g in groups_in_wc)
        logger.info(
            "WC %s: %d ops, %d gruppi risorse (%d min/giorno)",
            wc_str, count, len(groups_in_wc), cap,
        )

    # ── Step 4d: Precedence constraints dal DAG dei Reference Point ───────────
    #
    # Logica:
    #   Per ogni arco RP_pred → RP_succ nel DAG:
    #     - RP_pred.target_order_material → ordine A (+ tutti i suoi discendenti)
    #     - RP_succ.target_order_material → ordine B (+ tutti i suoi discendenti)
    #     - Tutte le op di A devono finire PRIMA che qualsiasi op di B inizi.
    #
    # Implementazione CP-SAT efficiente:
    #   Invece di O(|A|×|B|) coppie, usiamo una variabile ausiliaria per gruppo:
    #     completion_A = max(op_end[a] for a in ops_A)
    #     for b in ops_B: model.Add(op_start[b] >= completion_A)
    #   Questo viene fatto nel CpsatModelBuilder tramite rp_order_constraints.
    #
    # Qui nel reschedule_engine costruiamo il dict:
    #   rp_order_constraints: list[tuple[list[op_id], list[op_id]]]
    #   = [(ops_of_pred_order, ops_of_succ_order), ...]
    # che viene passato al builder.
 
    
 
    # Carica machine_model_id dall\'ordine macchina
    machine_order_row = session.get(MachineOrder, machine_order_id)  # già disponibile
    # (se non già caricato: session.query(MachineOrder).filter_by(id=machine_order_id).first())
    machine_model_id = machine_order_row.machine_model_id if machine_order_row else None
 
    rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] = []
    parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] = []
    # precedence_pairs già costruita in Step 4b (sequenza intra-routing)

    if machine_model_id is None:
        logger.warning("machine_model_id non trovato per machine_order %s — skip RP precedences", machine_order_id)
    else:
        # Query 1: tutti i RP del modello
        rp_rows = (
            session.query(ReferencePoint)
            .filter(ReferencePoint.machine_model_id == machine_model_id)
            .all()
        )
        # material_code → production_order_id (per ordini di questa macchina)
        all_pos = (
            session.query(ProductionOrder.id, ProductionOrder.material_code)
            .filter(ProductionOrder.machine_order_id == machine_order_id)
            .all()
        )
        material_to_po_id: dict[str, uuid.UUID] = {
            row.material_code: row.id for row in all_pos
        }
        rp_id_to_po_id: dict[uuid.UUID, uuid.UUID] = {}
        for rp in rp_rows:
            if rp.target_order_material and rp.target_order_material in material_to_po_id:
                rp_id_to_po_id[rp.id] = material_to_po_id[rp.target_order_material]
 
        # Query 2: tutti gli archi di precedenza del modello
        prec_rows = (
            session.query(ReferencePointPrecedence)
            .filter(ReferencePointPrecedence.machine_model_id == machine_model_id)
            .all()
        )
 
        # Costruisce children_map: parent_order_id → [child_order_id]
        # Serve per la raccolta ricorsiva delle operazioni
        children_map: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for po_row in session.query(
            ProductionOrder.id, ProductionOrder.parent_order_id
        ).filter(ProductionOrder.machine_order_id == machine_order_id).all():
            if po_row.parent_order_id:
                children_map[po_row.parent_order_id].append(po_row.id)
 
        # ops_by_order: order_id → [op_id] (solo op schedulabili)
        schedulable_op_ids: set[uuid.UUID] = {op.id for op in schedulable_ops}
        ops_by_order: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for op_sc in schedulable_ops:
            ops_by_order[op_sc.production_order_id].append(op_sc.id)

        # ── rp_direct_pairs: vincoli HARD diretti tra op con reference_point_id ──
        # Ogni operazione con reference_point_id è un'op di "integrazione/assemblaggio"
        # che referenzia un nodo specifico del DAG RP. Per ogni arco RP_pred → RP_succ,
        # l'op con RP_pred deve finire prima che l'op con RP_succ inizi.
        # Questo è il vincolo hard che deriva ESCLUSIVAMENTE dal RP DAG.
        #
        # Bypass naturale con componenti mancanti:
        # Le op foglia (GROUP) non hanno reference_point_id → nessun vincolo hard RP su di loro.
        # Se un GROUP ha earliest_start alto (componente mancante), le altre op lavorano
        # liberamente; solo l'op di integrazione (con reference_point_id) rispetta l'ordine RP.
        rp_to_op_id: dict[uuid.UUID, uuid.UUID] = {}
        for _op_r, _routing_r, _po_r in ops_rows:
            if _op_r.id in schedulable_op_ids and _op_r.reference_point_id:
                rp_to_op_id[_op_r.reference_point_id] = _op_r.id

        rp_direct_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
        for prec_d in prec_rows:
            _pred_op = rp_to_op_id.get(prec_d.predecessor_reference_point_id)
            _succ_op = rp_to_op_id.get(prec_d.reference_point_id)
            if _pred_op and _succ_op:
                rp_direct_pairs.append((_pred_op, _succ_op))

        precedence_pairs[:] = rp_direct_pairs   # sovrascrive la lista vuota di Step 4b
        logger.info(
            "Step 4d: %d archi RP DAG → %d rp_direct_pairs (vincoli hard op diretti con RP)",
            len(prec_rows), len(rp_direct_pairs),
        )

        # Per ogni arco pred_rp → succ_rp nel DAG, costruisce il vincolo subtree
        for prec in prec_rows:
            pred_rp_id = prec.predecessor_reference_point_id
            succ_rp_id = prec.reference_point_id
 
            pred_po_id = rp_id_to_po_id.get(pred_rp_id)
            succ_po_id = rp_id_to_po_id.get(succ_rp_id)
 
            if pred_po_id is None or succ_po_id is None:
                logger.debug(
                    "RP arco %s→%s: uno dei due ordini target non trovato — skip",
                    pred_rp_id, succ_rp_id,
                )
                continue
 
            # Raccoglie TUTTE le op schedulabili dell\'ordine A e dei suoi figli
            ops_pred = _collect_ops_recursive(
                pred_po_id, children_map, ops_by_order, schedulable_op_ids
            )
            ops_succ = _collect_ops_recursive(
                succ_po_id, children_map, ops_by_order, schedulable_op_ids
            )
 
            if not ops_pred or not ops_succ:
                logger.debug(
                    "RP arco %s→%s: pred_ops=%d succ_ops=%d — nessun vincolo generato",
                    pred_rp_id, succ_rp_id, len(ops_pred), len(ops_succ),
                )
                continue
 
            rp_order_constraints.append((ops_pred, ops_succ))
            logger.debug(
                "RP constraint: %d op di ordine %s → %d op di ordine %s",
                len(ops_pred), pred_po_id, len(ops_succ), succ_po_id,
            )
 
        logger.info(
            "Step 4d: %d archi DAG → %d vincoli RP generati",
            len(prec_rows), len(rp_order_constraints),
        )

        # ── Tipo A: vincolo BOM HARD (ordine-livello, ricorsivo) ─────────────────
        # Semantica: TUTTE le op di un ordine padre devono aspettare che TUTTE le
        # op di TUTTI i figli BOM (diretti e ricorsivi) siano completate prima di
        # poter iniziare.
        #
        # Questo è un vincolo HARD: prima di lavorare un'operazione di un ordine,
        # tutti gli ordini nella sua BOM devono essere completati.
        #
        # Il parallelismo tra sottoalberi (es. GRP-001..003 e GRP-004..007) è
        # possibile perché non esiste alcun vincolo hard tra di loro: il vincolo
        # BOM agisce solo tra padre e i SUOI figli (non tra figli di padri diversi).
        #
        # La priorità di quale sottoalbero lavorare prima è fornita dal DAG RP
        # (rp_order_constraints → op_priority), che è SOFT e guida solo il
        # dispatch del greedy e il warm-start del CP-SAT.
        for order_id, child_ids in children_map.items():
            if not child_ids:
                continue
            parent_ops_list = ops_by_order.get(order_id, [])
            if not parent_ops_list:
                continue
            # Raccoglie TUTTE le op schedulabili di TUTTI i figli (DFS ricorsivo)
            all_child_ops: list[uuid.UUID] = []
            for child_id in child_ids:
                all_child_ops.extend(_collect_ops_recursive(
                    child_id, children_map, ops_by_order, schedulable_op_ids
                ))
            # Deduplicazione preservando l'ordine
            seen_bom: set[uuid.UUID] = set()
            unique_child_ops = [
                x for x in all_child_ops if not (x in seen_bom or seen_bom.add(x))
            ]
            if not unique_child_ops:
                continue
            for parent_op_id in parent_ops_list:
                parent_wait_constraints.append((unique_child_ops, parent_op_id))
            logger.debug(
                "BOM wait: ordine %s (%d op padre) aspetta %d op figlie",
                order_id, len(parent_ops_list), len(unique_child_ops),
            )

        logger.info(
            "Step 4d: %d parent_wait_constraints (BOM HARD ordine-livello, %d ordini con figli)",
            len(parent_wait_constraints), len([k for k, v in children_map.items() if v]),
        )
    # ── Calcolo priorità di dispatch (SOFT) ──────────────────────────────────
    # Le op foglia (GROUP/COMPONENT) non hanno vincoli hard tra di loro: la priorità
    # guida solo la dispatch queue del greedy quando le risorse sono limitate.
    # Con risorse libere tutte le op pronte partono in parallelo.
    #
    # op_priority = rp_level_ordine × 10000
    #   rp_level = livello nel DAG RP dell'ordine di appartenenza dell'op
    #   (livello ereditato: GRP-032 sotto AGG-001 sotto RP-01 → level 0)
    #
    # NON si usa più sequence_number: l'ordinamento deriva solo dai RP.

    _op_to_order: dict[uuid.UUID, uuid.UUID] = {
        op_sc.id: op_sc.production_order_id for op_sc in schedulable_ops
    }
    _order_preds_lv: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for _ops_pred_lv, _ops_succ_lv in rp_order_constraints:
        _pred_ords = {_op_to_order[oid] for oid in _ops_pred_lv if oid in _op_to_order}
        _succ_ords = {_op_to_order[oid] for oid in _ops_succ_lv if oid in _op_to_order}
        for _so in _succ_ords:
            _order_preds_lv[_so].update(_pred_ords)

    _all_order_ids = {op.production_order_id for op in schedulable_ops}
    _rp_level: dict[uuid.UUID, int] = {oid: 0 for oid in _all_order_ids}
    _changed = True
    while _changed:
        _changed = False
        for _oid in _all_order_ids:
            for _pred_ord in _order_preds_lv.get(_oid, set()):
                _new_lv = _rp_level.get(_pred_ord, 0) + 1
                if _new_lv > _rp_level[_oid]:
                    _rp_level[_oid] = _new_lv
                    _changed = True

    # Intra-routing RP depth: per dare priorità anche all'ordine delle op all'interno
    # del routing (rp_direct_pairs). Se op-A deve precedere op-B secondo il DAG RP
    # intra-routing, op-A ha depth più bassa → viene dispatchata prima.
    # Questo è SOFT: se op-A è bloccata (mancanti) op-B può comunque partire.
    _op_intra_depth: dict[uuid.UUID, int] = {op_sc.id: 0 for op_sc in schedulable_ops}
    _intra_changed = True
    while _intra_changed:
        _intra_changed = False
        for _pred_id, _succ_id in precedence_pairs:
            _new_depth = _op_intra_depth.get(_pred_id, 0) + 1
            if _new_depth > _op_intra_depth.get(_succ_id, 0):
                _op_intra_depth[_succ_id] = _new_depth
                _intra_changed = True

    op_priority: dict[uuid.UUID, int] = {
        op_sc.id: _rp_level.get(op_sc.production_order_id, 0) * 10000
                  + _op_intra_depth.get(op_sc.id, 0)
        for op_sc in schedulable_ops
    }
    logger.info(
        "Priorità dispatch calcolate: %d op, max RP level=%d, max intra-depth=%d",
        len(op_priority), max(_rp_level.values(), default=0),
        max(_op_intra_depth.values(), default=0),
    )

    # ── Step 5: Orizzonte (solo bound di sicurezza per il greedy) ─────────────
    # Lo scheduler greedy riempie le prime finestre disponibili: l'orizzonte serve
    # solo come limite anti-loop. Usiamo il target se presente, altrimenti 1 anno.
    from app.core.scheduler.shift_preprocessor import compute_horizon_minutes
    from datetime import timedelta

    if scenario.target_finish_date:
        horizon_date = date.fromisoformat(str(scenario.target_finish_date))
    else:
        horizon_date = schedule_start_date + timedelta(days=365)

    horizon = compute_horizon_minutes(horizon_date, epoch)
    logger.info("Scheduling horizon: %s (%d minutes)", horizon_date, horizon)

    objective_mode = scenario.objective_mode.value if scenario.objective_mode else "FINISH_BY_DATE"

    total_work = sum(op.planned_duration_minutes for op in schedulable_ops)

    # Op senza alcun gruppo risorse compatibile (workcenter + skill)
    from app.core.scheduler.cpsat_types import _SKILL_CAN_DO
    orphan_ops = []
    for op in schedulable_ops:
        has_group = any(
            g.workcenter_id == op.workcenter_id
            and op.operation_type in _SKILL_CAN_DO.get(g.skill, set())
            for g in resource_groups
        )
        if not has_group:
            orphan_ops.append((op.id, op.operation_type, op.workcenter_id))
    logger.info("Operazioni senza gruppo risorse: %d", len(orphan_ops))
    for op_id, op_type, wc_id in orphan_ops[:10]:
        logger.info("  → %s tipo=%s wc=%s", op_id, op_type, wc_id)

    impossible_ops = [
        op for op in schedulable_ops
        if op.earliest_start_minutes + op.planned_duration_minutes > horizon
    ]
    logger.warning("Operazioni impossible (earliest+dur > horizon): %d", len(impossible_ops))
    logger.info("Epoch: %s, Horizon: %d min (%s)", epoch, horizon, horizon_date)

    # ── Scheduling a capacità di gruppo ───────────────────────────────────────
    # 1) greedy (capacity_scheduler): veloce, sempre fattibile → orizzonte stretto + warm-start
    # 2) CP-SAT cumulativo (capacity_cpsat): OTTIMIZZA (makespan / FINISH_BY_DATE) partendo
    #    dall'hint del greedy. Se non migliora o va in timeout → fallback al greedy.
    import os as _os
    import time as _time
    from app.core.scheduler.capacity_scheduler import CapacityScheduler

    _t0 = _time.perf_counter()
    greedy_result = CapacityScheduler(
        operations=schedulable_ops,
        resource_groups=resource_groups,
        horizon_minutes=horizon,
        epoch=epoch,
        precedence_pairs=precedence_pairs,
        rp_order_constraints=rp_order_constraints,
        parent_wait_constraints=parent_wait_constraints,
        missing_constraints=missing_constraints,
        op_priority=op_priority,
    ).solve()

    cap_result = greedy_result
    engine_used = "greedy"

    use_cpsat = _os.getenv("CPSAT_CAPACITY_ENABLED", "1") == "1"
    if use_cpsat and greedy_result.status == "OPTIMAL" and greedy_result.makespan_minutes:
        from app.core.scheduler.capacity_cpsat import CapacityCpsatScheduler

        margin = float(_os.getenv("CPSAT_CAPACITY_HORIZON_MARGIN", "1.5"))
        cpsat_horizon = min(horizon, int(greedy_result.makespan_minutes * margin) + 1440)
        target_min: int | None = None
        if scenario.target_finish_date:
            _tdt = datetime(
                scenario.target_finish_date.year, scenario.target_finish_date.month,
                scenario.target_finish_date.day, 23, 59, tzinfo=timezone.utc,
            )
            target_min = datetime_to_minutes(_tdt, epoch)
        cpsat_result = CapacityCpsatScheduler(
            operations=schedulable_ops,
            resource_groups=resource_groups,
            horizon_minutes=cpsat_horizon,
            epoch=epoch,
            precedence_pairs=precedence_pairs,
            rp_order_constraints=rp_order_constraints,
            parent_wait_constraints=parent_wait_constraints,
            missing_constraints=missing_constraints,
            objective_mode=objective_mode,
            target_finish_minutes=target_min,
            timeout_seconds=float(_os.getenv("CPSAT_CAPACITY_TIMEOUT", "30")),
            warm_start=greedy_result,
        ).solve()
        if cpsat_result.status in ("OPTIMAL", "FEASIBLE") and cpsat_result.entries:
            cap_result = cpsat_result
            engine_used = "cpsat"
        else:
            logger.info(
                "CP-SAT capacità non utilizzabile (status=%s) → fallback greedy",
                cpsat_result.status,
            )

    solve_seconds = round(_time.perf_counter() - _t0, 3)
    scheduled_op_ids = {e.operation_id for e in cap_result.entries}
    resources_used = len({(e.workcenter_id, e.skill, e.lane_index) for e in cap_result.entries})
    logger.info(
        "Motore=%s %s in %.3fs: %d op schedulate, %d entries, makespan=%s min",
        engine_used, cap_result.status, solve_seconds, len(scheduled_op_ids),
        len(cap_result.entries), cap_result.makespan_minutes,
    )

    makespan_days = (
        round(cap_result.makespan_minutes / 1440, 2)
        if cap_result.makespan_minutes is not None
        else None
    )

    # ── Entries (datetime) dai blocchi del greedy ─────────────────────────────
    from app.core.scheduler.shift_preprocessor import minutes_to_datetime
    from app.enums import ScheduleEntryStatus

    db_entries: list[ScheduleEntry] = [
        ScheduleEntry(
            id=uuid.uuid4(),
            scenario_id=scenario_id,
            operation_id=e.operation_id,
            operator_id=None,                       # capacità di gruppo → niente operatore con nome
            resource_type_id=e.resource_type_id,
            workcenter_id=e.workcenter_id,
            scheduled_start=minutes_to_datetime(e.start_minutes, epoch),
            scheduled_end=minutes_to_datetime(e.end_minutes, epoch),
            status=ScheduleEntryStatus.SCHEDULED,
            delay_minutes=0,
            is_manual_override=False,
        )
        for e in cap_result.entries
    ]

    earliest_start_iso: str | None = None
    latest_end_iso: str | None = None
    if db_entries:
        earliest_start_iso = min(e.scheduled_start for e in db_entries).isoformat()
        latest_end_iso = max(e.scheduled_end for e in db_entries).isoformat()

    # ── Workcenter breakdown ──────────────────────────────────────────────────
    from collections import Counter as _Counter
    wc_op_counts = _Counter(str(op.workcenter_id) for op in schedulable_ops)
    wc_group_caps: dict[str, int] = defaultdict(int)
    for g in resource_groups:
        wc_group_caps[str(g.workcenter_id)] += g.weekly_capacity_minutes
    workcenter_breakdown = [
        {
            "workcenter_id": wc_id,
            "ops_count": wc_op_counts[wc_id],
            "group_capacity_min_per_week": wc_group_caps.get(wc_id, 0),
        }
        for wc_id in sorted(wc_op_counts.keys())
    ]

    run_summary: dict = {
        # ── Input ──────────────────────────────────────────────────────────
        "schedule_start_date": str(schedule_start_date),
        "horizon_date": str(horizon_date),
        "objective_mode": objective_mode,
        "triggered_by": triggered_by,
        "total_schedulable_ops": len(schedulable_ops),
        "total_work_minutes": total_work,
        "group_capacity_min_per_week": group_daily_capacity,
        "resource_groups_total": len(resource_groups),
        "workcenter_breakdown": workcenter_breakdown,
        # ── Vincoli applicati ──────────────────────────────────────────────
        "in_progress_anchored": len(in_progress_op_ids),
        "missing_constraints_active": len(missing_constraints),
        "rp_order_constraints_count": len(rp_order_constraints),
        "parent_wait_constraints_count": len(parent_wait_constraints),
        "orphan_ops_count": len(orphan_ops),
        "impossible_ops_count": len(impossible_ops),
        # ── Risultato ──────────────────────────────────────────────────────
        "engine_used": engine_used,
        "solver_status": cap_result.status,
        "solve_time_seconds": solve_seconds,
        "scheduled_ops": len(scheduled_op_ids),
        "scheduled_entries": len(db_entries),
        "resources_used": resources_used,
        "operators_used": resources_used,   # retrocompat campo storico
        "makespan_days": makespan_days,
        "earliest_start": earliest_start_iso,
        "latest_end": latest_end_iso,
        "conflicts": cap_result.conflicts or [],
    }

    # Salva risultato nello scenario
    scenario_obj = session.get(ScheduleScenario, scenario_id)
    if scenario_obj:
        scenario_obj.last_run_status = cap_result.status
        scenario_obj.last_run_at = datetime.now(timezone.utc)
        scenario_obj.last_run_makespan_days = makespan_days
        scenario_obj.last_run_operators_used = resources_used
        scenario_obj.last_run_conflicts = cap_result.conflicts if cap_result.status == "INFEASIBLE" else None
        scenario_obj.last_run_summary = run_summary

    # ── Step 8: Persist new entries ───────────────────────────────────────────
    if cap_result.status in ("OPTIMAL", "FEASIBLE") and db_entries:
        for entry in db_entries:
            session.add(entry)

    # ── Step 9: Delete STALE entries ──────────────────────────────────────────
    _cleanup_stale(session, scenario_id)

    return {
        "status": cap_result.status,
        "scenario_id": str(scenario_id),
        "makespan_days": makespan_days,
        "operators_used": resources_used,
        "conflicts": cap_result.conflicts,
        "summary": run_summary,
    }


def _cleanup_stale(session: Session, scenario_id: uuid.UUID) -> None:
    """Delete all STALE schedule entries for the scenario."""
    from app.enums import ScheduleEntryStatus
    from app.models.schedule import ScheduleEntry

    session.query(ScheduleEntry).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.status == ScheduleEntryStatus.STALE,
    ).delete(synchronize_session="fetch")


def _broadcast_complete(scenario_id: str, result: dict) -> None:
    """Fire-and-forget WebSocket broadcast using asyncio.run in a thread."""
    import asyncio
    import threading

    from app.websocket.manager import manager

    async def _send() -> None:
        msg_type = (
            "RESCHEDULE_COMPLETE"
            if result.get("status") in ("OPTIMAL", "FEASIBLE")
            else "SCHEDULE_INFEASIBLE"
        )
        payload = {"type": msg_type, "scenario_id": scenario_id, **result}
        await manager.broadcast(scenario_id, payload)

    def _thread_target() -> None:
        try:
            asyncio.run(_send())
        except Exception:
            pass  # WebSocket errors must not crash the Celery task

    threading.Thread(target=_thread_target, daemon=True).start()
