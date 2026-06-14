# Copilot Agent Steps — MES Production Scheduler
# 21 step da inviare uno alla volta all'agente.
# Prima di ogni step: verifica che lo step precedente sia completato e runnable.
# Template recupero contesto: "@workspace leggi .github/copilot-instructions.md prima di procedere."

---

## ═══════════════════════════════════════════════════
## STEP 1 — Infrastructure: Docker Compose + DB + Alembic
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md per il contesto completo del progetto.

STEP 1 — Infrastructure setup

Crea la struttura base del progetto con Docker Compose, configurazione DB e Alembic.

FILE DA CREARE:
1. docker-compose.yml con 4 servizi:
   - postgres:16-alpine — porta 5432, volume persistente, db "scheduler", schema "public"
   - redis:7-alpine — porta 6379
   - backend — build da ./backend/Dockerfile, porta 8000, dipende da postgres e redis,
     volume mount ./backend:/app per hot-reload
   - frontend — build da ./frontend/Dockerfile, porta 5173, dipende da backend

2. .env.example con tutte le variabili:
   DATABASE_URL=postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler
   REDIS_URL=redis://localhost:6379/0
   ANTHROPIC_API_KEY=sk-ant-...
   CPSAT_TIMEOUT_SECONDS=60
   MIN_OP_DURATION_MINUTES=30
   ENVIRONMENT=development

3. backend/Dockerfile:
   - FROM python:3.12-slim
   - Installa: ortools, fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg,
     alembic, celery, redis, anthropic, networkx, weasyprint, python-dotenv, pytest

4. backend/alembic.ini e backend/alembic/env.py configurati per async SQLAlchemy
   con target_metadata dai modelli (da creare allo step 3)

5. backend/app/db/session.py:
   - async engine con asyncpg
   - AsyncSessionLocal
   - get_db() dependency FastAPI

6. backend/app/main.py:
   - FastAPI app base con CORS, health check GET /health → {"status": "ok"}
   - Include router placeholder (da popolare negli step successivi)

7. frontend/Dockerfile:
   - FROM node:20-alpine
   - Vite dev server su 0.0.0.0:5173

VINCOLI:
- Tutte le variabili sensibili da .env, mai hardcodate
- docker-compose up --build deve avviarsi senza errori
- Il backend deve rispondere su /health prima di procedere

OUTPUT ATTESO: docker-compose.yml, .env.example, backend/Dockerfile, frontend/Dockerfile,
backend/alembic.ini, backend/alembic/env.py, backend/app/db/session.py, backend/app/main.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 2 — Mock Data: Seed Script completo
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "8. MOCK DATA".

STEP 2 — Seed script completo TURBOPRESS-X500

Crea backend/app/db/seed.py con tutti i dati mock per testare l'intero sistema.

DATI DA GENERARE (in ordine di inserimento per rispettare FK):

1. WORKCENTERS (3):
   WC-MILANO (Milano), WC-TORINO (Torino), WC-BERGAMO (Bergamo)

2. MACHINE MODEL (1):
   code="TX500", name="TURBOPRESS-X500"

3. SHIFTS (3):
   Mattina: 06:00-14:00, pausa 30min
   Pomeriggio: 14:00-22:00, pausa 30min
   Notte: 22:00-06:00, pausa 30min

4. SKILL_WORKCENTER_MAPPING (9 righe: 3 skill × 3 workcenter):
   ELECTRICAL in qualsiasi WC → can_do_electrical=True, resto False
   MECHANICAL in qualsiasi WC → can_do_mechanical=True, resto False
   MULTI in qualsiasi WC → tutto True

5. OPERATORS (20, con nomi italiani fittizi):
   WC-MILANO: 3 ELECTRICAL (es. Marco Bianchi, Anna Colombo, Luca Ferrari),
              3 MECHANICAL (es. Giuseppe Russo, Maria Esposito, Paolo Romano),
              2 MULTI (es. Sara Conti, Diego Marino)
   WC-TORINO: 2 ELECTRICAL, 3 MECHANICAL, 2 MULTI
   WC-BERGAMO: 1 ELECTRICAL, 2 MECHANICAL, 2 MULTI

6. MACHINE ORDER (1):
   sap_order_id="ORD-MACH-001", machine_model=TX500, workcenter=WC-MILANO

7. BOM COMPLETA — production_orders (gerarchia):
   Usa random.seed(42) per tutte le generazioni randomiche.

   Macroaggregati (3, figli diretti della macchina):
   - MA-001: "Gruppo Idraulico", workcenter WC-MILANO
   - MA-002: "Quadro Elettrico", workcenter WC-MILANO
   - MA-003: "Struttura Portante", workcenter WC-BERGAMO

   Aggregati (15 totali):
   - MA-001 → AGG-001 "Cilindro Principale", AGG-002 "Pompa Olio",
               AGG-003 "Collettore", AGG-004 "Accumulatore", AGG-005 "Filtro Idraulico"
   - MA-002 → AGG-006 "Armadio Principale", AGG-007 "Modulo PLC",
               AGG-008 "Pannello HMI", AGG-009 "Quadro Distribuzione"
   - MA-003 → AGG-010 "Telaio Base", AGG-011 "Montanti", AGG-012 "Traversa"

   Gruppi (40 totali, 2-4 per aggregato):
   Genera gruppi realistici. Es. AGG-001 → GRP-001 "Kit Guarnizioni Cilindro",
   GRP-002 "Gruppo Pistoni", GRP-003 "Flangia Attacco"

   Componenti (~150, 3-6 per gruppo):
   Mix di is_purchase_component=True e is_production_component_untracked=True

8. Z_ORDERS_LINK:
   Popola specchiando la gerarchia production_orders

9. ROUTINGS + OPERATIONS:
   Per ogni ordine non-componente crea routing + 3-6 operazioni con:
   - operation_type distribuito realisticamente (MECHANICAL per struttura/idraulica,
     ELECTRICAL per quadro, GENERAL per assemblaggio generico)
   - workcenter = workcenter dell'ordine padre
   - planned_duration_minutes: random tra 120 e 480 (seed 42)
   - sequence_number: incrementale

10. REFERENCE POINTS (10, modello TX500):
    RP-001 → MA-003 (target_level=MACROAGGREGATE, target_order_material="MA-003-MAT")
    RP-002 → MA-001 (predecessore: RP-001)
    RP-003 → MA-002 (predecessore: RP-001)
    RP-004 → AGG-001 (predecessore: RP-002)
    RP-005 → AGG-002 (predecessore: RP-004)
    RP-006 → AGG-006 (predecessore: RP-003)
    RP-007 → AGG-007 (predecessore: RP-006)
    RP-008 → AGG-010 (predecessore: RP-001)
    RP-009 → AGG-011 (predecessore: RP-008)
    RP-010 → AGG-003 (predecessori: RP-004, RP-005)
    Assegna reference_point_id alle operazioni dell'ordine macchina.

11. REFERENCE_POINT_PRECEDENCES:
    Inserisci le coppie descritte sopra. Verifica che il DAG sia aciclico.

12. MISSING COMPONENTS (5 pre-settati):
    VLV-2200 → arrivo oggi+7gg, in GRP-001
    CAB-450  → arrivo oggi+3gg, in un gruppo di MA-002
    SEN-P100 → arrivo oggi+12gg, in un gruppo di MA-001
    VIT-M16  → arrivo oggi+1gg
    GUA-200  → arrivo oggi+5gg

13. OPERATOR_CALENDAR (4 settimane da oggi):
    Genera con random.seed(42): ogni operatore ha turno assegnato ogni giorno.
    Inserisci ~8 assenze casuali distribuite nei 20 operatori nelle 4 settimane.

14. SCHEDULE_SCENARIOS (1 scenario di default):
    name="Scenario Base", objective_mode=FINISH_BY_DATE, is_active=True

VINCOLI:
- Script idempotente: usa INSERT ... ON CONFLICT (sap_order_id) DO NOTHING o equivalente
- Eseguibile con: cd backend && python -m app.db.seed
- Stampa a console il conteggio di ogni tabella dopo l'inserimento
- random.seed(42) all'inizio dello script, mai seed diverso

OUTPUT ATTESO: backend/app/db/seed.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 3 — Backend: SQLAlchemy Models + Pydantic Schemas
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "3. MODELLO DATI".

STEP 3 — SQLAlchemy models e Pydantic schemas

PARTE A — SQLAlchemy Models (backend/app/models/)
Crea un file per dominio (non uno per tabella per ridurre import circolari):

- base.py: Base declarativa, UUIDMixin (id UUID PK default uuid4), TimestampMixin (created_at)
- machine.py: MachineModel, MachineOrder
- production.py: ProductionOrder, ZOrdersLink
- routing.py: Routing, Operation
- reference.py: ReferencePoint, ReferencePointPrecedence
- workcenter.py: Workcenter, SkillWorkcenterMapping
- operator.py: Operator, Shift, OperatorCalendar
- missing.py: MissingComponent
- schedule.py: ScheduleEntry, ScheduleScenario
- delay.py: DelayEvent
- ai.py: AiSuggestion, AiChatSession

Ogni model deve avere:
- Tutti i campi dalla sezione "3. MODELLO DATI" delle instructions
- Relationship lazy="selectin" dove utile per evitare N+1
- ENUM Python per tutti i campi status/type/level/skill
- __repr__ utile per debug

PARTE B — Pydantic Schemas (backend/app/schemas/)
Per ogni model crea: Base, Create, Update, Read (con id e timestamps).
Usa Pydantic v2 (model_config = ConfigDict(from_attributes=True)).

File:
- machine.py, production.py, routing.py, reference.py
- workcenter.py, operator.py, missing.py, schedule.py, delay.py, ai.py

Schema speciali da creare:
- BOMTreeNode (ricorsivo, per GET /api/orders/machine/{id}/bom-tree)
- GanttEntry (per GET /api/schedule/scenario/{id}/gantt-data)
- ScheduleRunRequest (scenario_id, objective_mode, objective_params_json)
- DelayImpactResponse (impacted_entries[], estimated_delta_days)

PARTE C — Prima Migration Alembic
Esegui: alembic revision --autogenerate -m "initial_schema"
Verifica che crei tutte le 19 tabelle. Includila nel progetto.

VINCOLI:
- Zero import circolari (usa TYPE_CHECKING per le relationship)
- Tutti gli ENUM definiti in enums.py centralizzato
- after_step: python -c "from app.models import *; print('OK')" deve passare

OUTPUT ATTESO: backend/app/models/*.py, backend/app/schemas/*.py,
backend/app/enums.py, backend/alembic/versions/001_initial_schema.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 4 — Scheduler Core: DAG Builder
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "4. ALGORITMO SCHEDULER, Fase 1".

STEP 4 — DAG Builder

Crea backend/app/core/scheduler/dag_builder.py

FUNZIONI DA IMPLEMENTARE:

1. build_precedence_dag(machine_model_id: UUID, db: AsyncSession) -> nx.DiGraph
   - Carica reference_points del modello da DB
   - Carica reference_point_precedences
   - Costruisce DiGraph networkx: nodo=rp.id, arco A→B = "A è predecessore di B"
   - Chiama validate_dag() — lancia CyclicDependencyError se trova cicli
   - Ritorna il grafo

2. validate_dag(dag: nx.DiGraph) -> None
   - usa nx.find_cycle() per trovare il ciclo
   - Se esiste: lancia CyclicDependencyError con messaggio che elenca
     gli archi del ciclo in formato leggibile (es. "RP-003 → RP-001 → RP-003")

3. get_scheduling_order(dag: nx.DiGraph, db: AsyncSession) -> list[SchedulingNode]
   - Calcola topological_sort (nx.topological_sort)
   - Per ogni rp_id nel sort, risolvi in production_order_id
     via reference_point.target_order_material → production_orders.material_code
   - Ritorna lista di SchedulingNode(rp_id, production_order_id, level, priority_rank)

4. get_roots(dag: nx.DiGraph) -> list[UUID]
   - Ritorna nodi senza predecessori (in_degree == 0) → priorità massima

5. resolve_blocking_orders(rp_id: UUID, db: AsyncSession) -> list[UUID]
   - Dato un reference point, ritorna tutti i production_order_id
     che devono essere COMPLETED prima che l'operazione macchina con quel RP possa iniziare

ECCEZIONI CUSTOM (in backend/app/core/scheduler/exceptions.py):
- CyclicDependencyError(cycle_edges: list[tuple])
- SchedulingInfeasibleError(conflicts: list[str])
- InsufficientResourcesError(operation_id, required_skill, workcenter)

TEST (backend/tests/test_dag_builder.py):
- test_linear_dag: A→B→C → order [A, B, C], roots=[A]
- test_multiple_roots: A e B senza predecessori, C dipende da entrambi
  → A e B in prima posizione (qualsiasi ordine), C dopo
- test_cycle_detection: A→B→C→A → CyclicDependencyError con ciclo nel messaggio
- test_empty_dag: nessun RP → lista vuota, nessuna eccezione
- test_diamond: A→B, A→C, B→D, C→D → D dopo B e C
- test_orphan_node: nodo senza archi → trattato come root

VINCOLI:
- async/await ovunque (db è AsyncSession)
- Nessuna query N+1 (carica tutto in 2 query max)
- Type hints completi

OUTPUT ATTESO: backend/app/core/scheduler/dag_builder.py,
backend/app/core/scheduler/exceptions.py,
backend/tests/test_dag_builder.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 5 — Scheduler Core: Shift Preprocessor
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "4. ALGORITMO SCHEDULER, Pre-processing".

STEP 5 — Shift Preprocessor

Crea backend/app/core/scheduler/shift_preprocessor.py

SCOPO: convertire operator_calendar + shifts in slot interi (minuti dall'epoch)
che CP-SAT può consumare direttamente.

FUNZIONI DA IMPLEMENTARE:

1. compute_epoch(reference_date: date) -> datetime
   - Ritorna reference_date alle 00:00 UTC
   - Questa è l'epoch (minuto 0) per tutti i calcoli CP-SAT

2. datetime_to_minutes(dt: datetime, epoch: datetime) -> int
   - Converte datetime in intero minuti dall'epoch
   - Sempre floor (non round)

3. minutes_to_datetime(minutes: int, epoch: datetime) -> datetime
   - Inverso di datetime_to_minutes

4. build_operator_available_slots(
       operator_id: UUID,
       start_date: date,
       end_date: date,
       epoch: datetime,
       db: AsyncSession
   ) -> list[tuple[int, int]]
   - Carica operator_calendar per il periodo
   - Per ogni giorno disponibile (is_available=True e shift_id non null):
     calcola (shift_start_minute, shift_end_minute - break_duration)
     rispettando la pausa (rimuovi i minuti di pausa dal totale disponibile,
     modella la pausa come slot indisponibile a metà turno)
   - Ritorna lista di (start_min, end_min) ordinata cronologicamente

5. build_all_operators_slots(
       operator_ids: list[UUID],
       start_date: date,
       end_date: date,
       epoch: datetime,
       db: AsyncSession
   ) -> dict[UUID, list[tuple[int, int]]]
   - Chiama build_operator_available_slots per ogni operatore
   - Una sola query DB (carica tutto il calendario in batch)

6. build_unavailable_intervals(
       operator_id: UUID,
       all_slots: list[tuple[int, int]],
       horizon_minutes: int,
       epoch: datetime
   ) -> list[tuple[int, int]]
   - Inverso degli slot disponibili: ritorna i gap (periodi indisponibili)
   - Usato per costruire IntervalVar fissi in CP-SAT

7. compute_horizon_minutes(end_date: date, epoch: datetime) -> int
   - Ritorna i minuti dall'epoch a end_date 23:59 UTC

TEST (backend/tests/test_shift_preprocessor.py):
- test_datetime_to_minutes_roundtrip: conversione A→int→A = A
- test_full_shift_no_absence: operatore con turno mattina 5 giorni → 5×(480-30) min disponibili
- test_absence_day: giorno con is_available=False → nessuno slot quel giorno
- test_null_shift: shift_id=None → nessuno slot (=assenza)
- test_unavailable_intervals_contiguous: disponibile solo [100,200] su horizon 300
  → indisponibile = [(0,100), (200,300)]
- test_night_shift_crosses_midnight: turno 22:00-06:00 → slot corretto cross-day

VINCOLI:
- Tutto in UTC
- Nessun arrotondamento: sempre integer floor
- Pausa modellata come gap a metà turno (es. turno 06-14 con 30min pausa →
  slot1: 06:00-10:00, slot2: 10:30-14:00)

OUTPUT ATTESO: backend/app/core/scheduler/shift_preprocessor.py,
backend/tests/test_shift_preprocessor.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 6a — CP-SAT: Variabili e Strutture Dati
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "4. ALGORITMO SCHEDULER, Fase 2".

STEP 6a — CP-SAT Model Builder: variabili e strutture dati

Crea backend/app/core/scheduler/cpsat_model_builder.py (prima parte).

DATACLASSES DI INPUT/OUTPUT (in cpsat_types.py):

@dataclass
class SchedulableOperation:
    id: UUID
    routing_id: UUID
    production_order_id: UUID
    operation_type: OperationType       # ELECTRICAL|MECHANICAL|GENERAL
    workcenter_id: UUID
    planned_duration_minutes: int
    progress_pct: float                 # 0-100
    can_be_interrupted: bool
    earliest_start_minutes: int         # da vincoli mancanti o precedenze
    reference_point_id: UUID | None     # solo per op macchina

@dataclass
class QualifiedOperator:
    id: UUID
    skill: SkillType
    workcenter_id: UUID
    available_slots: list[tuple[int, int]]

@dataclass
class CpsatVariables:
    op_start: dict[UUID, IntVar]
    op_end: dict[UUID, IntVar]
    op_interval: dict[UUID, IntervalVar]
    op_duration: dict[UUID, int]        # durata residua calcolata
    assignments: dict[tuple[UUID, UUID], BoolVar]  # (op_id, operator_id)
    operator_optional_intervals: dict[tuple[UUID, UUID], IntervalVar]

@dataclass
class CpsatSolution:
    status: str                         # OPTIMAL|FEASIBLE|INFEASIBLE|UNKNOWN
    schedule_entries: list[ScheduleEntryCreate]
    makespan_minutes: int | None
    operators_used: int | None
    solve_time_seconds: float
    conflicts: list[str]                # popolato se INFEASIBLE

CLASSE PRINCIPALE (cpsat_model_builder.py):

class CpsatModelBuilder:
    MIN_OP_DURATION: int = int(os.getenv("MIN_OP_DURATION_MINUTES", 30))
    TIMEOUT: float = float(os.getenv("CPSAT_TIMEOUT_SECONDS", 60))

    def __init__(
        self,
        operations: list[SchedulableOperation],
        operators: list[QualifiedOperator],
        horizon_minutes: int,
        epoch: datetime,
        missing_components_constraints: dict[UUID, int],  # op_id → earliest_start
        precedence_pairs: list[tuple[UUID, UUID]]         # (pred_op_id, succ_op_id)
    ):
        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()
        self.operations = operations
        self.operators = operators
        self.horizon = horizon_minutes
        self.epoch = epoch
        self.missing_constraints = missing_components_constraints
        self.precedence_pairs = precedence_pairs
        self.vars: CpsatVariables | None = None

    def _compute_residual_duration(self, op: SchedulableOperation) -> int:
        residual = op.planned_duration_minutes * (1 - op.progress_pct / 100)
        return max(int(math.ceil(residual)), self.MIN_OP_DURATION)

    def _get_qualified_operators(self, op: SchedulableOperation) -> list[QualifiedOperator]:
        # Filtra per workcenter_id matching E skill compatibile con operation_type
        # Usa skill_workcenter_mapping (passata come parametro o da contesto)
        ...

    def _create_variables(self) -> CpsatVariables:
        # Crea tutte le IntVar, IntervalVar, BoolVar
        # Per ogni operazione: start, end, interval con durata residua
        # Per ogni (op, operatore qualificato): BoolVar assegnazione
        # Per ogni (op, operatore): OptionalIntervalVar condizionato su BoolVar
        ...

VINCOLI DA IMPLEMENTARE (stub — implementazione nel 6b e 6c):
    def _add_assignment_constraints(self): ...
    def _add_shift_nooverlap_constraints(self): ...
    def _add_operator_nooverlap_constraints(self): ...
    def _add_precedence_constraints(self): ...
    def _add_missing_component_constraints(self): ...
    def _set_objective(self, objective_mode: str, params: dict): ...
    def build_and_solve(self, objective_mode: str, params: dict) -> CpsatSolution: ...

VINCOLI:
- Nessuna logica di vincolo in questo step (solo strutture e variabili)
- I tipi devono essere corretti e importabili dagli step successivi
- math.ceil per durata residua — mai sotto MIN_OP_DURATION

OUTPUT ATTESO: backend/app/core/scheduler/cpsat_types.py,
backend/app/core/scheduler/cpsat_model_builder.py (struttura con stub)

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 6b — CP-SAT: Vincoli Assegnazione e Turni
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "4. ALGORITMO SCHEDULER, Fase 2, punti 2b-2d".
Leggi anche il file cpsat_model_builder.py già creato.

STEP 6b — CP-SAT: implementa i vincoli di assegnazione operatori e turni

Implementa questi metodi in CpsatModelBuilder (sostituisci gli stub):

1. _add_assignment_constraints()
   Per ogni operazione schedulabile:
   - Raccoglie i BoolVar di assegnazione per gli operatori qualificati
   - Vincolo: sum(assign_vars) >= 1 (almeno un operatore)
   - Logica SIMULTANEOUS: durata effettiva = ceil(residual / n_assigned)
     Modella con variabile ausiliaria n_assigned (IntVar 1..len(qualified)):
       n_assigned = model.NewIntVar(1, len(qualified), f"n_{op.id}")
       model.Add(n_assigned == sum(assign_vars))
     Poi aggiorna op_duration[op.id] con variabile scalata:
       eff_duration = model.NewIntVar(MIN_OP, residual, f"eff_dur_{op.id}")
       model.AddDivisionEquality(eff_duration, residual_int_var, n_assigned)
       Ricrea l'IntervalVar con eff_duration come durata (se non già fissa)
   - Se len(qualified) == 0: aggiungi stringa a self._infeasibility_reasons

2. _add_shift_nooverlap_constraints()
   Per ogni operatore:
   - Costruisci IntervalVar FISSI per i periodi indisponibili
     (usa build_unavailable_intervals da shift_preprocessor)
   - Costruisci lista di OptionalIntervalVar per le op assegnate a questo operatore
     (già in vars.operator_optional_intervals)
   - model.AddNoOverlap(fixed_unavailable + optional_assigned)

3. _add_operator_nooverlap_constraints()
   Per ogni operatore:
   - Raccoglie tutti gli OptionalIntervalVar dell'operatore
   - model.AddNoOverlap(optional_intervals)
   - Questo garantisce: un operatore lavora su una sola operazione alla volta

TEST (backend/tests/test_cpsat_constraints.py):
- test_assignment_at_least_one: modello con 1 op e 2 operatori qualificati →
  soluzione assegna almeno 1 operatore
- test_simultaneous_reduces_duration: 2 operatori assegnati a 1 op di 120min →
  durata effettiva = 60min
- test_operator_nooverlap: 1 operatore, 2 operazioni da 480min, horizon 960min →
  le due operazioni non si sovrappongono
- test_shift_respected: operatore disponibile solo 06-14, op da 240min →
  scheduled_start >= 06:00, scheduled_end <= 14:00
- test_no_qualified_operator: op ELECTRICAL in WC con solo operatori MECHANICAL →
  CpsatSolution.status == "INFEASIBLE"

VINCOLI:
- Non introdurre nuove dipendenze esterne
- I test devono usare dati in-memory (no DB)

OUTPUT ATTESO: cpsat_model_builder.py aggiornato con i 3 metodi implementati,
backend/tests/test_cpsat_constraints.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 6c — CP-SAT: Vincoli Precedenza, Mancanti e Obiettivi
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "4. ALGORITMO SCHEDULER, Fase 2e-2f e Fase 3".
Leggi il file cpsat_model_builder.py corrente.

STEP 6c — CP-SAT: precedenze, mancanti e obiettivi multipli

Implementa gli ultimi metodi in CpsatModelBuilder:

1. _add_precedence_constraints()
   - Per ogni (pred_op_id, succ_op_id) in self.precedence_pairs:
     model.Add(op_end[pred_op_id] <= op_start[succ_op_id])
   - Per le operazioni dell'ordine macchina con reference_point_id:
     recupera blocking_end_minute dal contesto (passato come parametro)
     model.Add(op_start[op.id] >= blocking_end_minute)

2. _add_missing_component_constraints()
   - Per ogni (op_id, earliest_start) in self.missing_constraints:
     model.Add(op_start[op_id] >= earliest_start)

3. _set_objective(objective_mode: str, params: dict)
   Implementa tutti e 4 gli obiettivi:

   FINISH_BY_DATE:
     makespan = NewIntVar(0, horizon, "makespan")
     model.AddMaxEquality(makespan, [op_end[op.id] for op in ops])
     model.Minimize(makespan)
     if "target_finish_minutes" in params:
         model.Add(makespan <= params["target_finish_minutes"])

   MINIMIZE_OPERATORS:
     Per ogni operatore: operator_used[op.id] = NewBoolVar(...)
     Per ogni operatore: model.AddMaxEquality(used, [assign vars dell'operatore])
     model.Minimize(sum(operator_used.values()))

   MAXIMIZE_RESOURCE_UTILIZATION:
     total = sum(assign[(op,oper)] * op_duration[op] for op,oper in assignments)
     model.Maximize(total)

   CUSTOM:
     Weighted sum: w1*makespan_normalized + w2*operators_used + w3*(1-utilization)
     Pesi da params["weights"] = {"makespan": 0.5, "operators": 0.3, "utilization": 0.2}

4. build_and_solve(objective_mode: str, params: dict) -> CpsatSolution
   - Chiama nell'ordine: _create_variables(), _add_assignment_constraints(),
     _add_shift_nooverlap_constraints(), _add_operator_nooverlap_constraints(),
     _add_precedence_constraints(), _add_missing_component_constraints(),
     _set_objective()
   - Configura solver: timeout, num_workers=8
   - Risolvi e ritorna CpsatSolution popolata

TEST (aggiungi a test_cpsat_constraints.py):
- test_precedence_respected: op_A deve precedere op_B → end(A) <= start(B)
- test_missing_component_delays_op: mancante con arrivo minuto 500 →
  op del gruppo schedulata a >= 500
- test_finish_by_date_feasible: horizon ampio, target raggiungibile → OPTIMAL
- test_finish_by_date_infeasible: target impossibile → INFEASIBLE o FEASIBLE senza soddisfare vincolo hard
- test_minimize_operators: 3 op sequenziali con 3 operatori disponibili →
  soluzione usa meno di 3 operatori se possibile
- test_simultaneous_two_operators_halves_duration: verifica riduzione durata

OUTPUT ATTESO: cpsat_model_builder.py completo e testato

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 7 — Scheduler Core: Solution Extractor + Infeasibility Analyzer
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "4. ALGORITMO SCHEDULER, Fase 4".

STEP 7 — Solution Extractor e Infeasibility Analyzer

FILE 1: backend/app/core/scheduler/solution_extractor.py

class SolutionExtractor:
    def extract(
        self,
        solver: cp_model.CpSolver,
        vars: CpsatVariables,
        operations: list[SchedulableOperation],
        epoch: datetime,
        scenario_id: UUID
    ) -> list[ScheduleEntryCreate]:
        """
        Estrae la soluzione CP-SAT e costruisce i ScheduleEntryCreate da salvare.
        Per ogni operazione:
        - Legge solver.Value(op_start[op.id]) e op_end
        - Trova l'operatore assegnato: cerca assign[(op.id, oper.id)] == True
        - Converte minuti→datetime con minutes_to_datetime
        - Crea ScheduleEntryCreate(operation_id, operator_id, workcenter_id,
          scheduled_start, scheduled_end, status=SCHEDULED, scenario_id)
        """

    def compute_makespan(self, entries: list[ScheduleEntryCreate]) -> timedelta:
        """Max scheduled_end - Min scheduled_start"""

    def compute_operator_utilization(
        self, entries: list[ScheduleEntryCreate], total_available_minutes: int
    ) -> dict[UUID, float]:
        """Utilizzo per operatore: minuti_lavorati / minuti_disponibili"""

    def find_critical_path(
        self, entries: list[ScheduleEntryCreate], precedence_pairs: list[tuple[UUID,UUID]]
    ) -> list[UUID]:
        """
        Identifica il percorso critico: catena di operazioni il cui ritardo
        ritarda il makespan. Usa algoritmo longest-path sul DAG delle entries.
        Ritorna lista ordinata di operation_id sul critical path.
        """

FILE 2: backend/app/core/scheduler/infeasibility_analyzer.py

class InfeasibilityAnalyzer:
    def analyze(
        self,
        model: cp_model.CpModel,
        operations: list[SchedulableOperation],
        operators: list[QualifiedOperator],
        missing_constraints: dict[UUID, int],
        precedence_pairs: list[tuple[UUID,UUID]],
        infeasibility_reasons: list[str]
    ) -> list[str]:
        """
        Ritorna lista di stringhe in italiano che spiegano perché il modello
        è INFEASIBLE. Controlla:
        1. Operazioni senza operatori qualificati (workcenter/skill mismatch)
        2. Vincoli mancanti che rendono impossible rispettare horizon
        3. Precedenze circolari residue (safeguard)
        4. Target finish date irraggiungibile dato il carico
        Ogni stringa è leggibile: es. "L'operazione OP-045 (ELECTRICAL) non ha
        operatori qualificati nel workcenter WC-BERGAMO"
        """

    def suggest_fixes(self, conflicts: list[str]) -> list[str]:
        """
        Per ogni conflitto suggerisce una fix concreta in italiano.
        Es. "Aggiungi almeno un operatore ELECTRICAL al workcenter WC-BERGAMO"
        """

TEST (backend/tests/test_solution_extractor.py):
- test_extract_basic: soluzione con 2 op → 2 ScheduleEntryCreate con datetime corretti
- test_critical_path_linear: A→B→C tutti critical → tutti nel path
- test_critical_path_parallel: A→C e B→C con A più lungo → solo A e C nel path
- test_utilization_full_shift: op occupa tutto il turno → utilization ~1.0
- test_infeasibility_no_operators: analisi genera stringa leggibile

OUTPUT ATTESO: solution_extractor.py, infeasibility_analyzer.py,
tests/test_solution_extractor.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 8 — Scheduler Core: Reschedule Engine (Celery) + WebSocket
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "4. ALGORITMO SCHEDULER, Fase 5".

STEP 8 — Reschedule Engine + WebSocket

FILE 1: backend/app/core/scheduler/reschedule_engine.py

Questo è il Celery task che orchestra l'intera rischedulazione.

@celery_app.task(bind=True, max_retries=3)
def reschedule_incremental(self, scenario_id: str, triggered_by: str):
    """
    Rischedula solo le operazioni non COMPLETED del scenario.
    Mantiene fisse quelle IN_PROGRESS (non le sposta).
    Steps:
    1. Carica scenario + operazioni non completed dalla DB (sessione sincrona)
    2. Marca tutte le schedule_entries del scenario come STALE
    3. Recupera operazioni IN_PROGRESS → calcola i loro end come vincolo
    4. Carica mancanti, precedenze, operatori
    5. Pre-processa turni (shift_preprocessor)
    6. Costruisce DAG (dag_builder)
    7. Risolve CP-SAT (cpsat_model_builder)
    8. Salva nuove schedule_entries (solution_extractor)
    9. Elimina entries STALE
    10. Notifica WebSocket
    11. Se AI proattiva abilitata: triggera analyze_proactive task
    """

@celery_app.task
def analyze_proactive(scenario_id: str):
    """Task separato per analisi AI post-scheduling (implementato allo step 18)"""
    pass  # stub per ora

Crea anche:
- backend/celery_worker.py: configurazione Celery app con Redis broker
- backend/app/core/scheduler/scheduler_orchestrator.py:
  funzione sincrona run_schedule(scenario_id, db) che:
  - Può essere chiamata direttamente (per test) o triggera il task Celery
  - Ritorna CpsatSolution

FILE 2: backend/app/websocket/manager.py

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}
        # key = machine_order_id o scenario_id

    async def connect(self, websocket: WebSocket, room: str): ...
    async def disconnect(self, websocket: WebSocket, room: str): ...
    async def broadcast(self, room: str, message: dict): ...

    # Messaggi standard:
    # {"type": "RESCHEDULE_COMPLETE", "scenario_id": "...", "makespan_days": N}
    # {"type": "AI_SUGGESTION_NEW", "count": N, "scenario_id": "..."}
    # {"type": "SCHEDULE_INFEASIBLE", "conflicts": [...]}

Aggiungi in main.py:
- manager = ConnectionManager() (singleton)
- WebSocket endpoint: GET /ws/{room_id}

TEST (backend/tests/test_reschedule_engine.py):
- test_reschedule_skips_completed: operazione COMPLETED non viene spostata
- test_websocket_broadcast: mock websocket riceve RESCHEDULE_COMPLETE
- test_celery_task_retries: se DB non disponibile, task ritenta max 3 volte

VINCOLI:
- Il task Celery deve essere idempotente (può essere chiamato 2 volte stesso scenario)
- Sessione DB sincrona nel task Celery (non async — Celery non supporta asyncio nativo)
  usa sqlalchemy sync engine separato per i task

OUTPUT ATTESO: reschedule_engine.py, scheduler_orchestrator.py, celery_worker.py,
app/websocket/manager.py, tests/test_reschedule_engine.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 9 — Backend: Tutti gli Endpoint API REST
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezioni "6. STRUTTURA FILE" e lista API endpoints.

STEP 9 — Tutti gli endpoint API REST

Implementa tutti i router FastAPI. Un file per dominio in backend/app/api/routes/.

ROUTER 1: orders.py
GET  /api/orders/machine/{id}/bom-tree → BOMTreeNode (ricorsivo, con figli)
GET  /api/orders/{id} → ProductionOrderRead
GET  /api/orders/{id}/operations → list[OperationRead]
PATCH /api/orders/{id}/status → ProductionOrderRead

ROUTER 2: schedule.py
POST /api/schedule/run → {"task_id": str, "status": "queued"}
  body: ScheduleRunRequest(scenario_id, objective_mode, params_json)
  → triggera reschedule_incremental.delay(scenario_id)
GET  /api/schedule/scenario/{id} → list[ScheduleEntryRead]
GET  /api/schedule/scenario/{id}/gantt-data → list[GanttEntry]
  GanttEntry: {id, operation_id, operation_desc, order_id, order_desc,
               operator_id, operator_name, start, end, status, color, is_critical_path}
POST /api/schedule/scenario/{id}/override-operation
  body: {operation_id, new_start, new_end, operator_id}
  → crea entry con is_manual_override=True, triggera rischedulazione vincoli adiacenti
POST /api/schedule/scenario/{id}/reschedule → {"task_id": str}

ROUTER 3: scenarios.py
GET  /api/scenarios → list[ScheduleScenarioRead]
POST /api/scenarios → ScheduleScenarioRead
GET  /api/scenarios/{id} → ScheduleScenarioRead con KPI calcolati:
  {makespan_days, operators_used, utilization_pct, delayed_operations_count,
   blocked_operations_count, estimated_completion_date}
PUT  /api/scenarios/{id} → ScheduleScenarioRead
POST /api/scenarios/compare → ScenarioComparisonResult
  body: {scenario_a_id, scenario_b_id}
  ScenarioComparisonResult: {delta_makespan_days, delta_operators, delta_utilization,
                              gantt_a: list[GanttEntry], gantt_b: list[GanttEntry]}

ROUTER 4: operators.py
GET  /api/operators → list[OperatorRead]
POST /api/operators → OperatorRead
GET  /api/operators/{id}/calendar → list[OperatorCalendarRead] (periodo opzionale)
PUT  /api/operators/{id}/calendar/{date} → OperatorCalendarRead
POST /api/operators/calendar/bulk-update → {"updated": int}
  body: {operator_ids: list, date_from, date_to, shift_id, is_available}

ROUTER 5: reference_points.py
GET  /api/reference-points/model/{machine_model_id} → list[ReferencePointRead]
PUT  /api/reference-points/{id} → ReferencePointRead
GET  /api/reference-points/model/{id}/precedences → list[ReferencePointPrecedenceRead]
PUT  /api/reference-points/precedences → list[ReferencePointPrecedenceRead]
  body: {machine_model_id, precedences: list[{rp_id, predecessor_ids: list}]}
  → valida DAG prima di salvare, errore 422 se ciclo rilevato

ROUTER 6: delays.py
GET  /api/delays/machine/{id} → list[DelayEventRead]
POST /api/delays → DelayEventRead + triggera reschedule se requires_reschedule=True
PATCH /api/delays/{id}/resolve → DelayEventRead (updated)
GET  /api/delays/{id}/impact → DelayImpactResponse
  DelayImpactResponse: {impacted_entries: list[ScheduleEntryRead],
                         estimated_delta_days: float,
                         critical_path_affected: bool}

ROUTER 7: missing_components.py
GET  /api/missing-components/machine/{id} → list[MissingComponentRead]
POST /api/missing-components → MissingComponentRead
PATCH /api/missing-components/{id}/mark-arrived → MissingComponentRead
  → aggiorna is_arrived=True, triggera reschedule del gruppo
DELETE /api/missing-components/{id} → {"deleted": true}

ROUTER 8: export.py
GET /api/export/scenario/{id}/csv → StreamingResponse (CSV)
  Colonne: scenario, order_id, order_desc, operation_id, operation_desc,
           operator, workcenter, start, end, duration_h, status
GET /api/export/scenario/{id}/json-sap → JSONResponse
  Struttura SAP-ready: {"schedule": [{sap_order_id, sap_operation_id,
                                       resource_id, planned_start, planned_end}]}
GET /api/export/scenario/{id}/pdf → StreamingResponse (PDF)
  Report con: header macchina, KPI scenario, tabella schedule per operatore,
  lista mancanti, lista ritardi attivi (usa WeasyPrint con template HTML inline)

VINCOLI:
- Tutti i router registrati in main.py con prefix /api e tag OpenAPI
- HTTPException con status code corretto (404 se not found, 422 se validazione)
- Paginazione su GET list: ?page=1&size=50
- Logging strutturato su ogni endpoint (request_id, duration_ms)

OUTPUT ATTESO: backend/app/api/routes/*.py tutti implementati,
main.py aggiornato con tutti i router inclusi

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 10 — Frontend: Setup, Routing, Layout, API Client
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "2. STACK TECNOLOGICO, Frontend".

STEP 10 — Frontend base: Vite + React + routing + layout + API client

SETUP:
- Inizializza Vite con template react-ts
- Installa: react-router-dom v6, axios, zustand, recharts, frappe-gantt,
  reactflow, shadcn/ui (init), tailwindcss, @types/*

STRUTTURA PAGINE (src/pages/):
Crea stub per tutte le 10 pagine (solo layout placeholder con titolo h1):
Dashboard, GanttView, BOMExplorer, OperatorCalendar, ReferencePointConfig,
ScenarioManager, DelayManager, MissingComponents, AIAssistant, ExportPage

LAYOUT (src/components/shared/Layout.tsx):
- Sidebar sinistra con navigazione: icona + label per ogni pagina
- Header con: nome progetto, macchina selezionata (dropdown), badge AI suggestions
- Area contenuto principale con breadcrumb
- Tema dark/light toggle
- WebSocket status indicator (verde/rosso)

ROUTING (src/App.tsx):
- React Router con tutte le 10 rotte
- Redirect / → /dashboard
- Layout wrappa tutte le pagine

API CLIENT (src/api/):
- client.ts: axios instance con baseURL da import.meta.env.VITE_API_URL,
  interceptor per error handling uniforme, request ID header
- types.ts: riesporta tutti i tipi TypeScript (speculari agli schemi Pydantic)
  — almeno: MachineOrder, ProductionOrder, Operation, Operator, ScheduleScenario,
  ScheduleEntry, GanttEntry, ReferencePoint, DelayEvent, MissingComponent,
  AiSuggestion, BOMTreeNode
- hooks/: un file per dominio con React Query o SWR per ogni endpoint GET
  (es. useScheduleScenario(id), useBOMTree(machineOrderId), useOperators())

ZUSTAND STORES (src/store/):
- scheduleStore.ts: activeScenarioId, ganttViewMode (BY_OPERATOR|BY_ORDER)
- machineStore.ts: selectedMachineOrderId
- aiStore.ts: suggestions[], chatHistory[], unreadCount
- uiStore.ts: sidebarCollapsed, theme, websocketConnected

WEBSOCKET (src/hooks/useWebSocket.ts):
- Connessione a ws://localhost:8000/ws/{room_id}
- Auto-reconnect con backoff esponenziale
- On message RESCHEDULE_COMPLETE → invalida React Query cache per il scenario
- On message AI_SUGGESTION_NEW → incrementa aiStore.unreadCount

VINCOLI:
- TypeScript strict, zero any
- Tutte le env var con prefisso VITE_
- Tailwind configurato con shadcn/ui preset
- Il layout deve essere responsive (funziona a 1280px+)

OUTPUT ATTESO: frontend/src/ completo con routing, layout, API client,
stores, tutti i page stub

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 11 — Frontend: BOM Explorer
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezioni BOM e mock data.

STEP 11 — BOM Explorer

Implementa src/pages/BOMExplorer.tsx e componenti correlati.

FUNZIONALITÀ:
1. Albero interattivo navigabile (collassa/espandi nodi)
   Gerarchia: Macchina → Macroaggregati → Aggregati → Gruppi → Componenti
   Ogni nodo mostra: codice, descrizione, stato (badge colorato), livello

2. Per ogni nodo non-componente: click → pannello laterale con:
   - Info ordine (sap_order_id, workcenter, status)
   - Lista operazioni con stato e durata pianificata
   - Reference point associato (se presente)
   - Blocchi attivi (componenti mancanti che bloccano)

3. Per ogni nodo componente:
   - Badge "MANCANTE" con data arrivo se in missing_components
   - Toggle inline "Arrivato" → PATCH /api/missing-components/{id}/mark-arrived

4. Filtri nella toolbar:
   - Solo nodi bloccati
   - Solo nodi in ritardo
   - Per workcenter (dropdown)
   - Search per codice/descrizione

5. Header con breadcrumb del percorso corrente nell'albero

COMPONENTI DA CREARE (src/components/bom/):
- BOMTree.tsx: albero ricorsivo che usa BOMTreeNode
- BOMNodeRow.tsx: singola riga dell'albero con indentazione, icona livello, badge stato
- BOMNodeDetail.tsx: pannello laterale con dettaglio nodo
- BOMFilters.tsx: toolbar filtri

COLORI STATO:
- PLANNED: grigio
- IN_PROGRESS: blu
- COMPLETED: verde
- BLOCKED: rosso
- MISSING: arancione

VINCOLI:
- Performance: virtualizzazione se >200 nodi (usa react-virtual o window)
- Lo stato deve aggiornarsi in real-time dopo mark-arrived

OUTPUT ATTESO: src/pages/BOMExplorer.tsx + src/components/bom/*.tsx

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 12 — Frontend: Gantt View (doppia vista)
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "PAGINE FRONTEND, 2. Gantt View".

STEP 12 — Gantt View con doppia vista switchabile

Implementa src/pages/GanttView.tsx e componenti.

VISTA 1 — PER OPERATORE (src/components/gantt/GanttByOperator.tsx):
- Ogni riga = un operatore (nome + skill badge + workcenter)
- Barre = schedule_entries assegnate all'operatore
- Colore barra per operation_type: ELECTRICAL=blu, MECHANICAL=arancione, GENERAL=grigio
- Bordo barra per status: IN_PROGRESS=pulsante, COMPLETED=opaco, INTERRUPTED=tratteggiato,
  DELAYED=rosso, MANUAL_OVERRIDE=bordo giallo
- Riga "COMPONENTI MANCANTI" in fondo: marker rossi con tooltip data arrivo

VISTA 2 — PER ORDINE (src/components/gantt/GanttByOrder.tsx):
- Ogni riga = un ordine di produzione (con indentazione per livello BOM)
- Barre = span totale delle operazioni dell'ordine (min start → max end)
- Badge con % completamento

FUNZIONALITÀ COMUNI:
- Switch vista: toggle button "Per Operatore / Per Ordine"
- Zoom: giorno / settimana / mese (slider o button group)
- Linea verticale "oggi" sempre visibile
- Scroll orizzontale sincronizzato tra header date e righe
- Click su barra → popup con:
  - Dettaglio operazione (cod, desc, durata, tipo, workcenter)
  - Operatore assegnato
  - Stato + motivo interruzione se presente
  - Pulsante "Perché è schedulata così?" → chiama GET /api/ai/explain-entry/{id}
  - Pulsante "Override manuale" → apre modal con datepicker start/end + select operatore

MODAL OVERRIDE:
- DatetimePicker per nuovo start e new end
- Select operatore (filtrato per skill + workcenter compatibili)
- Warning se il nuovo slot viola turni o precedenze
- Conferma → POST /api/schedule/scenario/{id}/override-operation
- Rischedulazione automatica delle op adiacenti

HIGHLIGHT CRITICAL PATH:
- Toggle "Mostra critical path" → barre sul percorso critico evidenziate con bordo dorato

VINCOLI:
- Usa frappe-gantt come base ma wrappalo in un componente React custom
- La doppia vista deve condividere lo stesso zoom e scroll orizzontale
- Performance: con 500+ entry il Gantt non deve andare sotto 30fps

OUTPUT ATTESO: src/pages/GanttView.tsx, src/components/gantt/GanttByOperator.tsx,
src/components/gantt/GanttByOrder.tsx, src/components/gantt/GanttEntryPopup.tsx,
src/components/gantt/OverrideModal.tsx

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 13 — Frontend: Operator Calendar
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "PAGINE FRONTEND, 4. Operator Calendar".

STEP 13 — Operator Calendar

Implementa src/pages/OperatorCalendar.tsx

LAYOUT:
- Sidebar sinistra: lista operatori con filtro per workcenter e skill
  Ogni operatore: avatar iniziali, nome, skill badge, workcenter
- Area principale: calendario mensile per l'operatore selezionato

CALENDARIO MENSILE:
- Griglia 7 colonne (lun-dom), N righe di settimane
- Ogni cella (giorno):
  - Se disponibile: colore del turno (Mattina=verde chiaro, Pomeriggio=giallo, Notte=blu scuro)
  - Se assente: rosso con icona X e motivo (tooltip)
  - Se oggi: bordo evidenziato
  - Click → modal edit giorno

MODAL EDIT GIORNO:
- Dropdown: Assegna turno (Mattina/Pomeriggio/Notte) o "Assente"
- Se Assente: campo note obbligatorio
- Salva → PUT /api/operators/{id}/calendar/{date}
- Bottone "Crea delay_event assenza" (se la data è futura e l'op è sul critical path)

BULK EDIT:
- Seleziona range di date (datepicker from/to)
- Seleziona turno da applicare
- Applica a tutti gli operatori selezionati o solo al corrente
- POST /api/operators/calendar/bulk-update

STATISTICHE OPERATORE (panel in fondo):
- Ore totali nel mese: [n ore]
- % utilizzo su schedule attivo: [n%]
- Operazioni assegnate questo mese: [n]
- Assenze programmate: [n giorni]

VINCOLI:
- Navigazione mese: frecce prev/next + jump a mese specifico
- Il cambio operatore mantiene il mese visualizzato
- Aggiornamenti ottimistici (UI aggiorna prima, rollback se errore)

OUTPUT ATTESO: src/pages/OperatorCalendar.tsx + componenti correlati

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 14 — Frontend: Reference Point Config con DAG live
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "PAGINE FRONTEND, 5. Reference Point Config".

STEP 14 — Reference Point Config

Implementa src/pages/ReferencePointConfig.tsx

LAYOUT SPLIT:
- Metà sinistra: tabelle editabili
- Metà destra: visualizzazione DAG live (React Flow)

TABELLA REFERENCE POINTS (editabile inline):
Colonne: Codice | Nome | Livello Target | Ordine Associato | Azioni
- Edit inline: click su cella → input editabile
- Salva singola riga: PUT /api/reference-points/{id}
- Evidenzia RP senza predecessori (priorità massima) con badge "ROOT"

TABELLA PRECEDENZE:
- Per ogni RP: lista dei suoi predecessori (chips removibili)
- Aggiungi predecessore: dropdown con gli altri RP
- Rimuovi: X sulla chip
- Salva: PUT /api/reference-points/precedences (invia il grafo completo)

DAG VIEWER (React Flow, destra):
- Nodi = reference points, colorati per livello (MACROAGGREGATE=viola, AGGREGATE=teal)
- Archi = precedenze (frecce orientate A→B: A deve essere completato prima di B)
- Layout automatico: dagre-d3 o elk.js per posizionamento gerarchico
- Hover su nodo → tooltip con ordine associato e status attuale
- Click su nodo → evidenzia nella tabella sinistra
- Aggiornamento real-time mentre si editano le precedenze
- Se viene creato un ciclo: nodi del ciclo diventano rossi, alert "Ciclo rilevato!"
  e il salvataggio è bloccato

VALIDAZIONE FRONTEND:
- Prima di inviare PUT precedences: controlla cicli in-browser con DFS
- Se ciclo: mostra quali RP sono coinvolti nel ciclo, non inviare richiesta

VINCOLI:
- React Flow deve avere layout top-down (radici in alto, foglie in basso)
- Il DAG deve essere leggibile con 10+ nodi (auto-layout)

OUTPUT ATTESO: src/pages/ReferencePointConfig.tsx + componenti DAG

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 15 — Frontend: Scenario Manager
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "PAGINE FRONTEND, 6. Scenario Manager".

STEP 15 — Scenario Manager

Implementa src/pages/ScenarioManager.tsx

LISTA SCENARI:
- Card per ogni scenario con: nome, obiettivo, data creazione, KPI (makespan, operatori, completamento stimato)
- Badge "BASELINE" e "ACTIVE" sulle card
- Azioni: Attiva, Duplica, Imposta come Baseline, Elimina
- Pulsante "+ Nuovo Scenario"

MODAL CREA SCENARIO:
- Nome scenario (input)
- Obiettivo (radio buttons):
  - "Finisci entro il [datepicker]" → FINISH_BY_DATE
  - "Massimizza utilizzo risorse" → MAXIMIZE_RESOURCE_UTILIZATION
  - "Minimizza operatori" → MINIMIZE_OPERATORS
  - "Personalizzato [sliders pesi]" → CUSTOM
- Risorse: seleziona quali workcenter includere + quanti operatori per skill
- Pulsante "Crea e Schedula" → POST /api/scenarios + POST /api/schedule/run

CONFRONTO SCENARI (when 2+ scenari esistono):
- Seleziona Scenario A e Scenario B (dropdown)
- Pulsante "Confronta" → POST /api/scenarios/compare
- Risultato: layout affiancato con:
  - KPI comparativi (tabella delta con frecce su/giù)
  - Gantt A vs Gantt B (versione compatta, sola vista per ordine)
  - Sezione AI: pulsante "Analizza differenze con AI" → POST /api/ai/compare-scenarios
    Mostra risposta AI con recommendation esplicita

SIMULAZIONE WHAT-IF:
- Panel "Simula What-If" sul scenario attivo
- Input: "Se aggiungo N operatori [skill] al workcenter [WC]..."
- Pulsante "Stima impatto" → crea scenario temporaneo, run schedule, mostra delta
  senza salvare permanentemente

VINCOLI:
- Polling su task_id per aggiornare stato "In schedulazione..." → "Completato"
- Il confronto Gantt deve essere scrollabile orizzontalmente in sincronia

OUTPUT ATTESO: src/pages/ScenarioManager.tsx + modal e componenti correlati

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 16 — Frontend: Delay Manager + Missing Components
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezioni "7. Delay Manager" e "8. Missing Components".

STEP 16 — Delay Manager e Missing Components

PAGINA DELAY MANAGER (src/pages/DelayManager.tsx):

Lista ritardi attivi:
- Tabella: tipo | entità impattata | dal | al | descrizione | urgenza | azioni
- Filtri: per tipo, per stato (attivo/risolto), per urgenza
- Badge urgenza colorato (CRITICAL=rosso, HIGH=arancione, MEDIUM=giallo, LOW=grigio)

Form nuovo ritardo (modal):
- Tipo: radio (Assenza Operatore / Componente in Ritardo / Operazione Ritardata / Altro)
- Se Assenza Operatore: select operatore + datepicker from/to
- Se Componente in Ritardo: select dal missing_components + nuova data arrivo
- Se Operazione Ritardata: select operazione + minuti ritardo
- Checkbox "Richiede rischedulazione"
- Submit → POST /api/delays + se requires_reschedule: triggera reschedule + AI analyze-delay

Impatto ritardo (per ogni ritardo nella lista):
- Pulsante "Vedi impatto" → GET /api/delays/{id}/impact
- Modal con: lista operazioni impattate (con link al Gantt), delta giorni stimato,
  se il critical path è coinvolto (banner rosso se sì)
- Pulsante "Rischedula considerando questo ritardo" → POST /api/schedule/scenario/{id}/reschedule
- Pulsante "Analizza con AI" → POST /api/ai/analyze-delay → mostra risposta in panel

Risolvi ritardo: PATCH /api/delays/{id}/resolve → spostandolo in "Risolti"

PAGINA MISSING COMPONENTS (src/pages/MissingComponents.tsx):

Lista con colonne:
- Materiale | Descrizione | Ordine | Gruppo | Data Arrivo Prevista | Urgenza | Arrivato

Urgenza calcolata: se data_arrivo <= oggi+2: CRITICO, <= oggi+5: ALTO, <= oggi+10: MEDIO

Filtri: per ordine, per workcenter, per urgenza, "Mostra solo non arrivati"

Toggle "Arrivato":
- Switch nella riga → PATCH /api/missing-components/{id}/mark-arrived
- Mostra toast: "Componente arrivato. Rischedulazione avviata per i gruppi bloccati."
- Triggera automaticamente reschedule del gruppo

Aggiungi mancante (modal):
- Select ordine/gruppo dalla BOM
- Input materiale, descrizione, data arrivo prevista
- POST /api/missing-components

Timeline vista alternativa:
- Vista "Timeline arrivi": asse orizzontale tempo, marker per ogni mancante
- Mostra anche quando le operazioni bloccate potrebbero sbloccarsi

VINCOLI:
- Aggiornamenti real-time via WebSocket per nuovi mancanti
- I toast devono essere non invasivi (angolo basso destra, auto-dismiss 5s)

OUTPUT ATTESO: src/pages/DelayManager.tsx, src/pages/MissingComponents.tsx
+ componenti correlati

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 17 — AI Layer: Claude Client + Prompt Builder + Context Extractor
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "5. AI LAYER".

STEP 17 — AI Layer: moduli core

FILE 1: backend/app/core/ai/claude_client.py

class ClaudeClient:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = "claude-sonnet-4-6"
        self.max_tokens = 2000

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        expect_json: bool = True
    ) -> dict | str:
        """
        Chiama l'API Claude. Se expect_json=True, parsa la risposta come JSON.
        Gestisce retry (max 3) su errori 429/500.
        Logga prompt_tokens e completion_tokens.
        """

    async def chat(
        self,
        messages: list[dict],  # [{"role": "user"|"assistant", "content": str}]
        system: str
    ) -> str:
        """Per la chat multi-turno — ritorna solo il testo"""

FILE 2: backend/app/core/ai/context_extractor.py

class ContextExtractor:
    """Estrae e serializza il contesto DB per i prompt AI."""

    async def get_schedule_context(
        self, scenario_id: UUID, db: AsyncSession
    ) -> dict:
        """
        Ritorna: {
          scenario: {name, objective, target_date},
          machine: {order_id, description, status},
          schedule_summary: {total_ops, completed, in_progress, blocked, delayed},
          critical_path: [op_ids],
          bottlenecks: [{workcenter_id, overloaded_slots}],
          utilization_by_operator: {op_id: pct},
          active_delays: [delay summaries],
          missing_components: [missing summaries],
          available_operators: {workcenter: {skill: count}}
        }
        Max 4000 token di contesto — tronca liste se necessario.
        """

    async def get_delay_context(
        self, delay_id: UUID, db: AsyncSession
    ) -> dict: ...

    async def get_entry_context(
        self, entry_id: UUID, db: AsyncSession
    ) -> dict:
        """
        Per explain-entry: include i vincoli attivi che hanno determinato
        start/end/operatore (precedenze attive, turni, altre ops dell'operatore quel giorno)
        """

FILE 3: backend/app/core/ai/prompt_builder.py

SYSTEM_PROMPT_BASE = """
Sei un esperto di production scheduling per macchine industriali complesse.
Hai accesso al contesto completo dello schedule corrente.
Rispondi sempre in italiano. Sii preciso e conciso.
Quando suggerisci azioni, sii specifico: nomina le operazioni, gli operatori e le date.
"""

def build_optimize_prompt(context: dict) -> str: ...
def build_delay_analysis_prompt(delay_context: dict, schedule_context: dict) -> str: ...
def build_chat_system_prompt(schedule_context: dict) -> str: ...
def build_compare_scenarios_prompt(ctx_a: dict, ctx_b: dict, objective: str) -> str: ...
def build_history_analysis_prompt(historical_data: dict) -> str: ...
def build_explain_entry_prompt(entry_context: dict) -> str: ...

FILE 4: backend/app/core/ai/response_parser.py

def parse_optimize_response(raw: dict) -> AiSuggestionCreate: ...
def parse_delay_response(raw: dict) -> DelayImpactAiResponse: ...
def parse_chat_response(raw: str) -> ChatResponse: ...
def parse_compare_response(raw: dict) -> ScenarioCompareAiResult: ...
def parse_explain_response(raw: str) -> str: ...
# Ogni parser valida i campi obbligatori e fa fallback graceful se mancanti

Implementa anche gli endpoint AI in backend/app/api/routes/ai.py:
- POST /api/ai/optimize-schedule
- POST /api/ai/analyze-delay
- POST /api/ai/compare-scenarios
- POST /api/ai/analyze-history
- GET  /api/ai/explain-entry/{entry_id}
- GET  /api/ai/suggestions/{scenario_id}
- GET  /api/ai/suggestions/proactive/{machine_order_id}
- PATCH /api/ai/suggestions/{id}/accept
- PATCH /api/ai/suggestions/{id}/reject

VINCOLI:
- Tutti i prompt rispettano il limite 4000 token di contesto (tronca se necessario)
- Il client Claude ha timeout di 30s — non blocca il thread FastAPI (usa asyncio)
- Salva sempre la risposta in ai_suggestions prima di ritornare al client

OUTPUT ATTESO: backend/app/core/ai/*.py, backend/app/api/routes/ai.py

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 18 — AI Layer: Analisi Proattiva Post-Scheduling
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "5. AI LAYER, MODALITÀ 2".

STEP 18 — AI proattiva post-scheduling

Implementa backend/app/core/ai/proactive_analyzer.py

@celery_app.task
def analyze_proactive_after_schedule(scenario_id: str):
    """
    Invocato automaticamente da reschedule_incremental al completamento.
    Analizza il nuovo schedule e genera suggerimenti proattivi.
    """
    # 1. Estrai contesto completo del nuovo schedule
    # 2. Calcola metriche: utilization per operatore, carico per workcenter per settimana
    # 3. Identifica:
    #    a. Operatori con utilizzo > 90% in qualsiasi settimana → "Operatore X sovraccarico"
    #    b. Workcenter con > N operazioni in parallelo in qualsiasi giorno → "Picco WC-Y"
    #    c. Mancanti sul critical path (da solution_extractor.find_critical_path)
    #    d. Se target_finish_date esiste: verifica se makespan supera target
    #    e. Operatori con utilizzo < 30% → potenziale riassegnazione

    # 4. Se ci sono problemi rilevati (a, b, c, d):
    #    Costruisci prompt con context_extractor + prompt_builder
    #    Chiama claude_client.complete()
    #    Salva in ai_suggestions con suggestion_type=PROACTIVE

    # 5. Notifica WebSocket: AI_SUGGESTION_NEW con count nuovi suggerimenti

LOGICA DETECTION INTERNA (senza Claude, per velocità):
Implementa anche rilevamento rule-based prima di chiamare Claude:
- Rule 1: operatore con >90% utilizzo → genera suggerimento senza Claude
  "Operatore {nome} ha utilizzo {pct}% nella settimana {date_range}. Considera di redistribuire."
- Rule 2: mancante su critical path → priorità CRITICAL senza Claude
  "Il componente {mat} (arrivo {date}) è sul critical path. Ritardo impatta makespan di ~{days} giorni."
- Se regole trovano problemi gravi: chiama anche Claude per suggerimenti di mitigazione

VINCOLI:
- Task deve completare in < 60s (usa timeout Claude)
- Non deve bloccare la risposta allo scheduler (è completamente asincrono)
- Genera max 5 suggerimenti proattivi per run (evita spam)

OUTPUT ATTESO: backend/app/core/ai/proactive_analyzer.py aggiornato,
reschedule_engine.py aggiornato per chiamare il task

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 19 — AI Layer: Chat Multi-turno + Chat Session Manager
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "5. AI LAYER, MODALITÀ 4".

STEP 19 — Chat multi-turno e session manager

FILE 1: backend/app/core/ai/chat_session_manager.py

class ChatSessionManager:
    MAX_HISTORY_MESSAGES = 20  # Mantieni ultimi 20 messaggi per non sforare context

    async def get_or_create_session(
        self, machine_order_id: UUID, scenario_id: UUID | None, db: AsyncSession
    ) -> AiChatSession:
        """Recupera sessione esistente o crea nuova"""

    async def add_message(
        self, session_id: UUID, role: str, content: str, db: AsyncSession
    ) -> None:
        """Aggiunge messaggio alla history, tronca se > MAX_HISTORY_MESSAGES"""

    async def build_messages_for_api(
        self, session: AiChatSession
    ) -> list[dict]:
        """Ritorna [{"role": "user"|"assistant", "content": str}] per API Claude"""

    async def clear_session(self, session_id: UUID, db: AsyncSession) -> None: ...

FILE 2: aggiorna backend/app/api/routes/ai.py con endpoint chat:

POST /api/ai/chat
  body: {machine_order_id, scenario_id?, message: str, session_id?}
  Steps:
  1. Recupera/crea sessione
  2. Estrai contesto schedule corrente (context_extractor)
  3. Costruisci system prompt con contesto (prompt_builder.build_chat_system_prompt)
  4. Aggiungi messaggio utente alla history
  5. Chiama claude_client.chat(messages, system)
  6. Aggiungi risposta alla history
  7. Parsa risposta (parse_chat_response)
  8. Ritorna: {session_id, message, action_type, data?, apply_actions?}

DELETE /api/ai/chat/{session_id} — resetta la conversazione

GESTIONE INTENT SPECIALI nel chat:
Nel system prompt includi istruzioni per riconoscere:
- "Genera report" → action_type=REPORT, data={report_text: "..."}
- "Simula se..." → action_type=SIMULATION, data={impact: "...", delta_days: N}
- "Perché..." → action_type=INFO
- "Suggerisci..." → action_type=SUGGESTION, apply_actions=[...]

VINCOLI:
- Session_id opzionale: se non fornito, usa la sessione più recente dello scenario
- Il contesto schedule viene reinserito nel system prompt ad ogni chiamata
  (Claude non ha memoria tra chiamate, il contesto deve essere sempre presente)
- Streaming non necessario per ora (risposta completa)

OUTPUT ATTESO: chat_session_manager.py, ai.py aggiornato con endpoint chat

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 20 — Frontend: AI Assistant Panel completo
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "PAGINE FRONTEND, 9. AI Assistant Panel".

STEP 20 — AI Assistant Panel

Implementa src/pages/AIAssistant.tsx come pagina dedicata E come sidebar globale.

SIDEBAR GLOBALE (src/components/shared/AISidebar.tsx):
- Accessibile da qualsiasi pagina tramite pulsante fisso in basso a destra
- Pannello slide-over 400px larghezza
- Mostra badge con contatore suggerimenti non letti
- Contenuto: tab "Chat" e tab "Suggerimenti"

TAB CHAT:
- Area messaggi scrollabile con bolle: utente (destra, blu) e AI (sinistra, grigio)
- Typing indicator mentre AI risponde
- Input textarea + pulsante Invia (Ctrl+Enter per inviare)
- Pulsante "Nuova conversazione" (cancella history)
- Se action_type=SUGGESTION: mostra bottoni azione sotto la risposta
  Es. "Applica: Sposta OP-045 a martedì" → click → chiama l'azione e aggiorna schedule
- Se action_type=REPORT: mostra pulsante "Scarica report" → blob download

TAB SUGGERIMENTI (src/components/ai/SuggestionsList.tsx):
- Lista suggerimenti proattivi con: tipo badge, testo, confidence score (barra), data
- Per ogni suggerimento: pulsante "Applica" e pulsante "Ignora"
  Applica → esegue le suggested_actions via API, poi PATCH /accept
  Ignora → PATCH /reject
- Filtri: tutti / alta priorità / non letti
- "Marca tutti come letti" button

PAGINA DEDICATA (src/pages/AIAssistant.tsx):
- Vista espansa del pannello AI
- Storico conversazioni: lista sessioni con preview primo messaggio
- Storico suggerimenti con filtri avanzati (per tipo, per data, per accepted/rejected)
- Panel "Analisi storico": pulsante "Analizza pattern storici" → POST /api/ai/analyze-history
  mostra risposta formattata

VINCOLI:
- La sidebar non deve bloccare l'interazione con la pagina sottostante
- I suggerimenti "Applica" devono mostrare conferma prima di eseguire
- Aggiornamento real-time badge via WebSocket (AI_SUGGESTION_NEW)

OUTPUT ATTESO: src/components/shared/AISidebar.tsx,
src/components/ai/SuggestionsList.tsx, src/pages/AIAssistant.tsx

NON procedere oltre questo step.
```

---

## ═══════════════════════════════════════════════════
## STEP 21 — Export Endpoints + Dashboard finale + README
## ═══════════════════════════════════════════════════

```
@workspace Leggi .github/copilot-instructions.md — sezione "PAGINE FRONTEND, 1. Dashboard e 10. Export".

STEP 21 — Export, Dashboard KPI e README

PARTE A — Export endpoints (già stub nello step 9, ora implementa):

GET /api/export/scenario/{id}/csv:
- Genera CSV con colonne: scenario_name, sap_order_id, order_description,
  sap_operation_id, operation_description, operation_type, operator_name,
  operator_skill, workcenter, scheduled_start (ISO), scheduled_end (ISO),
  duration_hours, status, delay_minutes
- StreamingResponse con Content-Disposition: attachment; filename=schedule_{id}.csv

GET /api/export/scenario/{id}/json-sap:
- JSON strutturato SAP-ready:
  {
    "export_timestamp": ISO,
    "scenario": {name, objective, machine_order},
    "schedule": [
      {
        "sap_order_id": str,
        "sap_operation_id": str,
        "resource_id": str,          # employee_id dell'operatore
        "workcenter_code": str,
        "planned_start": ISO,
        "planned_end": ISO,
        "duration_minutes": int,
        "status": str
      }
    ]
  }

GET /api/export/scenario/{id}/pdf:
- Report HTML→PDF via WeasyPrint con:
  - Header: logo placeholder + nome macchina + data export + scenario
  - Sezione KPI: makespan, % completamento, operatori, data fine stimata
  - Tabella schedule raggruppata per operatore
  - Sezione mancanti (tabella)
  - Sezione ritardi attivi
  - Footer con pagina N/M

PARTE B — Dashboard KPI finale (src/pages/Dashboard.tsx):

KPI CARDS in alto (4 cards):
- Data fine stimata con delta vs target (verde/rosso)
- % operazioni completate (progress ring)
- Operatori attivi oggi
- Componenti mancanti critici (arrivo < 3gg)

PROGRESS BARS per livello BOM:
- Macroaggregati: barra % completamento per ognuno
- Click → naviga a BOM Explorer filtrato su quel macroaggregato

GANTT PREVIEW (mini gantt, settimana corrente):
- Versione compatta del GanttByOperator per la settimana corrente
- Link "Vedi Gantt completo"

ALERTS PANEL:
- Lista alert ordinati per urgenza: ritardi, mancanti critici, operatori sovraccarichi
- Ogni alert ha Quick Action (es. "Rischedula ora", "Vedi impatto")
- Suggerimenti AI in arrivo (se badge > 0): "Hai N suggerimenti AI — Visualizza"

TIMELINE PROSSIMI EVENTI:
- Lista cronologica: prossimi componenti in arrivo, prossime operazioni critiche,
  prossime assenze operatori, scadenze di scenario

PARTE C — README.md nella root:
- Descrizione progetto
- Prerequisiti (Docker, Python 3.12, Node 20)
- Quick start: docker-compose up --build + seed
- Struttura progetto (breve)
- Come aggiungere un nuovo scenario
- Come collegare SAP reale (sezione "Future integrations")
- Variabili d'ambiente documentate

EXPORT PAGE (src/pages/ExportPage.tsx):
- Dropdown seleziona scenario
- Anteprima dati: tabella prime 10 righe dello schedule
- 3 pulsanti download: CSV, JSON SAP, PDF
- Indicatore formato: "Pronto per importazione SAP" accanto al JSON
- Log export: lista ultimi export effettuati con timestamp e formato

VINCOLI:
- Il PDF non deve superare 5MB per schedule di ~500 operazioni
- Il CSV deve essere encodato UTF-8 con BOM (per Excel italiano)
- La Dashboard deve aggiornarsi ogni 30s automaticamente (polling leggero)

OUTPUT ATTESO: export.py completato, src/pages/Dashboard.tsx finale,
src/pages/ExportPage.tsx, README.md

NON procedere oltre questo step.
--- PROGETTO COMPLETATO ---
```
