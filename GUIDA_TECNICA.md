# MES Production Scheduler — Guida Tecnica Completa

> Documento di riferimento aggiornato alla versione attuale del sistema.
> Descrive il dominio, l'architettura, il processo di scheduling, le scelte
> tecniche (incluse quelle prese durante il debug) e come ogni requisito è risolto.

---

## 1. CONTESTO DI DOMINIO — Il problema da risolvere

### La macchina industriale e la sua gerarchia

Il sistema pianifica il **montaggio di macchine industriali complesse** come la
TURBOPRESS-X500. Una macchina non è un oggetto semplice: ha una gerarchia di
sottoassemblaggi che devono essere costruiti rispettando vincoli fisici e logistici.

```
TURBOPRESS-X500 (MachineOrder: ORD-MACH-001)
├── MA-001 "Gruppo Idraulico"          (Macroaggregato — WC-MILANO)
│   ├── AGG-001 "Cilindro Principale"  (Aggregato)
│   │   ├── GRP-001 "Kit Guarnizioni"  (Gruppo)
│   │   │   ├── IDR-0001 Guarnizione NBR  (Componente, acquisto)
│   │   │   └── IDR-0002 Raccordo 1/4"   (Componente, acquisto)
│   │   └── GRP-002 "Gruppo Pistoni"   (Gruppo)
│   └── AGG-002 "Pompa Olio" ...
├── MA-002 "Quadro Elettrico"          (Macroaggregato — WC-MILANO)
└── MA-003 "Struttura Portante"        (Macroaggregato — WC-BERGAMO, si monta per prima)
```

**Regola fondamentale di dominio**: non puoi montare il Gruppo Idraulico se la
Struttura Portante non è ancora pronta. Questo vincolo è espresso dai **Reference Point**.

### Il problema del planner

Senza il sistema, il planner risponde a mano a domande come:
- Chi fa cosa e quando, sapendo che Marco è elettricista e non può fare lavori meccanici?
- Se arriva un componente in ritardo di 7 giorni, quali operazioni slittano?
- Riesco a finire entro la data di consegna con questi 20 operatori su 3 stabilimenti?
- Se aggiungo 2 operatori extra, di quanto anticipo la consegna?

Il sistema automatizza tutto questo tramite OR-Tools CP-SAT.

---

## 2. ARCHITETTURA GENERALE — Come sono connessi i pezzi

```
Browser (React + TypeScript)
        │  HTTP REST / WebSocket
        ▼
FastAPI (Python) — porta 8000
        │
        ├─► PostgreSQL 16 — porta 5432  (dati permanenti)
        │
        ├─► Redis 7 — porta 6379        (broker task Celery)
        │
        └─► Celery Worker (Python)       (job asincroni pesanti)
                 │
                 ├─► CP-SAT solver (OR-Tools)
                 └─► Claude AI (Anthropic API — claude-sonnet-4-6)
```

**Perché Celery?** Il solver CP-SAT può richiedere fino a 60 secondi.
Un endpoint HTTP che bloccasse 60s andrebbe in timeout nel browser.
Celery risolve questo: l'endpoint restituisce subito un `task_id`, il solver
gira in background, e quando finisce notifica il frontend via WebSocket.

---

## 3. IL DATABASE — 19 tabelle e perché esistono

### Tabelle di dominio (cosa c'è da produrre)

| Tabella | Scopo |
|---|---|
| `machine_models` | Modello macchina (es. TX500). La struttura dei Reference Point è per-modello |
| `machine_orders` | Ordine di produzione radice. Una TURBOPRESS-X500 da consegnare è un `machine_order` |
| `production_orders` | Tutti i livelli sotto la macchina: macroaggregati, aggregati, gruppi, componenti |
| `z_orders_link` | Replica della gerarchia BOM SAP. Fonte di verità per le relazioni padre-figlio |

### Tabelle di routing (come si produce)

| Tabella | Scopo |
|---|---|
| `routings` | Collega un `production_order` al suo piano di lavorazione. Modo SIMULTANEOUS |
| `operations` | Singola operazione di lavorazione. Ha tipo (ELECTRICAL/MECHANICAL/GENERAL), durata pianificata e progresso |

### Tabelle di vincolo (chi può fare cosa e quando)

| Tabella | Scopo |
|---|---|
| `workcenters` | Officine fisiche (WC-MILANO, WC-TORINO, WC-BERGAMO) |
| `operators` | Operatori con skill fissa. Un ELECTRICAL fa solo operazioni ELECTRICAL nel suo workcenter |
| `skill_workcenter_mapping` | Matrice: quale skill può fare quale tipo di operazione in quale workcenter |
| `shifts` | Turni (Mattina 06-14, Pomeriggio 14-22, Notte 22-06) con pausa di 30 min |
| `operator_calendar` | Disponibilità giornaliera di ogni operatore |

### Tabelle di vincolo di precedenza (in quale ordine)

| Tabella | Scopo |
|---|---|
| `reference_points` | Identificatori logici che rappresentano il completamento di un macroaggregato/aggregato |
| `reference_point_precedences` | Il DAG: RP-001 (Struttura) deve completarsi prima di RP-002 (Idraulico) |

### Tabelle di scheduling (il piano generato)

| Tabella | Scopo |
|---|---|
| `schedule_scenarios` | Un "piano alternativo". È possibile averne più di uno per confronto |
| `schedule_entries` | Le singole assegnazioni: operazione X → operatore Y → dalle 08:00 alle 12:00 del giorno Z |

### Tabelle di evento (cosa cambia in corsa)

| Tabella | Scopo |
|---|---|
| `missing_components` | Componenti non ancora arrivati. Bloccano le operazioni del gruppo finché non arrivano |
| `delay_events` | Ritardi registrati (assenza operatore, ritardo componente, altro) |

### Tabelle AI

| Tabella | Scopo |
|---|---|
| `ai_suggestions` | Suggerimenti generati da Claude (proattivi o su richiesta) |
| `ai_chat_sessions` | Storico conversazioni AI del planner |

---

## 4. IL MODELLO DATI — Concetti chiave

### Routing SIMULTANEOUS

Ogni ordine non-componente ha un **routing** con N operazioni. La modalità
`SIMULTANEOUS` significa che tutte le operazioni di un routing possono essere
lavorate in parallelo da operatori diversi — non esiste una sequenza obbligata
*interna* al routing. I vincoli di sequenza esistono *tra* ordini diversi, non
tra le operazioni dello stesso ordine.

### Operation Type e Skill

| Operation Type | Chi può farla |
|---|---|
| `ELECTRICAL` | Operatori con skill `ELECTRICAL` o `MULTI` |
| `MECHANICAL` | Operatori con skill `MECHANICAL` o `MULTI` |
| `GENERAL` | Qualsiasi operatore (inclusi `ELECTRICAL` e `MECHANICAL`) |

Un operatore non può mai lavorare fuori dal proprio workcenter.

### Reference Point e DAG

I Reference Point modellano i vincoli di sequenza *tra* macroaggregati/aggregati.
Il DAG del seed TURBOPRESS-X500:

```
RP-001 (Struttura Portante)
  ├──► RP-008 (Telaio Base)  ──► RP-009 (Montanti)
  ├──► RP-002 (Gruppo Idraulico)
  │      └──► RP-004 (Cilindro)  ──► RP-005 (Pompa)
  │                               └──► RP-010 (Collettore)
  └──► RP-003 (Quadro Elettrico)
         └──► RP-006 (Armadio)  ──► RP-007 (PLC)
```

Ogni operazione dell'ordine macchina ha un `reference_point_id` associato.
Quella operazione non può iniziare finché tutte le operazioni degli ordini
*predecessori* nel DAG non sono completate.

---

## 5. IL PROCESSO DI SCHEDULING — Passo per passo

### Step 1 — Raccolta dati (pre-processing)

Prima di dare qualsiasi cosa al solver, `shift_preprocessor.py` converte
il calendario operatori in **slot di minuti interi dall'epoch**:

```python
# Esempio: Mario è disponibile il 13/06 turno mattina (06:00-13:30 con pausa)
mario_slots = [(8280, 8670)]  # minuti dall'epoch (oggi 00:00)
```

CP-SAT lavora SOLO con interi — niente datetime, niente float.

### Step 2 — Costruzione del DAG di precedenze

`dag_builder.py` usa `networkx` per costruire un grafo orientato dai reference point.
Validazione: se il DAG contiene un ciclo il sistema solleva `CyclicDependencyError`.

### Step 3 — Calcolo rp_order_constraints (Step 4d in reschedule_engine.py)

Questa è la fase più critica per la correttezza del piano. Per ogni arco `RP_pred → RP_succ`
nel DAG dei reference point, `reschedule_engine.py` costruisce un vincolo di gruppo:

```
rp_order_constraints: list[tuple[list[op_id], list[op_id]]]
```

**Logica di raccolta:**

```
Arco RP-M-01 → RP-M-02 significa:
  - RP-M-01 punta a MA-003 "Struttura Portante"
  - RP-M-02 punta a MA-001 "Gruppo Idraulico"
  → Tutte le op di MA-003 + AGG-010..012 + GRP-032..040 (ricorsivo)
    devono finire prima che qualsiasi op di MA-001 + suoi figli inizi
```

La raccolta è **ricorsiva sui figli BOM**: se il RP punta a MA-003, si raccolgono
le operazioni di MA-003, dei suoi aggregati figli, e dei gruppi figli degli aggregati.
I componenti vengono saltati (non hanno routing né operazioni).

**Struttura RP per livello BOM (v2 — corretta):**

Ogni ordine non-componente ha RP che puntano **esclusivamente ai propri figli diretti** nella BOM:

| Livello ordine | RP puntano a |
|---|---|
| MACHINE (ORD-MACH-001) | I 3 macroaggregati: MA-001, MA-002, MA-003 |
| MACROAGGREGATE MA-001 | I 5 aggregati figli: AGG-001..005 |
| MACROAGGREGATE MA-002 | I 4 aggregati figli: AGG-006..009 |
| MACROAGGREGATE MA-003 | I 3 aggregati figli: AGG-010..012 |
| AGGREGATE AGG-001 | I 3 gruppi figli: GRP-001..003 |
| AGGREGATE AGG-002 | I 4 gruppi figli: GRP-004..007 |
| ... (ogni aggregato verso i propri gruppi) | |
| GROUP | **Nessun RP** — figli sono componenti senza routing |

**Totale: 55 Reference Point, 43 archi DAG.**

### Step 4 — Costruzione del modello CP-SAT

`cpsat_model_builder.py` traduce il problema in matematica.

**Per ogni operazione schedulabile:**
```python
start_var = model.NewIntVar(earliest, horizon, f"start_{op.id}")
end_var   = model.NewIntVar(earliest, horizon, f"end_{op.id}")
residual  = max(planned * (1 - progress_pct/100), MIN_OP_MINUTES)
interval  = model.NewIntervalVar(start_var, residual, end_var, name)
```

**Vincoli in ordine di applicazione:**

1. **`_add_assignment_constraints()`** — almeno 1 operatore per op; durata fissa con 1 operatore
2. **`_add_shift_nooverlap_constraints()`** — blocchi indisponibilità operatori (versione rilassata v1)
3. **`_add_operator_nooverlap_constraints()`** — un operatore non fa due cose in parallelo
4. **`_add_precedence_constraints()`** — due sotto-logiche:
   - `precedence_pairs` diretti tra op (attualmente vuoti, meccanismo disponibile)
   - `blocking_constraints` per-op (dict statico, legacy, non più usato per i RP)
5. **`_add_rp_order_constraints()`** ← **NUOVO — la logica corretta per i RP**
6. **`_add_missing_component_constraints()`** — `start(op) >= arrival_minute(componente)`

**Perché `_add_precedence_constraints()` non è stato eliminato:**
Gestisce i `precedence_pairs` diretti op→op e i `blocking_constraints` per-operazione,
meccanismi utili per override manuali e future estensioni. Va mantenuto in sequenza
prima di `_add_rp_order_constraints()`.

**Come funziona `_add_rp_order_constraints()`:**

Per ogni coppia `(ops_pred, ops_succ)` in `rp_order_constraints`:

```python
# Variabile ausiliaria: quando finiscono TUTTE le op del gruppo predecessore
completion = model.NewIntVar(0, horizon, f"rp_completion_{idx}")
model.AddMaxEquality(completion, [op_end[id] for id in ops_pred])

# Ogni op del gruppo successore deve aspettare quel momento
for succ_id in ops_succ:
    model.Add(op_start[succ_id] >= completion)
```

Questo approccio è O(|pred| + |succ|) invece di O(|pred| × |succ|) pairwise,
e funziona **anche al primo run** quando non esistono `schedule_entries` precedenti
(problema che affliggeva il vecchio approccio `blocking_constraints`).

### Step 5 — Funzione obiettivo e risoluzione

Il solver opera con `stop_after_first_solution=True` e nessun obiettivo attivo
(`_set_objective` è `pass` — solo soddisfacibilità). Questo garantisce tempi
< 5 secondi su 259 operazioni. L'obiettivo verrà riabilitato dopo la stabilizzazione
del vincolo turni v2.

**Se INFEASIBLE**: `infeasibility_analyzer.py` trova i vincoli in conflitto e li
spiega in italiano al planner.

### Step 6 — Salvataggio e notifica

`solution_extractor.py` traduce la soluzione in `schedule_entries`, poi il Celery
task notifica via WebSocket `{"type": "RESCHEDULE_COMPLETE", "scenario_id": ...}`.

---

## Decisioni critiche già prese


### rp_order_constraints — approccio variabili CP-SAT (v2)

**Problema originale (v1):** `blocking_constraints` era un dict `{op_id → min_start_minute}`
calcolato a partire dalle `schedule_entries` esistenti. Al primo run il dict era vuoto
→ nessun vincolo RP veniva applicato → tutte le operazioni venivano schedulate in
parallelo ignorando la gerarchia BOM.

**Soluzione adottata (v2):** `rp_order_constraints: list[tuple[list[op_id], list[op_id]]]`
costruito in Step 4d del reschedule_engine. Per ogni arco del DAG RP:
1. Si raccolgono ricorsivamente tutte le op schedulabili dell'ordine predecessore (+ figli BOM)
2. Si raccolgono le op dell'ordine successore (+ figli BOM)
3. Il builder CP-SAT aggiunge una variabile `completion = max(op_end[pred])` e forza
   ogni op successore a partire dopo `completion`.

Funziona al primo run e a tutti i run incrementali. Non dipende da dati preesistenti.

**NON ripristinare** il vecchio `blocking_constraints` per i vincoli RP.
Il dict può rimanere come meccanismo per override manuali per-operazione.

### Struttura RP corretta (v2)

**I vecchi RP-001…RP-010 sono stati eliminati e sostituiti con 55 RP strutturati.**

**NON usare** codici RP nella forma `RP-XXX` (tre cifre). La nuova nomenclatura è:
- `RP-M-xx` per il livello macchina
- `RP-MA1-xx`, `RP-MA2-xx`, `RP-MA3-xx` per i macroaggregati
- `RP-Axxx-xx` per gli aggregati (es. `RP-A001-01`)

**Regola invariante:** ogni RP punta **solo ai figli diretti** dell'ordine che lo contiene.
Mai a nipoti, cugini, o ordini di livelli diversi.
## 6. LA RISCHEDULAZIONE — Quando il piano cambia in corsa

Tre eventi triggherano una rischedulazione automatica:

1. **Delay event creato** (`requires_reschedule=True`) → lo scenario attivo
   viene rischedulato automaticamente via Celery
2. **Chiamata manuale** a `POST /api/scenarios/{id}/run`
3. **In futuro**: integrazione con SAP che notifica cambi di stato materiale

Il flusso del Celery task `reschedule_incremental`:

```
1. Carica scenario e machine_order
2. Marca le schedule_entries esistenti come STALE
3. Identifica operazioni IN_PROGRESS (fissate come già avviate)
4. Carica operazioni schedulabili (status != COMPLETED)
5. Carica vincoli componenti mancanti
6. Carica operatori + slot calendario (56 giorni da oggi)
7. Calcola horizon (min tra target_finish_date e fine calendario + 7gg)
8. Costruisce e risolve modello CP-SAT
9. Se FEASIBLE: persiste le nuove schedule_entries, cancella le STALE
10. Notifica frontend via WebSocket
11. Avvia analisi proattiva AI in background
```

---

## 7. L'AI LAYER — 7 modalità

| Modalità | Trigger | Endpoint |
|---|---|---|
| 1. Ottimizzazione | Manuale "Ottimizza con AI" | POST /api/ai/optimize-schedule |
| 2. Proattiva | Auto post CP-SAT (Celery) | interno |
| 3. Analisi ritardo | Creazione delay_event | POST /api/ai/analyze-delay |
| 4. Chat libera | Input planner | POST /api/ai/chat |
| 5. What-if | Confronto scenari | POST /api/ai/compare-scenarios |
| 6. Spiega entry | Click "Perché?" su barra Gantt | GET /api/ai/explain-entry/{id} |
| 7. Pattern storici | Analisi tendenze | POST /api/ai/historical-patterns |

Tutte le chiamate AI usano `claude-sonnet-4-6` via Anthropic SDK. Il
`prompt_builder.py` costruisce il contesto includendo la struttura BOM,
il piano attuale, i KPI e la domanda specifica.

---

## 8. IL WEBSOCKET — Notifiche real-time

Il backend gestisce connessioni WebSocket per room (una per scenario):

```
Frontend → ws://localhost:8000/ws/{scenario_id}
```

**Messaggi che il server invia al frontend:**

```json
{"type": "RESCHEDULE_COMPLETE", "scenario_id": "uuid..."}
→ Il Gantt si aggiorna automaticamente

{"type": "AI_SUGGESTION_NEW", "count": 2, "scenario_id": "uuid..."}
→ Il badge AI nell'header mostra il contatore

{"type": "SCHEDULE_INFEASIBLE", "conflicts": ["Operatori insufficienti..."]}
→ Viene mostrato un messaggio di errore con la spiegazione
```

Il frontend implementa reconnect con backoff esponenziale (1s → 2s → 4s →
8s → 30s, max 10 tentativi).

---

## 9. LO SCHEDULER — Scenari multipli e confronto

Ogni `schedule_scenario` è un "piano alternativo" con un obiettivo diverso.
Il seed crea 3 scenari di partenza:

| Scenario | Obiettivo | Target |
|---|---|---|
| Scenario Base | FINISH_BY_DATE | oggi + 90gg |
| Scenario Economia Risorse | MINIMIZE_OPERATORS | oggi + 100gg |
| Scenario Massima Produttività | MAXIMIZE_RESOURCE_UTILIZATION | oggi + 80gg |

Solo uno scenario può essere **ACTIVE** (il piano operativo). Tutti possono
essere confrontati via `GET /api/scenarios/compare?a={id}&b={id}`.

Il **BASELINE** è il piano di riferimento contrattuale — rimane fermo anche
quando si attiva un altro scenario.

---

## 10. REQUISITI E COME SONO RISOLTI

| Requisito | Soluzione |
|---|---|
| Schedulare 259 operazioni rispettando skill e precedenze | OR-Tools CP-SAT con BoolVar di assegnazione e `AddNoOverlap` sugli optional intervals |
| Operatore non può lavorare su due operazioni contemporaneamente | `AddNoOverlap` sugli `OptionalIntervalVar` per operatore |
| Struttura Portante prima del Gruppo Idraulico | DAG topologico + `end(pred) ≤ start(succ)` in CP-SAT |
| Componente mancante blocca le operazioni del gruppo | `start(op) ≥ arrival_minute` come vincolo CP-SAT |
| Rischedulazione incrementale senza perdere il progresso | Operazioni COMPLETED escluse dal modello; IN_PROGRESS fissate |
| Risposta immediata nonostante calcolo lungo | Celery task asincrono + WebSocket per notifica completamento |
| Spiegare in italiano perché un piano è impossibile | `infeasibility_analyzer.py` analizza cause strutturali e le espone in italiano |
| Confrontare scenari alternativi | Tabella `schedule_scenarios` + endpoint compare con delta KPI |
| Export per SAP | JSON-SAP via `GET /api/export/scenario/{id}/json-sap` |
| Modello CP-SAT non trovava soluzione (UNKNOWN/INFEASIBLE) | Rimosso vincolo shift strutturalmente impossibile + `sum(assign)==1` + durate fisse + hint greedy |
| Operazioni da 480min non entravano in slot da 210min | Eliminato `AddNoOverlap(fixed_unavailable + optional)` — v2 userà decomposizione slot-task |
| `AddDivisionEquality` non-lineare bloccava il presolve | Rimossa logica SIMULTANEOUS: un operatore per op, durata fissa |

---

## 11. DATI MOCK — TURBOPRESS-X500 (seed.py (v2))

Il seed (`backend/app/db/seed.py`) è idempotente: si può rieseguire più volte
senza duplicare dati. Usa `random.seed(42)` e `uuid.uuid5()` per determinismo.

### Dati mock 

- 1 modello macchina TX500, 1 ordine macchina ORD-MACH-001
- 3 macroaggregati → 12 aggregati → 40 gruppi → ~150 componenti
- 20 operatori su 3 workcenter (Milano, Torino, Bergamo) con skill mista
- 3 turni (Mattina 06-14, Pomeriggio 14-22, Notte 22-06) con pausa 30 min
- 28 giorni calendario, ~8 assenze casuali distribuite
- **55 Reference Point** con DAG a più livelli (43 archi, verificato aciclico)
- 5 componenti mancanti pre-settati
- UUID deterministici: `uuid.uuid5(NS, name)` — safe da rieseguire

**Operazioni con reference_point_id:**
- Ordine MACHINE: 3 operazioni (una per macroaggregato)
- MA-001: 5 operazioni (una per aggregato figlio)
- MA-002: 4 operazioni, MA-003: 3 operazioni
- Ogni aggregato: N operazioni (una per gruppo figlio)
- Gruppi: 3-6 operazioni **senza** reference_point_id

**Per rieseguire il seed da zero**, vedi sezione "Come resettare il database".

**Miglioramenti nel seed v2 (`seed_v2.py`):**
- Calendario esteso a 56 giorni (8 settimane)
- 25 assenze (8 ferie estive a blocco + 17 singole), sabato solo Mattina, domenica chiuso
- Progress_pct non-zero su aggregati/gruppi (simulazione lavori già avviati)
- Durate operazioni coerenti col tipo (non più uniformemente random 120-480)
- 8 componenti mancanti con note di fornitura dettagliate
- 3 scenari di partenza con obiettivi diversi per confronto immediato

---
### Come resettare il database e rieseguire il seed

Il seed è idempotente (`ON CONFLICT DO NOTHING`) ma **non cancella i dati vecchi**.
Se hai cambiato la struttura dei RP o delle operazioni, devi prima pulire le tabelle
prima di rieseguire.

**Metodo 1 — Reset completo (raccomandato in sviluppo):**

```bash
# Entra nel container postgres
docker compose exec postgres psql -U postgres -d gdscheduler

# Oppure con la connection string dal .env
psql $DATABASE_URL
```

Poi esegui nell'ordine corretto (rispettando le FK):

```sql
-- Cancella in ordine: prima le tabelle dipendenti
TRUNCATE TABLE schedule_entries CASCADE;
TRUNCATE TABLE schedule_scenarios CASCADE;
TRUNCATE TABLE operator_calendar CASCADE;
TRUNCATE TABLE missing_components CASCADE;
TRUNCATE TABLE operations CASCADE;
TRUNCATE TABLE routings CASCADE;
TRUNCATE TABLE reference_point_precedences CASCADE;
TRUNCATE TABLE reference_points CASCADE;
TRUNCATE TABLE z_orders_link CASCADE;
TRUNCATE TABLE production_orders CASCADE;
TRUNCATE TABLE machine_orders CASCADE;
TRUNCATE TABLE skill_workcenter_mapping CASCADE;
TRUNCATE TABLE operators CASCADE;
TRUNCATE TABLE shifts CASCADE;
TRUNCATE TABLE machine_models CASCADE;
TRUNCATE TABLE workcenters CASCADE;
```

Poi riesegui il seed:

```bash
cd backend && python -m app.db.seed
```

**Metodo 2 — Reset solo delle tabelle RP (se vuoi preservare altri dati):**

```sql
-- Solo le tabelle che cambiano con i nuovi RP
TRUNCATE TABLE schedule_entries CASCADE;    -- dipende da operations
TRUNCATE TABLE operations CASCADE;          -- ha reference_point_id
TRUNCATE TABLE routings CASCADE;
TRUNCATE TABLE reference_point_precedences CASCADE;
TRUNCATE TABLE reference_points CASCADE;
```

Poi riesegui solo le sezioni coinvolte oppure l'intero seed (è idempotente
per le tabelle non troncate, quindi non duplica).

**Metodo 3 — Drop e ricrea il DB (tabula rasa totale):**

```bash
docker compose exec postgres psql -U postgres -c "DROP DATABASE gdscheduler;"
docker compose exec postgres psql -U postgres -c "CREATE DATABASE gdscheduler;"

# Riesegui le migrazioni Alembic
cd backend && alembic upgrade head

# Riesegui il seed
python -m app.db.seed
```

**Verifica dopo il seed:**

Il seed stampa automaticamente i conteggi. Valori attesi con seed v2:

```
reference_points              55 righe
reference_point_precedences   43 righe
operations con RP             55 righe   ← [CHECK] stampato dal seed
routings                      ~56 righe  (1 macchina + 3 macro + 12 agg + 40 gruppi)
```

## 12. STRUTTURA FILE DEL PROGETTO

```
gd-scheduler/
├── .github/
│   ├── copilot-instructions.md        ← Contesto persistente per l'AI di sviluppo
│   ├── copilot-agent-steps.md         ← I 21 step di implementazione usati
│   └── esecuzione-github-copilot.md   ← Log dell'esecuzione con GitHub Copilot Agent
│
├── backend/
│   ├── app/
│   │   ├── api/routes/
│   │   │   ├── orders.py              ← BOM, machine orders, operations
│   │   │   ├── schedule.py            ← Scenari, schedule entries, Gantt data, run CP-SAT
│   │   │   ├── operators.py           ← Operatori, turni, calendario
│   │   │   ├── reference_points.py    ← Reference point e DAG precedenze
│   │   │   ├── delays.py              ← Delay events
│   │   │   ├── missing_components.py  ← Componenti mancanti
│   │   │   ├── ai.py                  ← Tutti gli endpoint AI (7 modalità)
│   │   │   ├── export.py              ← CSV, JSON-SAP, PDF
│   │   │   └── admin.py               ← DB Admin (solo sviluppo)
│   │   │
│   │   ├── core/
│   │   │   ├── scheduler/
│   │   │   │   ├── dag_builder.py             ← Costruisce il DAG networkx dai RP
│   │   │   │   ├── shift_preprocessor.py      ← Converte calendario in slot interi
│   │   │   │   ├── cpsat_model_builder.py     ← Costruisce e risolve il modello CP-SAT
│   │   │   │   ├── cpsat_types.py             ← Dataclass: SchedulableOperation, QualifiedOperator, ecc.
│   │   │   │   ├── solution_extractor.py      ← Traduce la soluzione in schedule_entries
│   │   │   │   ├── infeasibility_analyzer.py  ← Spiega in italiano perché INFEASIBLE
│   │   │   │   └── reschedule_engine.py       ← Celery task: pipeline rischedulazione
│   │   │   │
│   │   │   └── ai/
│   │   │       ├── claude_client.py           ← Wrapper Anthropic SDK con retry
│   │   │       ├── prompt_builder.py          ← Costruisce prompt contestuali
│   │   │       └── proactive_analyzer.py      ← Analisi automatica post-scheduling
│   │   │
│   │   ├── db/
│   │   │   ├── seed.py                        ← Seed v1 (28gg calendario, 5 mancanti)
│   │   │   └── seed_v2.py                     ← Seed v2 (56gg, 3 scenari, dati realistici)
│   │   │
│   │   ├── models/                            ← SQLAlchemy ORM models
│   │   ├── schemas/                           ← Pydantic schemas (request/response)
│   │   ├── enums.py
│   │   └── main.py
│   │
│   ├── tests/
│   │   ├── test_cpsat_constraints.py          ← 14 test CP-SAT con dati in-memory
│   │   ├── test_shift_preprocessor.py
│   │   ├── test_dag_builder.py
│   │   └── test_reschedule_engine.py
│   │
│   ├── celery_worker.py
│   └── requirements.txt
│
├── frontend/src/
│   ├── pages/                         ← 10 pagine (Dashboard, Gantt, BOM, ...)
│   ├── components/
│   │   ├── shared/Layout.tsx          ← Sidebar, header, WebSocket indicator
│   │   ├── gantt/                     ← GanttByOperator, GanttByOrder, popup
│   │   └── bom/BOMTree.tsx
│   ├── api/
│   │   ├── client.ts                  ← axios con interceptor
│   │   ├── types.ts                   ← TypeScript interfaces
│   │   └── hooks/                     ← React Query hooks per ogni endpoint
│   ├── store/                         ← Zustand stores (4)
│   └── hooks/useWebSocket.ts          ← Auto-reconnect con backoff esponenziale
│
├── docker-compose.yml
├── .env.example
└── GUIDA_TECNICA.md
```

---

## 13. COME FARE LA PRIMA SCHEDULAZIONE

Prerequisiti: PostgreSQL, Redis, backend e Celery worker attivi.

1. Apri `http://localhost:5173`
2. L'header mostra automaticamente "ORD-MACH-001 — TURBOPRESS-X500"
3. Vai su **Scenari** nella sidebar
4. Vedi "Scenario Base" con il badge `ACTIVE`
5. Clicca **Schedula** sulla card
6. Il pulsante mostra "Scheduling in corso…" con spinner
7. Nei log del Celery worker vedi l'output CP-SAT
8. Dopo pochi secondi il frontend riceve la notifica WebSocket
9. Vai su **Gantt** — le barre delle operazioni sono visibili
10. L'AI ha già analizzato il piano: controlla il badge AI nell'header

---

## 14. ERRORI COMUNI E SOLUZIONI

| Errore | Causa | Soluzione |
|---|---|---|
| `OSError: Connect call failed ('127.0.0.1', 5432)` | PostgreSQL non avviato | Avvia il servizio PostgreSQL |
| `ModuleNotFoundError: No module named 'celery_worker'` | Celery lanciato dalla root | Lancia da `cd backend` |
| `No module named 'psycopg2'` nel Celery | psycopg2-binary non installato | `python -m pip install psycopg2-binary` |
| CP-SAT → INFEASIBLE | Vincolo turni con operazioni multi-turno | Verificato e risolto: `_add_shift_nooverlap_constraints` rilassato nella v1 |
| CP-SAT → UNKNOWN dopo 30s | Timeout prima di trovare soluzione | `stop_after_first_solution=True` + hint greedy riduce a 1-5s |
| Gantt vuoto | Solver non ancora eseguito | Clicca "Schedula" nello Scenario Manager |
| BOM Explorer vuoto | `selectedMachineOrderId` null | Il Layout auto-seleziona la prima macchina |
| WebSocket sempre rosso | Connessione persa | Il frontend riprova con backoff esponenziale; controlla che il backend sia up |

---

## 15. ROADMAP — Cosa manca / cosa migliorare

### v2 — Vincolo turni corretto (priorità alta)

Reintrodurre il rispetto dei turni orari con decomposizione slot-task:
ogni operazione viene modellata come serie di sotto-task pari agli slot
disponibili dell'operatore assegnato, collegati da vincoli di sequenza con
`can_be_interrupted=True`. Questo è il modello standard per scheduling
con interruzioni notturne/weekend in CP-SAT.

### v2 — Obiettivo CP-SAT riattivato

Riattivare `_set_objective` con `FINISH_BY_DATE` / `MINIMIZE_OPERATORS` /
`MAXIMIZE_RESOURCE_UTILIZATION` una volta che il vincolo turni è stabile
e il modello trova FEASIBLE in < 10s.


### v3 — Integrazione SAP reale

Sostituire i mock con chiamate reali a SAP DM API per sincronizzazione
bidirezionale di ordini, materiali e schedule.