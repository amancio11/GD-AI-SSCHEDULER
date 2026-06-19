# GD Scheduler — GUIDA TECNICA
> Istruzioni persistenti per AI assistant. Leggere integralmente prima di toccare qualsiasi file.
> Versione rigenerata e riconciliata — sostituisce tutte le versioni precedenti del documento.

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
schedule_scenarios      id, machine_order_id FK, name, objective_mode, target_finish_date,
                        resource_set_json, is_active, is_baseline, ai_explanation, created_at
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
  cpsat_types.py            SchedulableOperation, QualifiedOperator, CpsatVariables, CpsatSolution
  cpsat_model_builder.py    CpsatModelBuilder con build_and_solve()
  dag_builder.py            build_precedence_dag(), get_scheduling_order()
  shift_preprocessor.py     build_operator_available_slots(), build_unavailable_intervals()
  reschedule_engine.py      Celery task reschedule_incremental (ENTRY POINT)
  solution_extractor.py     Traduce soluzione CP-SAT in schedule_entries
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

> Questa sezione è stata riverificata riga per riga contro il codice attuale
> (giugno 2026). Le versioni precedenti di questo documento contenevano
> affermazioni superate (es. "obiettivo disabilitato") che NON sono più vere.
> Vedi sezione 6.4 per il dettaglio di cosa è cambiato.

### 6.1 Riepilogo vincoli CP-SAT

| # | Vincolo | Metodo | Stato |
|---|---|---|---|
| 1 | Ogni op ha esattamente 1 operatore qualificato (WC + skill) | `_add_assignment_constraints()` | ✅ Implementato |
| 2 | Operatori senza slot esclusi dall'assegnazione | `_add_shift_nooverlap_constraints()` | ⚠️ **v1 rilassata** (vedi 6.2) |
| 3 | Un operatore non fa due op contemporaneamente | `_add_operator_nooverlap_constraints()` | ✅ Implementato |
| 4 | Precedenze dirette op→op (`precedence_pairs`) | `_add_precedence_constraints()` | ✅ Implementato (pairs attualmente vuoti — routing SIMULTANEOUS non ne genera) |
| 5 | **Tipo B**: ordinamento intra-livello via DAG RP | `_add_rp_order_constraints()` | ✅ Implementato |
| 6 | **Tipo A**: op padre aspetta figlio target (per ogni RP) | `_add_parent_wait_constraints()` | ✅ **Implementato** (era il problema #1 ad alta priorità — risolto) |
| 7 | Op bloccata finché componente mancante non arriva | `_add_missing_component_constraints()` | ✅ Implementato |

### 6.2 Vincolo turni — ancora v1 rilassata (NON risolto)

`_add_shift_nooverlap_constraints()` blocca **solo** gli operatori che non hanno
nessuno slot disponibile in tutto l'horizon. Non impedisce a un'operazione di
cadere a cavallo di un periodo di assenza o fuori turno.

```python
def _add_shift_nooverlap_constraints(self) -> None:
    for oper in self.operators:
        if not oper.available_slots:
            for (op_id, oper_id), bv in v.assignments.items():
                if oper_id == oper.id:
                    model.Add(bv == 0)
```

**Perché è così**: la versione completa (`AddNoOverlap(fixed_intervals + optional)`)
causa INFEASIBLE in 0.2s perché operazioni multi-turno (es. 480 min) non
entrano in un singolo slot (max ~225 min per turno con pausa). La fix corretta
richiede la **decomposizione slot-task**: spezzare ogni operazione lunga in N
sotto-task che stanno singolarmente dentro un turno. **Non ancora implementata.**

**NON ripristinare** il vecchio `AddNoOverlap(fixed + optional)` senza prima
implementare la decomposizione slot-task — causa lo stesso crash.

### 6.3 Obiettivo CP-SAT — IMPLEMENTATO, non più disabilitato

`_set_objective()` implementa tutti e 4 i modi (`objective_mode`), non è più `pass`:

```python
FINISH_BY_DATE:
    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, list(op_end.values()))
    model.Minimize(makespan)
    # ⚠️ vedi nota sotto: il vincolo hard sul target è commentato

MINIMIZE_OPERATORS:
    # minimizza sum(operator_used_bool) — un bool per operatore, AddMaxEquality
    # sulle sue assign vars

MAXIMIZE_RESOURCE_UTILIZATION:
    # massimizza sum(assign[op,oper] * durata[op])

CUSTOM:
    # somma pesata: w_makespan*makespan + w_operators*used - w_utilization*total_util
    # pesi da params["weights"], default {makespan:0.5, operators:0.3, utilization:0.2}
```

Il parametro solver è stato corretto coerentemente: `stop_after_first_solution`
non è più sempre `True`. La logica attuale in `build_and_solve`:

```python
has_objective = self.model.Proto().HasField("objective") or \
                self.model.Proto().HasField("floating_point_objective")

if has_objective:
    self.solver.parameters.stop_after_first_solution = False
    if objective_mode in ("MINIMIZE_OPERATORS", "MAXIMIZE_RESOURCE_UTILIZATION", "CUSTOM"):
        self.solver.parameters.max_time_in_seconds = max(self.TIMEOUT, 60)
else:
    self.solver.parameters.stop_after_first_solution = True
```

→ **con un obiettivo attivo, il solver ha tempo per ottimizzare davvero**, non
si ferma più alla prima soluzione fattibile come avveniva nella versione
originaria del progetto.

**⚠️ Punto ancora aperto — vincolo hard su `target_finish_minutes` commentato:**

```python
if objective_mode == "FINISH_BY_DATE":
    makespan = model.NewIntVar(0, self.horizon, "makespan")
    model.AddMaxEquality(makespan, list(v.op_end.values()))
    model.Minimize(makespan)
    # AM3 - TEMPORANEAMENTE COMMENTATO per debug
    # if "target_finish_minutes" in params:
    #     model.Add(makespan <= int(params["target_finish_minutes"]))
```

Conseguenza pratica: oggi il solver **minimizza** il makespan (cerca di finire
il prima possibile dato lo stato corrente), ma **non rifiuta** una soluzione
che sfora la `target_finish_date` dello scenario — quel vincolo rigido è
disattivato per debug e non è stato riattivato. Verificare lo stato di questo
commento prima di assumere che le date target siano garantite come hard
constraint.

### 6.4 Cosa è cambiato rispetto alle versioni precedenti di questo documento

| Affermazione vecchia (superata) | Stato reale verificato |
|---|---|
| "`_add_parent_wait_constraints()` DA AGGIUNGERE — problema #1 priorità ALTA" | ✅ Implementato e integrato in `build_and_solve` |
| "`_set_objective` è `pass` (solo soddisfacibilità)" | ❌ Falso oggi — tutti e 4 i modi sono implementati |
| "Il solver usa `stop_after_first_solution=True`" sempre | ❌ Falso oggi — `True` solo in assenza di obiettivo |
| "Vincolo turni rilassato v1" | ✅ Ancora vero — non ancora risolto |

### 6.5 Decisioni critiche — NON modificare senza capire il perché

**Assegnazione 1 operatore, durata fissa.** Rimossa la logica SIMULTANEOUS multi-operatore
(`AddDivisionEquality` è non-lineare → instabile). Attualmente: `sum(assign_vars) == 1`,
durata fissa = residual da `max(planned × (1 - progress/100), MIN_OP_DURATION)`.
**NON reintrodurre** `AddDivisionEquality` senza testare su subset di 10 op con `CPSAT_MAX_OPS=10`.

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
3.  Identifica operazioni IN_PROGRESS → i loro end sono trattati come vincolo fisso
4.  Carica operazioni schedulabili (status != COMPLETED)
4a. Calcola vincoli componenti mancanti
4b. Carica operatori + slot calendario (56 giorni da oggi)
4c. Costruisce il DAG dei reference point (dag_builder, networkx)
4d. Calcola rp_order_constraints (Tipo B) + parent_wait_constraints (Tipo A)
5.  Calcola horizon (min tra target_finish_date e fine calendario + 7gg)
6.  Costruisce e risolve il modello CP-SAT (cpsat_model_builder)
7.  Se FEASIBLE/OPTIMAL: persiste le nuove schedule_entries (solution_extractor),
    elimina le STALE
8.  Notifica il frontend via WebSocket {"type": "RESCHEDULE_COMPLETE", "scenario_id": ...}
9.  Avvia analisi proattiva AI in background (analyze_proactive task)
```

Tre eventi triggerano oggi una rischedulazione automatica:
1. **DelayEvent creato** con `requires_reschedule=True` (manuale via `delays.py`,
   oppure automatico via il nuovo state engine — vedi sezione 9)
2. **Chiamata manuale** a `POST /api/schedule/scenario/{id}/reschedule`
3. **Componente mancante marcato arrivato** (`missing_components/mark-arrived`)

---

## 8. SOLUTION EXTRACTOR E INFEASIBILITY ANALYZER

`solution_extractor.py`:

| Metodo | Descrizione |
|---|---|
| `extract(solver, vars, ops, epoch, scenario_id)` | Legge `solver.Value(op_start/op_end)`, trova `assign==1`, converte minuti→datetime, ritorna `list[ScheduleEntryCreate]` |
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
scenario attivo (sezione 6.3) — se lo scenario ha `FINISH_BY_DATE`, il nuovo
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

## 10. AI LAYER — 7 modalità

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

## 11. WEBSOCKET

Notifiche real-time al frontend (`ConnectionManager`, endpoint `GET /ws/{room_id}`,
room = `machine_order_id` o `scenario_id`):
```json
{"type": "RESCHEDULE_COMPLETE", "scenario_id": "...", "makespan_days": N}
{"type": "AI_SUGGESTION_NEW", "count": N, "scenario_id": "..."}
{"type": "SCHEDULE_INFEASIBLE", "conflicts": [...]}
```

---

## 12. MIGRATIONS ALEMBIC

- `001_initial_schema.py` — tutte le 19 tabelle originarie, enum `targetlevel` con `MACROAGGREGATE, AGGREGATE`
- `002_add_group_to_targetlevel.py` — `ALTER TYPE targetlevel ADD VALUE IF NOT EXISTS 'GROUP'`
- `003_add_operation_status_audit.py` — tabella `operation_status_audit` (state engine, sezione 9)

Alembic usa psycopg2 sync. `env.py` converte `postgresql+asyncpg://` → `postgresql+psycopg2://`.

**NON eseguire `alembic upgrade head` da fuori il venv con PostgreSQL spento.**
Comando corretto: dalla cartella `backend` con venv attivo e PostgreSQL portable avviato.

---

## 13. PARAMETRI SOLVER ATTUALI

```python
solver.parameters.max_time_in_seconds = 30           # 60 se MINIMIZE_OPERATORS/MAXIMIZE_UTIL/CUSTOM
solver.parameters.num_search_workers = min(8, cpu)
solver.parameters.stop_after_first_solution = ...     # True solo SENZA objective attivo
solver.parameters.log_search_progress = True          # solo dev
solver.parameters.linearization_level = 1
```

Hint greedy attivo (`_add_solution_hints`): assegna ogni op al primo operatore
disponibile in ordine topologico prima del solve vero — riduce il tempo a
FEASIBLE da ~30s a 1-5s anche con obiettivo attivo.

---

## 14. ERRORI NOTI E SOLUZIONI

| Errore | Causa | Soluzione |
|---|---|---|
| `'GROUP' is not among the defined enum values` | Migration 002 non applicata | `python -m alembic upgrade head` |
| `connection refused port 5432` | PostgreSQL portable non avviato | Avviare con `pg_ctl start -D <data_dir>` |
| `alembic upgrade head` fallisce su localhost | Eseguito fuori dal venv con PG spento | Attivare venv + avviare PG + rieseguire |
| `AddDivisionEquality` → INFEASIBLE | Durata variabile non lineare | Usare durata fissa + 1 solo operatore |
| `AddNoOverlap(fixed + optional)` → INFEASIBLE | Op 480min non entra nei turni | Vincolo turni rilassato (v1 attuale, sezione 6.2) |
| `wc_id = routing.production_order_id` | Bug UUID ordine usato come WC | Fix: `wc_id = op.workcenter_id or po.workcenter_id` |
| `ModuleNotFoundError: celery_worker` | Celery lanciato da cartella sbagliata | `cd backend` prima di lanciare Celery |
| Tutte le op schedulabili in parallelo (ignorano BOM) | `parent_wait_constraints` mancanti | Risolto — vedi sezione 6.1/6.4 |
| `MINIMIZE_OPERATORS` e `MAXIMIZE_RESOURCE_UTILIZATION` danno risultati identici | `stop_after_first_solution=True` ignorava l'obiettivo | Risolto — vedi sezione 6.3 |
| Makespan supera `target_finish_date` senza errore | Vincolo hard `makespan <= target_finish_minutes` commentato per debug | Ancora aperto — vedi sezione 6.3 |
| `PATCH /api/operations/{id}/status` → 404 Not Found | Router `operations.py` non esisteva | Risolto — vedi sezione 9, da integrare in `main.py` |

---

## 15. PROBLEMI APERTI (ordinati per priorità, stato verificato giugno 2026)

### 1. Vincolo hard `target_finish_minutes` commentato (ALTA)

In `_set_objective`, ramo `FINISH_BY_DATE`, la riga
`model.Add(makespan <= int(params["target_finish_minutes"]))` è commentata
"temporaneamente per debug". Il solver minimizza il makespan ma non rifiuta
soluzioni che sforano la data target. Verificare se riattivarla rompe la
feasibility con i dati di seed attuali prima di farlo in modo permanente.

### 2. Vincolo turni v2 — decomposizione slot-task (MEDIA)

`_add_shift_nooverlap_constraints()` resta in versione v1 rilassata (sezione
6.2). Le operazioni possono essere schedulate a cavallo di periodi di assenza
o fuori turno. Richiede di spezzare ogni operazione lunga in sotto-task che
stiano singolarmente dentro un turno.

### 3. Validazione end-to-end del nuovo state engine (MEDIA)

`order_status_rollup.py`, `delay_propagation.py` e il router `operations.py`
(sezione 9) sono stati validati solo a livello di logica pura. Vanno
testati contro un Postgres reale e integrati fisicamente nel repo
(`INTEGRAZIONE.md`).

### 4. `AddDivisionEquality` / multi-operatore SIMULTANEOUS (BASSA)

Resta disabilitato per instabilità del solver. Non reintrodurre senza un
banco di prova isolato (`CPSAT_MAX_OPS=10`).