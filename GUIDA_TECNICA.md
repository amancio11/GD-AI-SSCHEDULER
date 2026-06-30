# GD Scheduler — GUIDA TECNICA
> Istruzioni persistenti per AI assistant. Leggere integralmente prima di toccare qualsiasi file.
> Versione rigenerata e riconciliata — sostituisce tutte le versioni precedenti del documento.

---

## ⚠️ AGGIORNAMENTO 2026-06-26 — Motore di scheduling: da CP-SAT a capacità di gruppo

Il motore di scheduling è stato **riscritto**. Il vecchio modello CP-SAT a segmenti
(un intervallo opzionale per ogni coppia *operatore × slot di calendario*, con `Σ size == durata` +
due `NoOverlap`) si è rivelato **intrattabile**: su 240+ operazioni il solver non trovava nemmeno una
soluzione fattibile in 60s (UNKNOWN), per via della simmetria degli operatori identici e del NoOverlap
sui segmenti.

**Nuovo modello (attuale) — CP-SAT cumulativo a capacità di gruppo:**
- **`backend/app/core/scheduler/capacity_cpsat.py`** — motore CP-SAT cumulativo a minuti. Ogni operazione
  → pochi segmenti opzionali (`Σ size == durata`, `NoOverlap` per-op = una risorsa alla volta); ogni
  gruppo → **`AddCumulative`** con capacità = `count`; calendario via intervalli bloccanti; obiettivo
  `Minimize(makespan)` / `makespan ≤ target`. Risorse = tipi `(workcenter, skill, ore/giorno, count)`,
  non individui → niente simmetria, niente NoOverlap per-operatore.
- **`backend/app/core/scheduler/capacity_scheduler.py`** — euristica greedy *di supporto*: gira prima per
  dare **orizzonte stretto + warm-start** al CP-SAT, e fa da **fallback** se il solver va in timeout.
- Il vecchio `cpsat_model_builder.py` (segmenti per-operatore) **non è più il motore**.
- **Risorse = tipi configurabili**, non individui: tabella **`resource_types`**
  `(workcenter_id, skill, daily_capacity_hours, count)`. Capacità di gruppo = `count × ore/giorno`
  (due risorse 8h → 16h/giorno). API CRUD: `/api/resource-types`.
- **Una risorsa alla volta per operazione** (max ore/giorno = capacità di una singola risorsa); le op
  lunghe si spalmano su più giorni o passano a un'altra risorsa (hand-off).
- **`schedule_entries.operator_id` è nullable**; aggiunto **`resource_type_id`**. Il Gantt raggruppa
  per gruppo risorsa (workcenter · skill), non per nome operatore.
- Orizzonte **slegato** dal calendario (solo bound anti-loop). Precedenze BOM (parent-wait + RP order)
  **invariate**.

Le sezioni CP-SAT/segmenti più sotto sono **storiche** e vanno lette in quest'ottica.

---

## 1. DOMINIO

Sistema di **Production Scheduling** per il montaggio di macchine industriali complesse (mock: TURBOPRESS-X500). Si integra in modo mock con SAP DM / SAP ERP.

### Gerarchia BOM

```
MachineOrder (ORD-MACH-001)
  └── ProductionOrder MACHINE
        ├── MA-001 "Gruppo Idraulico"     [MACROAGGREGATE]
        │     ├── AGG-001 "Cilindro Principale"  [AGGREGATE]
        │     │     ├── GRP-001 "Kit Guarnizioni Cilindro"  [GROUP]
        │     │     │     ├── COMP-xxx  [COMPONENT, acquisto]
        │     │     │     └── ...
        │     │     └── GRP-002 "Gruppo Pistoni"
        │     ├── AGG-002 "Pompa Olio"
        │     ├── AGG-003 "Collettore"
        │     ├── AGG-004 "Accumulatore"
        │     └── AGG-005 "Filtro Idraulico"
        ├── MA-002 "Quadro Elettrico"     [MACROAGGREGATE]
        │     ├── AGG-006..AGG-009
        └── MA-003 "Struttura Portante"   [MACROAGGREGATE]
              ├── AGG-010..AGG-012
```

- **Componenti**: solo acquisto o produzione non tracciata. No routing, no operazioni. Verificati solo per mancanza.
- **Gruppi**: hanno routing + operazioni, ma i figli sono componenti → **nessun Reference Point**.
- **Aggregati e Macroaggregati**: hanno routing + operazioni **con Reference Point** che puntano ai loro figli diretti nella BOM.
- **Ordine Macchina**: ha routing + operazioni **con Reference Point** che puntano ai 3 macroaggregati.
- Routing mode: **SIMULTANEOUS** — tutte le operazioni di un routing possono andare in parallelo.
- Le operazioni possono essere interrotte e riprese (`progress_pct`).

### Reference Point — logica corretta (v2)

Ogni ordine non-componente ha RP che puntano **solo ai propri figli diretti nella BOM**:

| Livello | RP puntano a |
|---|---|
| MACHINE | I 3 macroaggregati (MA-001, MA-002, MA-003) |
| MACROAGGREGATE MA-001 | I 5 aggregati figli (AGG-001..005) |
| MACROAGGREGATE MA-002 | I 4 aggregati figli (AGG-006..009) |
| MACROAGGREGATE MA-003 | I 3 aggregati figli (AGG-010..012) |
| AGGREGATE AGG-xxx | I propri gruppi figli |
| GROUP | **Nessun RP** — figli sono componenti senza routing |

La tabella `reference_point_precedences` definisce un DAG **intra-livello**: gli archi esistono solo tra RP dello stesso livello padre. Es. nel livello MACHINE: MA-003 (struttura) → MA-001 (idraulico) e MA-003 → MA-002 (elettrico).

**Semantica**: l'operazione dell'ordine padre con `reference_point_id = RP-X` non può iniziare finché l'ordine target del RP-X **e tutti i suoi figli BOM ricorsivamente** non sono completati.

### Operatori

- Skill fissa: `ELECTRICAL | MECHANICAL | MULTI`
- Workcenter fisso: non si spostano tra officine
- ELECTRICAL → solo operazioni ELECTRICAL nel proprio WC
- MECHANICAL → solo operazioni MECHANICAL nel proprio WC
- MULTI → tutti i tipi nel proprio WC
- 20 operatori: WC-MILANO (8), WC-TORINO (7), WC-BERGAMO (5)

---

## 2. STACK TECNOLOGICO

### Backend
- Python 3.12, FastAPI + Uvicorn
- OR-Tools CP-SAT (`ortools.sat.python.cp_model`)
- SQLAlchemy 2.x async (asyncpg), Alembic, PostgreSQL 16 **portable** (no Docker, no servizi Windows)
- Celery 5.x + Redis 7 (broker + backend)
- WebSocket nativo FastAPI
- Anthropic SDK → `claude-sonnet-4-6`
- networkx per DAG e per il CPM (vedi sezione 9)

### Frontend
- React 18 + TypeScript strict (zero `any`)
- Vite 5, shadcn/ui + Tailwind CSS
- Zustand (state), Recharts (KPI), frappe-gantt (Gantt), React Flow (DAG viz)
- axios con typed API layer

### Infrastruttura
- **NO Docker** — PostgreSQL portable (ZIP), Redis portable
- Alembic usa psycopg2 sync (non asyncpg) per evitare problemi con ENUM
- `.env` per tutte le variabili, mai hardcodate

---

## 3. DATABASE — 20 tabelle

```
machine_models          id, code, name, description
machine_orders          id, sap_order_id, machine_model_id FK, description, status, workcenter_id FK, created_at
production_orders       id, sap_order_id, parent_order_id FK(self nullable), parent_material,
                        machine_order_id FK, level ENUM(MACHINE|MACROAGGREGATE|AGGREGATE|GROUP|COMPONENT),
                        material_code, description, quantity, unit, workcenter_id FK,
                        progress_pct FLOAT, status ENUM(PLANNED|IN_PROGRESS|COMPLETED|BLOCKED|MISSING),
                        missing_arrival_date, is_purchase_component, is_production_component_untracked, created_at
z_orders_link           id, child_order_id FK, parent_order_id FK, parent_material, child_material, level, link_type
routings                id, production_order_id FK UNIQUE, sap_routing_id, execution_mode ENUM(SIMULTANEOUS)
operations              id, routing_id FK, sap_operation_id, sequence_number, description,
                        operation_type ENUM(ELECTRICAL|MECHANICAL|GENERAL), workcenter_id FK,
                        planned_duration_minutes INT, actual_duration_minutes INT nullable,
                        progress_pct FLOAT, status ENUM(PENDING|IN_PROGRESS|COMPLETED|BLOCKED|INTERRUPTED),
                        reference_point_id FK nullable, can_be_interrupted BOOL
reference_points        id, code, name, machine_model_id FK,
                        target_level ENUM(MACROAGGREGATE|AGGREGATE|GROUP),  ← include GROUP dalla migration 002
                        target_order_material
reference_point_precedences   id, reference_point_id FK, predecessor_reference_point_id FK, machine_model_id FK
workcenters             id, code, name, location, description, is_active
skill_workcenter_mapping      id, skill, workcenter_id FK, can_do_electrical, can_do_mechanical, can_do_general
operators               id, employee_id, full_name, skill, workcenter_id FK, is_active
shifts                  id, name, start_time, end_time, break_duration_minutes, is_active
operator_calendar       id, operator_id FK, date, shift_id FK nullable, is_available, notes, override_reason
missing_components      id, production_order_id FK, component_material, description,
                        expected_arrival_date, is_arrived, arrival_confirmed_date, manually_flagged, notes
schedule_scenarios      id, machine_order_id FK, name, objective_mode,
                        start_date DATE nullable,           ← NUOVA (migration 005): data di partenza scheduling
                        target_finish_date DATE nullable,   vincolo hard FINISH_BY_DATE
                        resource_set_json, is_active, is_baseline, ai_explanation,
                        last_run_status, last_run_at, last_run_makespan_days,
                        last_run_operators_used, last_run_conflicts, created_at
schedule_entries        id, scenario_id FK, operation_id FK, operator_id FK, workcenter_id FK,
                        scheduled_start, scheduled_end, actual_start, actual_end,
                        status ENUM(SCHEDULED|IN_PROGRESS|COMPLETED|INTERRUPTED|DELAYED|STALE),
                        interruption_reason, delay_minutes INT, is_manual_override BOOL
delay_events            id, machine_order_id FK, event_type ENUM(OPERATOR_ABSENCE|COMPONENT_DELAY|
                        MANUAL_OPERATION_DELAY|OTHER), affected_entity_id, affected_entity_type,
                        delay_from, delay_until, description, reported_at, requires_reschedule
ai_suggestions          id, scenario_id FK nullable, machine_order_id FK, suggestion_type, content_json,
                        confidence_score, accepted, created_at
ai_chat_sessions        id, scenario_id FK nullable, machine_order_id FK, messages_json, created_at, last_activity
operation_status_audit  id, entity_type, entity_id, old_status, new_status, is_unusual,
                        delay_minutes, reschedule_urgency, audit_message, warnings_json,
                        triggered_by, created_at          ← NUOVA (migration 003, vedi sezione 9)
```

**Enum `targetlevel`**: migration `001` lo crea con `MACROAGGREGATE, AGGREGATE`. Migration `002` aggiunge `GROUP` (`ALTER TYPE targetlevel ADD VALUE IF NOT EXISTS 'GROUP'`).

**Campo `start_date`** (migration `005`): data di partenza dello scheduling per ogni scenario. Se `NULL`, il `reschedule_engine` usa `date.today()` come fallback. Questo è il punto zero dell'epoch CP-SAT — tutte le variabili di tempo sono minuti relativi a questo instante.

---

## 4. DATI MOCK (seed.py — v2 corretta)

File: `backend/app/db/seed.py`

- UUID deterministici: `uuid.uuid5(NS, name)` — safe da rieseguire
- `random.seed(42)` una sola volta in `main()`
- Idempotente: `INSERT ... ON CONFLICT DO NOTHING`
- Usa asyncpg direttamente (non SQLAlchemy) per semplicità

### Struttura RP nel seed (v2 — CORRETTA)

55 Reference Point totali, organizzati per livello:

```
Livello MACHINE:   RP-M-01 → MA-003, RP-M-02 → MA-001, RP-M-03 → MA-002
Livello MA-001:    RP-MA1-01..05 → AGG-001..005
Livello MA-002:    RP-MA2-01..04 → AGG-006..009
Livello MA-003:    RP-MA3-01..03 → AGG-010..012
Livello AGG-001:   RP-A001-01..03 → GRP-001..003
Livello AGG-002:   RP-A002-01..04 → GRP-004..007
... (tutti gli aggregati hanno RP verso i loro gruppi)
Livello AGG-012:   RP-A012-01..02 → GRP-039..040
```

43 archi DAG, tutti intra-livello, verificati aciclici.

**NON usare** codici nella forma `RP-XXX` (vecchi RP-001..010, eliminati nella v2).

### Operazioni nel seed

- Ordine MACHINE: **3 operazioni** (una per macroaggregato), ciascuna con il RP del livello macchina
- Macroaggregati: N operazioni dove N = numero di aggregati figli, ciascuna con il RP corrispondente
- Aggregati: N operazioni dove N = numero di gruppi figli, ciascuna con il RP corrispondente
- Gruppi: 3-6 operazioni random, **nessun RP**

### Componenti mancanti (5 pre-settati)

```
VLV-2200  "Valvola idraulica"     → oggi +7gg
CAB-450   "Cavo elettrico 25mm²"  → oggi +3gg
SEN-P100  "Sensore pressione"     → oggi +12gg
VIT-M16   "Vite speciale M16x80" → oggi +1gg
GUA-200   "Guarnizione gomma"     → oggi +5gg
```

### Calendario operatori

28 giorni, turno rotante (Mattina/Pomeriggio/Notte), ~8 assenze random.

---

## 5. SCHEDULER — Struttura file

```
backend/app/core/scheduler/
  cpsat_types.py            SchedulableOperation, QualifiedOperator, SegmentVars, CpsatVariables, CpsatSolution
  cpsat_model_builder.py    CpsatModelBuilder con build_and_solve() — modello a segmenti (v2)
  dag_builder.py            build_precedence_dag(), get_scheduling_order()
  shift_preprocessor.py     build_operator_available_slots(), build_unavailable_intervals()
  reschedule_engine.py      Celery task reschedule_incremental (ENTRY POINT)
  solution_extractor.py     LEGACY/non usato — l'estrazione è in CpsatModelBuilder._extract_entries()
  infeasibility_analyzer.py Spiega INFEASIBLE in italiano

backend/app/core/state_engine/    ← NUOVO (vedi sezione 9)
  transitions.py             State machine pura (Operation/ScheduleEntry) + rollup BOM
  cpm_analyzer.py             Critical Path Method: early/late start-finish, total float
  order_status_rollup.py      Propagazione stato bottom-up lungo la BOM (DB)
  delay_propagation.py        Orchestratore: DelayEvent automatico + rollup + reschedule
  models_audit.py             Modello OperationStatusAudit

backend/app/core/ai/
  claude_client.py            Wrapper SDK Anthropic, retry, timeout 30s
  context_extractor.py        Serializza contesto DB per i prompt (max ~4000 token)
  prompt_builder.py           6 builder di prompt, uno per modalità AI
  response_parser.py          Parsing + validazione JSON response, fallback graceful
  proactive_analyzer.py       Analisi proattiva post-scheduling (rule-based + Claude)
  chat_session_manager.py     History multi-turno (ai_chat_sessions, max 20 messaggi)
```

---

## 6. CP-SAT — STATO REALE DEI VINCOLI E DELL'OBIETTIVO

> Questa sezione descrive il **modello a segmenti (v2)** introdotto per rendere il
> calendario degli operatori un vincolo hard e per consentire l'**hand-off** di
> un'operazione tra operatori e turni. Il documento di riferimento dettagliato è
> `SCHEDULER_CONSTRAINTS.md`. Vedi 6.4b per cosa è cambiato rispetto alla v1.

### 6.1 Riepilogo vincoli CP-SAT

| # | Vincolo | Metodo | Stato |
|---|---|---|---|
| 1 | Tutto il lavoro di un'op è allocato sui segmenti (`Σ size == residual`); può distribuirsi su più operatori/turni | `_create_variables()` | ✅ Implementato |
| 2 | Ogni segmento vive dentro il suo slot-turno (calendario = vincolo **hard**) | `_create_variables()` (domini) | ✅ Implementato |
| 3 | Un operatore non fa due segmenti contemporaneamente | `_add_resource_nooverlap_constraints()` | ✅ Implementato |
| 4 | Un'operazione è lavorata da un solo operatore alla volta (hand-off sequenziale) | `_add_resource_nooverlap_constraints()` | ✅ Implementato |
| 5 | Precedenze dirette op→op (`precedence_pairs`) | `_add_precedence_constraints()` | ✅ Implementato |
| 6 | **Tipo B**: ordinamento intra-livello via DAG RP | `_add_rp_order_constraints()` | ✅ Implementato |
| 7 | **Tipo A**: op padre aspetta figlio target (per ogni RP) | `_add_parent_wait_constraints()` | ✅ Implementato |
| 8 | Op bloccata finché componente mancante non arriva | `_add_missing_component_constraints()` | ✅ Implementato |

### 6.2 Vincolo turni — RISOLTO con la decomposizione a segmenti

Il calendario è ora un vincolo **strutturale**. Ogni operazione è decomposta in
*segmenti* (uno per slot-turno candidato); ogni segmento ha `start, end ∈
[slot_start, slot_end]` per costruzione, quindi nessun lavoro può cadere in pausa,
fuori turno o in un giorno di assenza. Non esiste più il "forward pass" post-solve:
`op_end` è la fine wall-clock esatta.

```python
# Cuore del modello (per ogni op):
Σ size[seg] == work_residual                  # tutto il lavoro svolto
seg.start, seg.end ∈ [slot_start, slot_end]   # turni = hard
seg.present ⇒ size ≥ 1 ; ¬present ⇒ size = 0
op_start = min present seg.start ; op_end = max present seg.end
```

**Hand-off tra operatori/turni.** I segmenti di una stessa operazione possono
appartenere a operatori diversi e turni diversi: una lavorazione lunga può essere
**iniziata da un operatore in un turno e completata da un altro in un turno
successivo**. Il no-overlap per-operazione garantisce che l'hand-off sia
*sequenziale* (niente parallelismo fantasma che dimezzerebbe la durata).

**Quando si spezza un'operazione (in parole semplici).** La segmentazione **non è
decisa a priori: la decide il solver**. In fase di build creo solo segmenti
*potenziali* (una casella per ogni operatore-qualificato × slot); il solver accende
quelli che servono per soddisfare `Σ size == durata`. Quindi:
- se il lavoro entra in **un'unica finestra continua → un solo segmento** (nessuno
  spezzettamento);
- l'op si spezza **solo se non ci sta**: il turno finisce (fine turno/pausa/assenza)
  prima del completamento e il resto trabocca nello slot successivo, eventualmente di
  un altro operatore. Es.: 400 min con A (mattina, 225) e B (pomeriggio, 225) → 225 su
  A + 175 su B; ma 200 min → tutto su A, intero.

Vedi anche `SCHEDULER_CONSTRAINTS.md` §3c (quando si spezza) e §13.1 (frammentazione
estetica delle op non critiche).

**Operazione non schedulabile.** Se un'op non ha alcuno slot candidato (nessun
operatore qualificato/disponibile), il modello aggiunge un vincolo contraddittorio
→ scenario `INFEASIBLE` con messaggio in `conflicts`.

**Pruning** (`_candidate_slots` + `_compute_est`): per ogni op si tengono solo gli
slot più vicini la cui capacità cumulata raggiunge `SLOT_CAPACITY_FACTOR ×
work_residual` (default 4×, env `CPSAT_SLOT_CAPACITY_FACTOR`), a partire da un lower
bound `est[op]` ottenuto col longest-path sul DAG delle dipendenze.

### 6.3 No-overlap risorse — `_add_resource_nooverlap_constraints()`

Due famiglie di `AddNoOverlap` sugli `interval` dei segmenti:

```python
# per operatore: 1 segmento alla volta
AddNoOverlap([seg.interval for seg in segmenti_di(operatore)])

# per operazione: 1 operatore alla volta (hand-off sequenziale)
AddNoOverlap([seg.interval for seg in segmenti(op)])
```

### 6.4 Obiettivo CP-SAT — `_set_objective()`

```python
FINISH_BY_DATE:
    makespan = max(op_end);  Minimize(makespan)
    if "target_finish_minutes" in params:
        model.Add(makespan <= target_finish_minutes)   # vincolo hard attivo

MINIMIZE_OPERATORS:
    # minimizza sum(operator_used_bool); used = OR(assign[*, oper])

MAXIMIZE_RESOURCE_UTILIZATION:
    # mappato su minimize makespan (l'utilizzo è costante: Σ size == residual)

CUSTOM:
    # w_makespan*makespan + w_operators*operatori ; default {makespan:0.6, operators:0.4}
```

`assign[(op, oper)] = OR(present)` dei segmenti dell'operatore su quell'op (reificato
con `AddMaxEquality`), usato solo dagli obiettivi che contano gli operatori.

Logica `stop_after_first_solution` in `build_and_solve` invariata: `False` se c'è un
obiettivo (il solver ottimizza davvero), `True` solo per pura soddisfacibilità.

**Vincolo hard `target_finish_minutes` attivo**: il solver rifiuta soluzioni che
sforano la `target_finish_date`; se impossibile → `INFEASIBLE`.

### 6.4b Cosa è cambiato rispetto alla v1 (modello a singolo operatore)

| v1 (superata) | v2 (attuale) |
|---|---|
| `assign==1`: esattamente 1 operatore per op | `Σ size == residual`: lavoro distribuibile su più operatori/turni (hand-off) |
| Durata wall-clock **stimata** (`wc_span` da `ref_start` fisso) | `op_end` **esatto** (max degli end dei segmenti) |
| Turni rispettati da `_slot_aware_forward_pass()` post-solve | Turni = vincolo **hard** nei domini dei segmenti |
| `_add_solution_hints()` greedy pre-solve | Rimosso (non più necessario) |
| `_compute_wall_clock_span`, `_compute_slot_aware_end`, forward pass | **Eliminati** |
| 1 `ScheduleEntry` per operazione | 1 `ScheduleEntry` **per segmento** (Gantt mostra l'hand-off) |

### 6.5 Decisioni critiche — NON modificare senza capire il perché

**No parallelismo fantasma.** Il no-overlap per-operazione (§6.3) impedisce che due
operatori lavorino la stessa op nello stesso istante: l'hand-off è *sequenziale*, la
durata non si dimezza. Se in futuro serve la lavorazione SIMULTANEA (più operatori in
parallelo su una stessa op), va modellata esplicitamente rilassando questo vincolo —
**non** è un effetto collaterale gratuito.

**Pruning vs. INFEASIBLE spurio.** Se su istanze molto cariche il solver torna
`INFEASIBLE` per contesa risorse, alzare `CPSAT_SLOT_CAPACITY_FACTOR` (più slot
candidati per op) prima di sospettare un bug del modello.

**Workcenter ID nelle operazioni:**
```python
wc_id = op.workcenter_id or po.workcenter_id  # ← CORRETTO
# NON: op.workcenter_id or routing.production_order_id  ← era UUID dell'ordine!
```

**rp_order_constraints — approccio a variabili CP-SAT (non blocking_constraints).**
Il vecchio `blocking_constraints` (dict `{op_id → min_start_minute}` calcolato da
`schedule_entries` esistenti) era vuoto al primo run → nessun vincolo RP applicato
→ tutto schedulato in parallelo ignorando la BOM. Soluzione adottata:
`rp_order_constraints` e `parent_wait_constraints` costruiti su variabili CP-SAT,
corretti su ogni run indipendentemente da entries preesistenti. **NON ripristinare**
il vecchio meccanismo per i vincoli RP (può restare per override manuali per-operazione).

---

## 7. LOGICA DI PRECEDENZA — Due meccanismi (entrambi attivi)

### Meccanismo A: precedenze dirette (`precedence_pairs`)

```python
list[tuple[op_id, op_id]]  → model.Add(op_end[pred] <= op_start[succ])
```
Attualmente vuoto — routing SIMULTANEOUS non crea precedenze interne.

### Meccanismo B: rp_order_constraints — "Tipo B" (ordine tra rami fratelli)

```python
list[tuple[list[op_id], list[op_id]]]
```

Per ogni arco RP_pred → RP_succ nel DAG (stesso livello padre):
```python
completion = model.NewIntVar(0, horizon, f"rp_completion_{idx}")
model.AddMaxEquality(completion, [op_end[op] for op in ops_pred])
for op in ops_succ:
    model.Add(op_start[op] >= completion)
```
Risponde a: "in che ordine si costruiscono i RAMI dello stesso livello?"
Es. MA-003 (struttura) deve finire prima che MA-001 (idraulico) inizi.

### Meccanismo "Tipo A": parent_wait_constraints (op padre aspetta il proprio figlio)

```python
list[tuple[list[op_id], uuid.UUID]]   # (ops_target_figlio, op_id_padre)
```

Per ogni operazione del padre con `reference_point_id = RP-X`:
```python
ops_target = _collect_ops_recursive(target_po_id, children_map, ops_by_order, schedulable_op_ids)
completion = model.NewIntVar(0, horizon, f"pw_completion_{idx}")
model.AddMaxEquality(completion, [op_end[op] for op in ops_target])
model.Add(op_start[parent_op_id] >= completion)
```
Risponde a: "quando può iniziare l'operazione DEL PADRE che ha un certo RP?"

**Entrambi sono necessari e ENTRAMBI sono implementati oggi.** Il Tipo B da solo
non garantisce che le operazioni del padre attendano il completamento dei
propri figli diretti. Il Tipo A da solo non garantisce l'ordine tra rami
paralleli dello stesso livello.

### Flusso completo della rischedulazione (`reschedule_incremental`)

```
1.  Carica scenario e machine_order (sessione sync — Celery non supporta asyncio nativo)
2.  Marca tutte le schedule_entries esistenti del scenario come STALE
3.  Identifica operazioni IN_PROGRESS (actual_start set, actual_end NULL) →
    le loro op_id vengono raccolte in `in_progress_op_ids`
4.  Carica operazioni schedulabili (status != COMPLETED)
4a. Calcola epoch = scenario.start_date (o date.today() se NULL)
    Calcola now_minutes = minuti tra epoch e UTC ora
    Costruisce schedulable_ops:
      - ops IN_PROGRESS → earliest_start_minutes = now_minutes  ← NUOVO
      - ops PENDING/BLOCKED → earliest_start_minutes = 0
4b. Calcola vincoli componenti mancanti (arrival_date → minuto CP-SAT)
4c. Carica operatori + slot calendario dal giorno `start_date` in poi
4d. Costruisce il DAG dei reference point; calcola:
    - rp_order_constraints (Tipo B: ordine tra rami fratelli)
    - parent_wait_constraints (Tipo A: padre aspetta figlio)
5.  Calcola horizon = min(target_finish_date, fine_calendario + 7gg)
6.  Costruisce e risolve il modello CP-SAT (cpsat_model_builder)
7.  Se FEASIBLE/OPTIMAL: persiste nuove schedule_entries, elimina STALE
8.  Aggiorna scenario: last_run_status, last_run_at, last_run_makespan_days,
    last_run_operators_used, last_run_conflicts
9.  Notifica frontend via WebSocket {"type": "RESCHEDULE_COMPLETE", ...}
10. Avvia analisi proattiva AI in background (analyze_proactive task)
```

Tre eventi triggerano oggi una rischedulazione automatica:
1. **DelayEvent creato** con `requires_reschedule=True` (manuale via `delays.py`,
   oppure automatico via il nuovo state engine — vedi sezione 9)
2. **Chiamata manuale** a `POST /api/schedule/scenario/{id}/reschedule`
3. **Componente mancante marcato arrivato** (`missing_components/mark-arrived`)

---

## 8. SOLUTION EXTRACTOR E INFEASIBILITY ANALYZER

> **Nota v2:** l'estrazione delle entries avviene in
> `CpsatModelBuilder._extract_entries()` (1 `ScheduleEntryCreate` **per segmento
> presente**). `solution_extractor.py` è **legacy/non collegato** al flusso attuale;
> i suoi helper (`compute_makespan`, `find_critical_path`) restano utilizzabili come
> utility ma `extract()` riflette ancora il vecchio modello a singolo operatore.

`solution_extractor.py` (legacy):

| Metodo | Descrizione |
|---|---|
| `extract(solver, vars, ops, epoch, scenario_id)` | Legacy: 1 entry per `assign==1` con `op_start/op_end` aggregati. Non usato dal flusso a segmenti. |
| `compute_makespan(entries)` | `max(end) − min(start)` → `timedelta` |
| `compute_operator_utilization(entries, total_available_minutes)` | `{op_id: min(1.0, worked/available)}` per ogni operatore |
| `find_critical_path(entries, precedence_pairs)` | Longest-path su DAG pesato (peso = durata predecessore), `nx.dag_longest_path` |

**Nota**: questo critical path è una stima rapida basata solo sulle entries
correnti. Per un'analisi completa con slack/float per operazione (non solo
"critico sì/no"), vedi `CpmAnalyzer` in sezione 9 — usa lo stesso principio ma
espone anche il margine assorbibile di ogni operazione non critica.

`infeasibility_analyzer.py`:

| Metodo | Check effettuati |
|---|---|
| `analyze(...)` | Operatori qualificati mancanti, componenti mancanti oltre l'horizon, cicli residui nelle precedenze, carico totale > target |
| `suggest_fixes(conflicts)` | Pattern matching sul testo del conflitto → fix specifica in italiano |

---

## 9. STATE ENGINE — Gestione stati, ritardi e CPM (NUOVO)

> Modulo aggiunto per gestire transizioni di stato, propagazione ritardi e
> calcolo slack/float in modo professionale, colmando il gap tra
> `OperationSimulator.tsx`/`DelayManager.tsx` (frontend già esistente) e un
> backend che fino a questo punto non aveva l'endpoint reale né la logica di
> propagazione a supporto.

```
backend/app/core/state_engine/
  transitions.py          State machine pura (Operation/ScheduleEntry) + compute_rollup_status
  cpm_analyzer.py          Critical Path Method: early/late start-finish, total float
  order_status_rollup.py   Propagazione stato bottom-up lungo la gerarchia BOM (DB reale)
  delay_propagation.py     Orchestratore: DelayEvent automatico + rollup + reschedule condizionale
  models_audit.py          Modello OperationStatusAudit
```

Nuovo router: `backend/app/api/routes/operations.py` — implementa
`PATCH /api/operations/{operation_id}/status`, l'endpoint che il frontend
(`OperationSimulator.tsx`) chiamava da tempo ma che non esisteva lato backend.

Nuovo endpoint in `schedule.py`: `GET /api/schedule/scenario/{id}/cpm`
(slack/float per ogni operazione schedulata).

Nuova tabella: `operation_status_audit` (migration `003`).

### 9.1 Schema di transizione: PERMISSIVO

Qualunque transizione di stato è tecnicamente ammessa — il planner ha sempre
l'ultima parola (può riaprire un'operazione `COMPLETED`, annullare un
`BLOCKED`, ecc.). Il sistema non blocca mai, ma:
- marca la transizione `is_unusual=True` se esce dal workflow MES standard
  (`_EXPECTED_OPERATION_TRANSITIONS` in `transitions.py`)
- la traccia **sempre** in `operation_status_audit`, con warning testuali

**Non introdurre validazione bloccante** senza discuterne: è una scelta
esplicita del product owner.

### 9.2 Soglia di reschedule: configurabile, default 15 minuti

```bash
DELAY_RESCHEDULE_THRESHOLD_MINUTES=15   # .env
```

- Ritardo `< soglia` → `RescheduleUrgency.SOFT`: si aggiornano
  `delay_minutes`/stato, si crea comunque un `DelayEvent` (visibile in
  `DelayManager`), **ma non parte il reschedule CP-SAT automatico**.
- Ritardo `>= soglia` → `RescheduleUrgency.HARD`: reschedule CP-SAT completo
  via `reschedule_incremental.delay(...)` (stesso entry point Celery usato da
  `delays.py`) — **non** uno spostamento incrementale dei soli successori
  diretti: rilancia il solver su tutto il lavoro residuo.
- Bloccare/interrompere un'operazione `IN_PROGRESS` → sempre `HARD` (libera
  capacità operatore, i successori vanno ripianificati).
- Bloccare un'operazione mai iniziata (`PENDING`→`BLOCKED`) → `NONE` (nessun
  impatto sullo schedule esistente).

Il reschedule innescato dal state engine usa lo stesso `objective_mode` dello
scenario attivo (sezione 6.4) — se lo scenario ha `FINISH_BY_DATE`, il nuovo
piano post-ritardo minimizza di nuovo il makespan, non produce una soluzione
arbitraria.

### 9.3 Rollup stato ProductionOrder: autoritativo, eccetto MISSING

`compute_rollup_status()` deriva lo stato di un `ProductionOrder` dai suoi
figli diretti nella BOM con priorità `BLOCKED > MISSING > IN_PROGRESS > PLANNED`;
`COMPLETED` solo se **tutti** i figli sono `COMPLETED`.

**Eccezione sticky**: se lo stato attuale è già `MISSING`, il rollup non lo
sovrascrive automaticamente — resta `MISSING` finché
`missing_components.mark-arrived` non lo sblocca esplicitamente, anche se nel
frattempo tutti i figli risultano `COMPLETED`.

Per i `GROUP` (niente figli BOM con routing, solo componenti) lo stato è
derivato dalle proprie **operazioni**, non da figli `ProductionOrder`
(`_status_from_operations` in `order_status_rollup.py`).

### 9.4 CPM — stesso grafo di precedenza del CP-SAT

`CpmAnalyzer.analyze()` non richiama mai OR-Tools — lavora sui tempi già
decisi (`schedule_entries` correnti), quindi è molto più veloce di un secondo
solve e gira ad ogni `GET /cpm`.

Il grafo di precedenza unisce Meccanismo A (vuoto oggi) e il Tipo
A/B dei reference point, ricostruito al volo da
`_build_rp_constraints_for_cpm()` filtrato sulle sole operazioni presenti
nello scenario corrente.

Un'operazione con `total_float_minutes == 0` è sul critical path: qualunque
ritardo si propaga 1:1 sul makespan. Float positivo = slack assorbibile senza
impattare la data finale — stessa metrica del "Total Slack" di MS Project /
Primavera P6.

### 9.5 Flusso completo — operatore chiude un'operazione in ritardo

```
PATCH /api/operations/{id}/status  {status: COMPLETED, actual_end: ...}
  │
  ├─ 1. transition_operation_status() [puro] → calcola delay_minutes, urgency
  ├─ 2. Persiste Operation + ScheduleEntry (sessione async)
  ├─ 3. Scrive operation_status_audit (sempre, anche se is_unusual)
  ├─ 4. DelayPropagationEngine.apply() [sessione sync dedicata]
  │     ├─ 4a. Crea DelayEvent automatico se urgency SOFT|HARD
  │     ├─ 4b. OrderStatusRollup.propagate_from() → risale tutta la BOM
  │     └─ 4c. Se urgency==HARD → reschedule_incremental.delay() (Celery)
  └─ 5. Risposta HTTP immediata (reschedule è async)
        └─ frontend riceve poi RESCHEDULE_COMPLETE via WebSocket
```

### 9.6 Test e copertura

```bash
cd backend
pytest tests/test_transitions.py tests/test_cpm_analyzer.py -v
```

28 test puri (nessun DB, nessun OR-Tools), verificati: transizioni standard e
inusuali, soglie SOFT/HARD (incluso boundary esatto), rollup BOM con tutte le
priorità di dominanza, CPM su catena lineare/parallela/rp_order_constraints/
cicli difensivi.

**Non ancora testato con DB reale**: `order_status_rollup.py`,
`delay_propagation.py`, `operations.py` end-to-end. Da validare con pytest
contro Postgres portable avviato, stesso pattern di `test_reschedule_engine.py`
(mock/patch dei task Celery).

### 9.7 Integrazione manuale richiesta

Il modulo è stato consegnato come file standalone (nessun accesso diretto al
repo). Vedi `INTEGRAZIONE.md` per i diff puntuali:
1. Registrare `operations_router` in `main.py`
2. Integrare il blocco CPM in `schedule.py` (decoratore `@router.get` da
   aggiungere, lasciato fuori apposta per non duplicare `APIRouter()`)
3. Importare `OperationStatusAudit` in un modulo caricato all'avvio
4. Aggiungere `DELAY_RESCHEDULE_THRESHOLD_MINUTES=15` a `.env`
5. `alembic upgrade head` (venv attivo + PostgreSQL portable avviato)

---

## 10. SCENARIO — start_date, FINISH_BY_DATE e reschedule robusto (NUOVO)

### 10.1 Data di partenza configurabile (`start_date`)

Ogni scenario ha un campo opzionale `start_date`. È il **punto zero dell'epoch CP-SAT**:
tutte le variabili di tempo nel modello OR-Tools sono minuti interi relativi a questo instante.

```
epoch = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, UTC)
```

Se `start_date` è `NULL`, il reschedule usa `date.today()` (comportamento precedente).

**Casi d'uso:**
- `start_date = oggi` → scheduling standard in tempo reale
- `start_date = data futura` → simulazione "se iniziassimo il 1° agosto…"
- `start_date = data passata` → ricostruzione storica / confronto retroattivo

Il frontend (`NewScenarioModal`) espone il campo per tutti gli obiettivi di scheduling.
Il calendario degli operatori viene caricato **dal giorno `start_date`** in poi, non da oggi.

### 10.2 FINISH_BY_DATE — vincolo hard attivo

Il vincolo `makespan <= target_finish_minutes` è **attivo** in CP-SAT.
Se i vincoli di precedenza, i componenti mancanti o la capacità degli operatori
rendono impossibile rispettare la data, il solver restituisce `INFEASIBLE`.

Il frontend segnala questo con il banner rosso "Soluzione non fattibile" e
suggerisce azioni correttive (allargare la finestra, rimuovere componenti mancanti,
aggiungere operatori al workcenter).

`target_finish_minutes` viene calcolato in `reschedule_engine` come:
```python
target_dt = datetime(target_finish_date.year, ..., 23, 59, UTC)
params["target_finish_minutes"] = datetime_to_minutes(target_dt, epoch)
```

Nota: l'horizon CP-SAT è comunque limitato al min tra `target_finish_date` e
la fine del calendario degli operatori + 7 giorni. Se `target_finish_date` supera
il calendario, l'horizon viene troncato con un warning di log.

### 10.3 Reschedule robusto — operazioni IN_PROGRESS

Al momento del reschedule, le operazioni con `actual_start` impostato e `actual_end`
ancora `NULL` (cioè in corso) vengono identificate in **Step 3**.

Il loro `earliest_start_minutes` viene impostato a `now_minutes` (minuti tra epoch e UTC ora),
non a `0`. Questo garantisce che il solver **non riposizioni nel passato** un'operazione
già avviata. La durata residua è già corretta tramite `progress_pct`.

```python
earliest = now_minutes if op.id in in_progress_op_ids else 0
```

Se `start_date` è nel futuro, `now_minutes` è negativo → viene clampato a `0`
(nessuna operazione può iniziare prima dell'epoch, cioè prima di `start_date`).

---

## 11. AI LAYER — 7 modalità


| Modalità | Trigger | Endpoint |
|---|---|---|
| 1. Ottimizzazione | Manuale "Ottimizza con AI" | POST /api/ai/optimize-schedule |
| 2. Proattiva | Auto post CP-SAT (Celery, `analyze_proactive`) | interno |
| 3. Analisi ritardo | Creazione delay_event | POST /api/ai/analyze-delay |
| 4. Chat libera (multi-turno) | Input planner | POST /api/ai/chat |
| 5. What-if | Confronto scenari | POST /api/ai/compare-scenarios |
| 6. Storico | Manuale o 3+ scenari | POST /api/ai/analyze-history |
| 7. Spiega entry | Click "Perché?" su barra Gantt | GET /api/ai/explain-entry/{id} |

Tutte le risposte Claude sono JSON strutturato. Modello sempre `claude-sonnet-4-6`.
Lingua risposta: italiano. Timeout client 30s, retry max 3 su 429/500.
Prompt troncati a ~4000 token di contesto.

**Modalità 2 (proattiva)** ha anche una componente rule-based che gira
**prima** di chiamare Claude (più veloce, non dipende dall'API):
- operatore con utilizzo >90% in una settimana → suggerimento immediato
- componente mancante sul critical path → priorità CRITICAL immediata
- solo se le regole rilevano problemi gravi, si chiama anche Claude per
  mitigazioni più articolate. Max 5 suggerimenti per run (anti-spam).

Endpoint completi:
```
POST   /api/ai/optimize-schedule
POST   /api/ai/analyze-delay
POST   /api/ai/compare-scenarios
POST   /api/ai/analyze-history
GET    /api/ai/explain-entry/{entry_id}
GET    /api/ai/suggestions/{scenario_id}
GET    /api/ai/suggestions/proactive/{machine_order_id}
PATCH  /api/ai/suggestions/{id}/accept
PATCH  /api/ai/suggestions/{id}/reject
POST   /api/ai/chat
DELETE /api/ai/chat/{session_id}
```

`ChatSessionManager`: mantiene massimo 20 messaggi di history per sessione
(`ai_chat_sessions`), il contesto schedule viene reinserito nel system prompt
ad ogni chiamata (Claude non ha memoria propria tra chiamate API separate).

---

## 12. WEBSOCKET

Notifiche real-time al frontend (`ConnectionManager`, endpoint `GET /ws/{room_id}`,
room = `machine_order_id` o `scenario_id`):
```json
{"type": "RESCHEDULE_COMPLETE", "scenario_id": "...", "makespan_days": N}
{"type": "AI_SUGGESTION_NEW", "count": N, "scenario_id": "..."}
{"type": "SCHEDULE_INFEASIBLE", "conflicts": [...]}
```

---

## 13. MIGRATIONS ALEMBIC

- `001_initial_schema.py` — tutte le 19 tabelle originarie, enum `targetlevel` con `MACROAGGREGATE, AGGREGATE`
- `002_add_group_to_targetlevel.py` — `ALTER TYPE targetlevel ADD VALUE IF NOT EXISTS 'GROUP'`
- `003_add_cascade_to_scenario_id_fkey.py` — `CASCADE` su FK `schedule_entries.scenario_id`
- `004_add_operation_status_audit.py` — tabella `operation_status_audit` (state engine, sezione 9)
- `005_add_start_date_to_scenario.py` — colonna `start_date DATE nullable` su `schedule_scenarios`
  — punto zero dell'epoch CP-SAT per ogni scenario; se NULL usa `date.today()`

Alembic usa psycopg2 sync. `env.py` converte `postgresql+asyncpg://` → `postgresql+psycopg2://`.

**NON eseguire `alembic upgrade head` da fuori il venv con PostgreSQL spento.**
Comando corretto: dalla cartella `backend` con venv attivo e PostgreSQL portable avviato.

---

## 14. PARAMETRI SOLVER ATTUALI

```python
solver.parameters.max_time_in_seconds = 30           # 60 se MINIMIZE_OPERATORS/MAXIMIZE_UTIL/CUSTOM
solver.parameters.num_search_workers = min(8, cpu)
solver.parameters.stop_after_first_solution = ...     # True solo SENZA objective attivo
solver.parameters.log_search_progress = True          # solo dev
solver.parameters.linearization_level = 1
```

`CPSAT_SLOT_CAPACITY_FACTOR = 4.0` (env): per ogni op si generano i segmenti solo sui
slot più vicini la cui capacità cumulata raggiunge `4 × work_residual`. Alzarlo se il
solver torna `INFEASIBLE` per contesa risorse su istanze cariche.

> L'hint greedy pre-solve (`_add_solution_hints`) è stato **rimosso** nella v2 insieme
> al forward pass: il modello a segmenti non ne ha bisogno. Se i tempi di solve
> crescono su istanze grandi, valutare un hint sui `present`/`size` dei segmenti.

---

## 15. ERRORI NOTI E SOLUZIONI

| Errore | Causa | Soluzione |
|---|---|---|
| `'GROUP' is not among the defined enum values` | Migration 002 non applicata | `python -m alembic upgrade head` |
| `column "start_date" of relation … does not exist` | Migration 005 non applicata | `python -m alembic upgrade head` |
| `connection refused port 5432` | PostgreSQL portable non avviato | Avviare con `pg_ctl start -D <data_dir>` |
| `alembic upgrade head` fallisce su localhost | Eseguito fuori dal venv con PG spento | Attivare venv + avviare PG + rieseguire |
| `AddDivisionEquality` → INFEASIBLE | Durata variabile non lineare | Usare durata fissa (residual) |
| Op 480min non entra in un turno (~225min) | Modello v1 a singolo intervallo | Risolto — decomposizione a segmenti (sezione 6.2), l'op si spezza su più turni/operatori |
| `INFEASIBLE` per contesa risorse su istanze cariche | Pochi slot candidati per op | Alzare `CPSAT_SLOT_CAPACITY_FACTOR` |
| `wc_id = routing.production_order_id` | Bug UUID ordine usato come WC | Fix: `wc_id = op.workcenter_id or po.workcenter_id` |
| `ModuleNotFoundError: celery_worker` | Celery lanciato da cartella sbagliata | `cd backend` prima di lanciare Celery |
| Tutte le op schedulabili in parallelo (ignorano BOM) | `parent_wait_constraints` mancanti | Risolto — vedi sezione 6.1/6.4 |
| `MINIMIZE_OPERATORS` e `MAXIMIZE_RESOURCE_UTILIZATION` danno risultati identici | `stop_after_first_solution=True` ignorava l'obiettivo | Risolto — vedi sezione 6.4 |
| Op IN_PROGRESS rischedulata nel passato | `earliest_start_minutes=0` ignorava lo stato | Risolto — ora ancorata a `now_minutes` |
| `PATCH /api/operations/{id}/status` → 404 Not Found | Router `operations.py` non esisteva | Risolto — vedi sezione 9, da integrare in `main.py` |

---

## 16. PROBLEMI APERTI (ordinati per priorità, stato verificato giugno 2026)

### 1. Contiguità dei segmenti / anti-frammentazione (BASSA)

Il modello a segmenti (sezione 6.2) rispetta i turni come vincolo hard, ma **non**
impone che gli slot interni siano riempiti prima di passare al successivo: le
operazioni non critiche potrebbero risultare frammentate. L'obiettivo makespan tende
a compattare, ma per un Gantt più pulito si può aggiungere la formulazione monotona
`before/after` (vedi `SCHEDULER_CONSTRAINTS.md` §13).

### 2. Validazione end-to-end del nuovo state engine (MEDIA)

`order_status_rollup.py`, `delay_propagation.py` e il router `operations.py`
(sezione 9) sono stati validati solo a livello di logica pura. Vanno
testati contro un Postgres reale e integrati fisicamente nel repo
(`INTEGRAZIONE.md`).

### 3. Lavorazione SIMULTANEA (più operatori in parallelo su una stessa op) (BASSA)

Il modello v2 supporta l'**hand-off sequenziale** (operatori diversi in tempi diversi),
ma **non** la lavorazione simultanea che dimezzerebbe la durata. Richiede di rilassare
il no-overlap per-operazione (sezione 6.5) modellando esplicitamente la capacità
parallela. Non introdurla come effetto collaterale.

---

## 17. DEPLOY CON PODMAN (alternativa al deploy locale a terminali)

Oltre al deploy locale a più terminali (`start-local.ps1`, con Postgres/Redis
portable), lo stack si può avviare interamente in container con **Podman**, senza
Docker e senza privilegi di amministratore (Podman è rootless e daemonless).

### 17.1 File coinvolti

| File | Ruolo |
|------|-------|
| `podman-compose.yml` | Definizione dei 7 servizi (vedi sotto). Funziona anche con `docker compose`. |
| `start-podman.ps1` | Script PowerShell: verifica Podman, avvia la macchina, build + up, seed opzionale. |
| `backend/Dockerfile` | Immagine backend (FastAPI + Celery + OR-Tools). Usata da `backend`, `worker`, `migrate`, `seed`. |
| `frontend/Dockerfile` | Immagine frontend (Vite dev server). |
| `backend/.dockerignore`, `frontend/.dockerignore` | Escludono `.venv`/`node_modules`/cache dal build context (rispettati anche da Podman). |

### 17.2 Servizi

| Servizio | Cosa fa | Porta |
|----------|---------|-------|
| `postgres` | PostgreSQL 16 (volume `postgres_data`, healthcheck `pg_isready`) | 5432 |
| `redis` | Redis 7 (broker/backend Celery, healthcheck `redis-cli ping`) | 6379 |
| `migrate` | One-shot: `alembic upgrade head`, poi esce. Backend/worker lo attendono. | — |
| `backend` | `uvicorn app.main:app --reload` | 8000 |
| `worker` | `celery -A celery_worker.celery_app worker --pool=solo` — **esegue il reschedule** | — |
| `frontend` | `npm run dev` (Vite) | 5173 |
| `seed` | Opt-in (profile `seed`): popola i dati mock TURBOPRESS-X500 | — |

> Il vecchio `docker-compose.yml` era **incompleto**: mancava il servizio `worker`
> (senza il quale `reschedule_incremental.delay` non viene mai eseguito) e lo step di
> migrazione. `podman-compose.yml` li include entrambi.

### 17.3 Prerequisiti (una-tantum)

1. Installare **Podman Desktop** (include `podman` CLI e il provider `podman compose`).
2. Su Windows/macOS inizializzare la macchina Podman:
   ```powershell
   podman machine init
   podman machine start
   ```
3. Tenere il progetto **sotto la home utente** (es. `C:\Users\<nome>\...`): la podman
   machine monta automaticamente la home, e i bind-mount del codice (hot-reload)
   funzionano solo per path montati.

### 17.4 Avvio

```powershell
# Tutto in un comando (build immagini + avvio + healthcheck):
.\start-podman.ps1            # avvia lo stack
.\start-podman.ps1 -Seed      # avvia E popola i dati mock (primo avvio)
.\start-podman.ps1 -Down      # ferma (il DB nel volume resta)
```

Oppure manualmente:
```powershell
podman compose -f podman-compose.yml up -d --build
podman compose -f podman-compose.yml --profile seed run --rm seed   # seed opt-in
podman compose -f podman-compose.yml logs -f backend worker
podman compose -f podman-compose.yml down                           # stop
podman compose -f podman-compose.yml down -v                        # stop + cancella DB
```

Frontend → `http://localhost:5173`, backend/docs → `http://localhost:8000/docs`.

### 17.5 Note tecniche specifiche per Podman

- **Override degli URL**: il `.env` contiene URL `@localhost` (validi per il deploy a
  terminali). Dentro la rete compose i servizi si raggiungono per **nome**, quindi il
  compose sovrascrive `DATABASE_URL`/`REDIS_URL` con `@postgres`/`@redis` (la chiave
  `environment` ha precedenza su `env_file`).
- **SELinux / `:Z`**: i bind-mount usano l'opzione `:Z` (relabel) richiesta dalla
  podman machine; Docker la ignora, quindi lo stesso file resta compatibile.
- **`node_modules`**: il servizio `frontend` usa un volume anonimo su `/app/node_modules`
  così i moduli installati nel container non vengono nascosti dal bind-mount del sorgente.
- **`psycopg2-binary`**: aggiunto a `requirements.txt` — Alembic (`alembic/env.py`) usa
  il driver **sync** psycopg2, distinto dall'asyncpg usato a runtime da FastAPI. Senza,
  lo step `migrate` fallirebbe nel container.
- **`depends_on: condition:`**: `service_healthy` / `service_completed_successfully`
  richiedono `podman compose` (provider docker-compose) o `podman-compose >= 1.1`.
- **Hot-reload**: backend (`--reload`) e frontend (Vite) montano il sorgente dall'host,
  quindi le modifiche al codice si ricaricano a caldo come nel deploy locale.

### 17.6 Troubleshooting

| Problema | Causa / Soluzione |
|----------|-------------------|
| `migrate` fallisce con errore psycopg2 | Ricostruire l'immagine: `podman compose -f podman-compose.yml build --no-cache backend` (deve includere `psycopg2-binary`). |
| Backend non raggiunge il DB | URL ancora `@localhost`: verifica che il compose imposti `DATABASE_URL=...@postgres`. |
| Bind-mount vuoto / codice non montato | Progetto fuori dalla home montata: spostalo sotto `C:\Users\<nome>` o aggiungi il volume con `podman machine init --volume`. |
| `condition` ignorata / ordine errato | Stai usando una versione vecchia di `podman-compose`: passa a `podman compose`. |
| Reschedule non parte | Controlla che il servizio `worker` sia up: `podman compose -f podman-compose.yml ps`. |