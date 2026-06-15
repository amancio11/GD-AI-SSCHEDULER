# MES Production Scheduler — Copilot Instructions

> Questo file è il contesto persistente del progetto. Viene letto automaticamente da
> GitHub Copilot Agent ad ogni sessione. Non modificarlo durante lo sviluppo senza
> motivo esplicito. Contiene: stack, domain model, data model, algoritmo, struttura
> file e regole di qualità.

---

## 1. RUOLO E DOMINIO

Stai implementando un **Production Scheduler intelligente** per il montaggio di
macchine industriali complesse. Il sistema si integra (in versione mock) con SAP
Digital Manufacturing (DM) e SAP ERP.

Concetti chiave del dominio:
- Una **macchina** è l'ordine radice. Sotto di essa ci sono **macroaggregati**,
  poi **aggregati**, poi **gruppi**, poi **componenti** (foglie).
- La gerarchia è flessibile: un aggregato può essere montato direttamente in macchina
  senza passare per il macroaggregato. La struttura non è rigida.
- I **componenti** sono tutti acquisto (o produzione non tracciata): non hanno routing,
  operazioni né BOM propria. Si verifica solo se sono mancanti o no.
- Tutti gli ordini non-componente hanno un **routing** con N operazioni. Il routing è
  SIMULTANEOUS: tutte le operazioni possono essere lavorate in parallelo (solo una risorsa per ognuna).
- Le **operazioni** possono essere interrotte e riprese. Mantengono `progress_pct`.
- I **vincoli di precedenza** tra ordini sono espressi tramite **reference point**:
  ogni operazione dell'ordine macchina ha un reference point associato che identifica
  un macroaggregato o aggregato. Un DAG di precedenze tra reference point determina
  in quale ordine schedulare gli sottoordini.
- Gli **operatori** hanno skill fissa (ELECTRICAL, MECHANICAL, MULTI) e workcenter
  fisso (non si spostano). Un operatore ELECTRICAL può fare solo operazioni ELECTRICAL
  nel suo workcenter. MULTI può fare tutto.
- Lo **scheduler** usa OR-Tools CP-SAT come solver. Il calendario operatori è
  pre-processato in slot interi (minuti dall'epoch) prima di costruire il modello.

---

## 2. STACK TECNOLOGICO

### Backend
- **Runtime**: Python 3.12
- **Framework**: FastAPI con Uvicorn
- **Scheduling engine**: OR-Tools CP-SAT (`ortools.sat.python.cp_model`)
- **DAG**: networkx
- **ORM**: SQLAlchemy 2.x (async con asyncpg)
- **Migrations**: Alembic
- **Database**: PostgreSQL 16
- **Task queue**: Celery 5.x con Redis 7 come broker
- **WebSocket**: FastAPI WebSocket nativo
- **AI**: Anthropic Python SDK (`anthropic`) → modello `claude-sonnet-4-6`
- **Export PDF**: WeasyPrint

### Frontend
- **Framework**: React 18 + TypeScript (strict mode — zero `any`)
- **Build**: Vite 5
- **UI**: shadcn/ui + Tailwind CSS
- **State**: Zustand
- **Charts/KPI**: Recharts
- **Gantt**: frappe-gantt (open source)
- **DAG visualization**: React Flow (per la pagina Reference Point Config)
- **HTTP client**: axios con typed API layer

### Infrastructure
- **Container**: Docker Compose (backend, frontend, postgres, redis)
- **Env**: variabili in `.env` (mai hardcodate)

---

## 3. MODELLO DATI — 19 TABELLE

Ogni tabella SQLAlchemy usa UUID come PK. Tutti i timestamp sono `DateTime(timezone=True)`.

```
machine_models          id, code, name, description
                        → modello macchina; tabella RP è per-modello

machine_orders          id, sap_order_id, machine_model_id FK, description,
                        status ENUM(PLANNED|IN_PROGRESS|COMPLETED|BLOCKED),
                        workcenter_id FK, created_at
                        → ordine radice (livello macchina)

production_orders       id, sap_order_id, parent_order_id FK(self nullable),
                        parent_material, machine_order_id FK,
                        level ENUM(MACHINE|MACROAGGREGATE|AGGREGATE|GROUP|COMPONENT),
                        material_code, description, quantity, unit,
                        workcenter_id FK, progress_pct FLOAT DEFAULT 0,
                        status ENUM(PLANNED|IN_PROGRESS|COMPLETED|BLOCKED|MISSING),
                        missing_arrival_date TIMESTAMP nullable,
                        is_purchase_component BOOL, is_production_component_untracked BOOL,
                        created_at

z_orders_link           id, child_order_id, parent_order_id, parent_material,
                        child_material, level, link_type
                        → replica SAP; fonte di verità per gerarchia BOM

routings                id, production_order_id FK UNIQUE, sap_routing_id,
                        execution_mode ENUM(SIMULTANEOUS)

operations              id, routing_id FK, sap_operation_id, sequence_number,
                        description,
                        operation_type ENUM(ELECTRICAL|MECHANICAL|GENERAL),
                        workcenter_id FK,
                        planned_duration_minutes INT,
                        actual_duration_minutes INT nullable,
                        progress_pct FLOAT DEFAULT 0,
                        status ENUM(PENDING|IN_PROGRESS|COMPLETED|BLOCKED|INTERRUPTED),
                        reference_point_id FK nullable,
                        can_be_interrupted BOOL DEFAULT true

reference_points        id, code, name, machine_model_id FK,
                        target_level ENUM(MACROAGGREGATE|AGGREGATE),
                        target_order_material VARCHAR
                        → identifica macroagg/aggregato; collegato alle operazioni macchina

reference_point_precedences
                        id, reference_point_id FK, predecessor_reference_point_id FK,
                        machine_model_id FK
                        → vincoli precedenza DAG; nessun predecessore = priorità massima

workcenters             id, code, name, location, description, is_active BOOL

operators               id, employee_id, full_name,
                        skill ENUM(ELECTRICAL|MECHANICAL|MULTI),
                        workcenter_id FK, is_active BOOL
                        → sede fissa, non si sposta

skill_workcenter_mapping
                        id, skill ENUM, workcenter_id FK,
                        can_do_electrical BOOL, can_do_mechanical BOOL, can_do_general BOOL
                        → mapping: quali operation_type può fare ogni skill in ogni workcenter

shifts                  id, name, start_time TIME, end_time TIME,
                        break_duration_minutes INT, is_active BOOL

operator_calendar       id, operator_id FK, date DATE,
                        shift_id FK nullable (null = assente),
                        is_available BOOL, notes TEXT, override_reason TEXT

missing_components      id, production_order_id FK, component_material, description,
                        expected_arrival_date DATE, is_arrived BOOL,
                        arrival_confirmed_date DATE nullable,
                        manually_flagged BOOL, notes TEXT

schedule_entries        id, scenario_id FK, operation_id FK, operator_id FK,
                        workcenter_id FK,
                        scheduled_start TIMESTAMP, scheduled_end TIMESTAMP,
                        actual_start TIMESTAMP nullable, actual_end TIMESTAMP nullable,
                        status ENUM(SCHEDULED|IN_PROGRESS|COMPLETED|INTERRUPTED|DELAYED|STALE),
                        interruption_reason TEXT nullable,
                        delay_minutes INT DEFAULT 0,
                        is_manual_override BOOL DEFAULT false

schedule_scenarios      id, name, description, machine_order_id FK,
                        objective_mode ENUM(FINISH_BY_DATE|MAXIMIZE_RESOURCE_UTILIZATION|
                                            MINIMIZE_OPERATORS|CUSTOM),
                        target_finish_date DATE nullable,
                        resource_set_json JSONB,
                        created_at, is_active BOOL, is_baseline BOOL,
                        ai_explanation TEXT nullable

delay_events            id, machine_order_id FK,
                        event_type ENUM(OPERATOR_ABSENCE|COMPONENT_DELAY|
                                        MANUAL_OPERATION_DELAY|OTHER),
                        affected_entity_id UUID, affected_entity_type VARCHAR,
                        delay_from TIMESTAMP, delay_until TIMESTAMP,
                        description TEXT, reported_at TIMESTAMP,
                        requires_reschedule BOOL DEFAULT true

ai_suggestions          id, scenario_id FK nullable, machine_order_id FK,
                        suggestion_type ENUM(ON_DEMAND|PROACTIVE|DELAY_ANALYSIS|
                                             HISTORICAL_PATTERN|WHAT_IF|EXPLAIN_ENTRY),
                        suggestion_text TEXT,
                        suggested_actions_json JSONB,
                        confidence_score FLOAT,
                        accepted BOOL nullable,
                        created_at

ai_chat_sessions        id, scenario_id FK nullable, machine_order_id FK,
                        messages_json JSONB, created_at, last_activity TIMESTAMP
```

---

## 4. ALGORITMO SCHEDULER — OR-Tools CP-SAT

### Pre-processing (shift_preprocessor.py)
Converti `operator_calendar` + `shifts` in slot interi (minuti dall'epoch):
```
available_slots: dict[operator_id, list[tuple[int, int]]]
```
Tutto CP-SAT lavora su interi. L'epoch è `today 00:00` in UTC.

### Fase 1 — DAG Builder (dag_builder.py)
- Usa `networkx.DiGraph`
- Nodi = reference_points del machine_model
- Archi = reference_point_precedences (A→B: A predecessore di B)
- Valida aciclicità con `nx.is_directed_acyclic_graph()` → ValueError su ciclo
- Output: `OrderedDict[rp_id → production_order_id]` in topological order

### Fase 2 — CP-SAT Model Builder (cpsat_model_builder.py)
Variabili per ogni operazione schedulabile:
- `start_var = NewIntVar(earliest_start, horizon, f"start_{op.id}")`
- `end_var = NewIntVar(earliest_start, horizon, f"end_{op.id}")`
- `interval = NewIntervalVar(start, residual_duration, end, name)`
- `residual_duration = max(planned * (1 - progress_pct/100), MIN_OP_MINUTES)`

Variabili assegnazione:
- `assign[op_id][operator_id] = NewBoolVar(...)` per ogni (op, operatore qualificato)
- Vincolo: `sum(assign[op]) >= 1` (almeno un operatore)
- Logica SIMULTANEOUS: `durata_eff = ceil(residual / n_assigned)` con variabile ausiliaria

Vincoli:
- **Turni**: slot indisponibili = `NewIntervalVar` fissi; `AddNoOverlap` per operatore
- **One-op-at-time**: `NewOptionalIntervalVar` condizionato su `assign`; `AddNoOverlap` per operatore
- **Precedenze DAG**: `model.Add(op_end[pred] <= op_start[succ])`
- **Reference point**: op macchina con RP-X non inizia finché ordine associato al RP-X non è completato
- **Mancanti**: `op_start[op] >= arrival_minute` per tutte le op del gruppo con mancante

Obiettivi (switchabili via `objective_mode`):
- `FINISH_BY_DATE`: minimize makespan + vincolo hard `makespan <= target`
- `MINIMIZE_OPERATORS`: minimize `sum(operator_used_bool)`
- `MAXIMIZE_RESOURCE_UTILIZATION`: maximize `sum(assign * duration)`
- `CUSTOM`: weighted sum configurabile

Solver:
```python
solver.parameters.max_time_in_seconds = float(os.getenv("CPSAT_TIMEOUT_SECONDS", 60))
solver.parameters.num_search_workers = 8
```
Se INFEASIBLE: `infeasibility_analyzer.py` trova il sotto-insieme minimo di vincoli
in conflitto e lo comunica in italiano al planner.

### Fase 3 — Rischedulazione Incrementale (reschedule_engine.py)
Celery task: ri-esegue CP-SAT solo su operazioni non COMPLETED; mantiene fisse le
IN_PROGRESS. Al termine notifica via WebSocket `{"type": "RESCHEDULE_COMPLETE", "scenario_id": ...}`.

---

## 5. AI LAYER — 7 MODALITÀ

| Modalità | Trigger | Endpoint |
|---|---|---|
| 1. Ottimizzazione | Manuale "Ottimizza con AI" | POST /api/ai/optimize-schedule |
| 2. Proattiva | Auto post CP-SAT (Celery) | interno |
| 3. Analisi ritardo | Creazione delay_event | POST /api/ai/analyze-delay |
| 4. Chat libera | Input planner | POST /api/ai/chat |
| 5. What-if | Confronto scenari | POST /api/ai/compare-scenarios |
| 6. Storico | Manuale o 3+ scenari | POST /api/ai/analyze-history |
| 7. Spiega entry | Click barra Gantt | GET /api/ai/explain-entry/{id} |

Tutte le risposte Claude sono JSON strutturato. Modello sempre `claude-sonnet-4-6`.
Lingua risposta: italiano.

Modulo AI:
```
backend/app/core/ai/
├── claude_client.py          # wrapper SDK, retry, error handling
├── prompt_builder.py         # prompt dinamici per ogni modalità
├── context_extractor.py      # serializza contesto DB per i prompt
├── response_parser.py        # parsing + validazione JSON response
├── proactive_analyzer.py     # analisi proattiva post-scheduling
└── chat_session_manager.py   # history multi-turno (ai_chat_sessions)
```

---

## 6. STRUTTURA FILE COMPLETA

```
scheduler-mes/
├── .github/
│   └── copilot-instructions.md        ← QUESTO FILE
├── backend/
│   ├── app/
│   │   ├── api/routes/
│   │   │   ├── orders.py
│   │   │   ├── schedule.py
│   │   │   ├── operators.py
│   │   │   ├── calendar.py
│   │   │   ├── scenarios.py
│   │   │   ├── ai.py
│   │   │   ├── delays.py
│   │   │   └── export.py
│   │   ├── core/
│   │   │   ├── scheduler/
│   │   │   │   ├── dag_builder.py
│   │   │   │   ├── cpsat_model_builder.py
│   │   │   │   ├── objective_configurator.py
│   │   │   │   ├── solution_extractor.py
│   │   │   │   ├── infeasibility_analyzer.py
│   │   │   │   ├── shift_preprocessor.py
│   │   │   │   └── reschedule_engine.py
│   │   │   ├── ai/
│   │   │   │   ├── claude_client.py
│   │   │   │   ├── prompt_builder.py
│   │   │   │   ├── context_extractor.py
│   │   │   │   ├── response_parser.py
│   │   │   │   ├── proactive_analyzer.py
│   │   │   │   └── chat_session_manager.py
│   │   │   └── export/
│   │   │       ├── csv_exporter.py
│   │   │       └── json_sap_exporter.py
│   │   ├── models/            # un file per tabella, es. operator.py
│   │   ├── schemas/           # Pydantic v2, un file per dominio
│   │   ├── db/
│   │   │   ├── session.py     # async engine + get_db dependency
│   │   │   └── seed.py        # idempotente, random.seed(42)
│   │   ├── websocket/
│   │   │   └── manager.py     # ConnectionManager per broadcast
│   │   └── main.py
│   ├── tests/
│   │   ├── test_dag_builder.py
│   │   ├── test_cpsat_model.py
│   │   ├── test_shift_preprocessor.py
│   │   └── test_api_schedule.py
│   ├── alembic/
│   ├── celery_worker.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── GanttView.tsx
│   │   │   ├── BOMExplorer.tsx
│   │   │   ├── OperatorCalendar.tsx
│   │   │   ├── ReferencePointConfig.tsx
│   │   │   ├── ScenarioManager.tsx
│   │   │   ├── DelayManager.tsx
│   │   │   ├── MissingComponents.tsx
│   │   │   ├── AIAssistant.tsx
│   │   │   └── ExportPage.tsx
│   │   ├── components/
│   │   │   ├── gantt/
│   │   │   │   ├── GanttByOperator.tsx
│   │   │   │   └── GanttByOrder.tsx
│   │   │   ├── bom/BOMTree.tsx
│   │   │   ├── scheduler/
│   │   │   │   ├── ScenarioPanel.tsx
│   │   │   │   ├── ObjectiveSelector.tsx
│   │   │   │   └── RescheduleButton.tsx
│   │   │   └── shared/
│   │   ├── store/             # Zustand: scheduleStore, operatorStore, aiStore, uiStore
│   │   ├── api/               # axios client + typed hooks per ogni endpoint
│   │   └── types/             # interfaces TypeScript speculari ai modelli DB
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 7. REGOLE DI QUALITÀ — SEMPRE RISPETTATE

- **TypeScript**: strict mode, zero `any`, zero `as unknown`
- **Python**: type hints completi, docstring su ogni funzione pubblica
- **API errors**: formato uniforme `{"error": str, "detail": str, "code": str}`
- **Seed**: idempotente (`INSERT ... ON CONFLICT DO NOTHING`), `random.seed(42)`
- **CP-SAT timeout**: da env `CPSAT_TIMEOUT_SECONDS` (default 60)
- **Test**: ogni modulo scheduler ha pytest con casi limite (ciclo, INFEASIBLE, interrupt)
- **WebSocket**: notifiche per rischedulazione completata e nuovi suggerimenti AI
- **Env vars**: mai hardcoded — sempre da `.env` via `python-dotenv` / Vite `import.meta.env`
- **No breaking changes**: ogni step deve lasciare il progetto in stato runnable

---

## 8. MOCK DATA — TURBOPRESS-X500

Macchina: `TURBOPRESS-X500`, modello `TX500`

BOM (seed.py deve generare esattamente):
- 1 ordine macchina `ORD-MACH-001`
- 3 macroaggregati: MA-001 "Gruppo Idraulico", MA-002 "Quadro Elettrico", MA-003 "Struttura Portante"
- MA-001 → 5 aggregati, MA-002 → 4 aggregati, MA-003 → 3 aggregati
- Ogni aggregato → 2-4 gruppi, ogni gruppo → 3-6 componenti
- Totale atteso: ~15 aggregati, ~40 gruppi, ~150 componenti

Operatori (20 totali su 3 workcenter):
- WC-MILANO: 8 op (3 ELECTRICAL, 3 MECHANICAL, 2 MULTI)
- WC-TORINO: 7 op (2 ELECTRICAL, 3 MECHANICAL, 2 MULTI)
- WC-BERGAMO: 5 op (1 ELECTRICAL, 2 MECHANICAL, 2 MULTI)

Turni: Mattina 06-14, Pomeriggio 14-22, Notte 22-06 (pausa 30 min ciascuno)

Componenti mancanti pre-settati:
- VLV-2200 "Valvola idraulica" → oggi +7gg
- CAB-450 "Cavo elettrico 25mm²" → oggi +3gg
- SEN-P100 "Sensore pressione" → oggi +12gg
- VIT-M16 "Vite speciale M16x80" → oggi +1gg
- GUA-200 "Guarnizione gomma" → oggi +5gg

Reference points modello TX500 (almeno 10, DAG valido senza cicli):
- RP-001 → MA-003 (nessun predecessore → priorità max)
- RP-002 → MA-001 (predecessore: RP-001)
- RP-003 → MA-002 (predecessore: RP-001)
- RP-004 → AGG nell'MA-001 (predecessore: RP-002)
- ... (continua fino a RP-010+)
