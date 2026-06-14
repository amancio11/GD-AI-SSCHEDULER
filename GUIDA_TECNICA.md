# MES Production Scheduler — Guida Tecnica Completa

> Documento di riferimento che spiega per filo e per segno tutto il processo:
> cosa fa il sistema, perché, come è costruito, e come ogni requisito è risolto.

---

## 1. CONTESTO DI DOMINIO — Il problema da risolvere

### La macchina industriale e la sua gerarchia

Il sistema nasce per pianificare il **montaggio di macchine industriali complesse**
come la TURBOPRESS-X500 usata come dato mock. Una macchina non è un oggetto semplice:
ha una gerarchia di sottoassemblaggi che devono essere costruiti in ordine.

```
TURBOPRESS-X500 (MachineOrder: ORD-MACH-001)
├── MA-001 "Gruppo Idraulico"  (Macroaggregato)
│   ├── AGG-001 "Cilindro principale"  (Aggregato)
│   │   ├── GRP-001 "Corpo cilindro"  (Gruppo)
│   │   │   ├── COMP-001 Guarnizione gomma      (Componente, acquisto)
│   │   │   ├── COMP-002 Vite speciale M16x80   (Componente, acquisto)
│   │   │   └── COMP-003 O-ring ...             (Componente, acquisto)
│   │   └── GRP-002 "Testata"  (Gruppo)
│   └── AGG-002 ...
├── MA-002 "Quadro Elettrico"  (Macroaggregato)
└── MA-003 "Struttura Portante"  (Macroaggregato, si monta per prima)
```

**Regola fondamentale di dominio**: non puoi montare il Gruppo Idraulico se la
Struttura Portante non è ancora pronta — fisica e logistica lo impediscono.
Questo vincolo è espresso dai **Reference Point**.

### Il problema del planner

Senza il sistema, il planner deve rispondere a mano a domande come:
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
                 └─► Claude AI (Anthropic API)
```

**Perché questa separazione?**

Il solver CP-SAT può richiedere fino a 60 secondi. Se lo eseguissimo direttamente
nell'endpoint HTTP, il browser aspetterebbe 60 secondi senza risposta e andrebbe
in timeout. Celery risolve questo: l'endpoint restituisce immediatamente un `task_id`,
il solver gira in background, e quando finisce notifica il frontend via WebSocket.

---

## 3. IL DATABASE — 19 tabelle e perché esistono

### Tabelle di dominio (cosa c'è da produrre)

| Tabella | Scopo |
|---|---|
| `machine_models` | Modello macchina (es. TX500). La struttura dei Reference Point è per-modello, non per-ordine |
| `machine_orders` | Ordine di produzione radice. Una TURBOPRESS-X500 da consegnare è un machine_order |
| `production_orders` | Tutti i livelli sotto la macchina: macroaggregati, aggregati, gruppi, componenti |
| `z_orders_link` | Replica della gerarchia BOM SAP. È la fonte di verità per le relazioni padre-figlio |

### Tabelle di routing (come si produce)

| Tabella | Scopo |
|---|---|
| `routings` | Collega un production_order al suo piano di lavorazione. Modo SIMULTANEOUS: tutte le operazioni possono andare in parallelo |
| `operations` | Singola operazione di lavorazione. Ha tipo (ELECTRICAL/MECHANICAL/GENERAL), durata pianificata e progresso |

### Tabelle di vincolo (chi può fare cosa e quando)

| Tabella | Scopo |
|---|---|
| `workcenters` | Officine fisiche (WC-MILANO, WC-TORINO, WC-BERGAMO) |
| `operators` | Operatori con skill fissa. Un ELECTRICAL fa solo operazioni ELECTRICAL nel suo workcenter |
| `skill_workcenter_mapping` | Matrice: quale skill può fare quale tipo di operazione in quale workcenter |
| `shifts` | Turni (Mattina 06-14, Pomeriggio 14-22, Notte 22-06) con pausa di 30 min |
| `operator_calendar` | Disponibilità giornaliera di ogni operatore. Può essere assente, in turno diverso, o con note |

### Tabelle di vincolo di precedenza (in quale ordine)

| Tabella | Scopo |
|---|---|
| `reference_points` | Identificatori logici che rappresentano il completamento di un macroaggregato/aggregato |
| `reference_point_precedences` | Il DAG: RP-001 (Struttura) deve completarsi prima di RP-002 (Idraulico) |

### Tabelle di scheduling (il piano generato)

| Tabella | Scopo |
|---|---|
| `schedule_scenarios` | Un "piano alternativo". È possibile averne più di uno (es. "Piano A: minimizza operatori", "Piano B: finisci entro luglio") |
| `schedule_entries` | Le singole assegnazioni: operazione X → operatore Y → dalle 08:00 alle 12:00 del giorno Z |

### Tabelle di evento (cosa cambia in corsa)

| Tabella | Scopo |
|---|---|
| `missing_components` | Componenti non ancora arrivati. Bloccano l'avanzamento del gruppo che li richiede |
| `delay_events` | Ritardi registrati: assenteismo, ritardo componente, blocco manuale |

### Tabelle AI

| Tabella | Scopo |
|---|---|
| `ai_suggestions` | Suggerimenti generati da Claude (proattivi, su richiesta, analisi ritardi) |
| `ai_chat_sessions` | Storico delle conversazioni chat con Claude, persistito (max 20 messaggi) |

---

## 4. IL CONCETTO DI SCENARIO — Attiva vs Baseline

Uno **scenario** è una fotografia completa di come potrebbero essere allocate
le operazioni nel tempo. Permette al planner di fare "what-if":
*"Cosa succede se uso solo 15 operatori invece di 20?"*

```
Scenario A: "Piano base"      → 20 operatori, finisce il 15 luglio   [BASELINE]
Scenario B: "Piano veloce"    → 20 operatori, obiettivo finire il 1° luglio [ACTIVE]
Scenario C: "Piano economico" → 12 operatori, finisce il 30 agosto
```

### `is_active` — Lo scenario che guida le operazioni reali

**Un solo scenario per macchina può essere ACTIVE alla volta.**

Lo scenario attivo è quello che i reparti stanno effettivamente seguendo.
Quando il Celery worker esegue una rischedulazione incrementale (ad esempio dopo
un ritardo), aggiorna lo scenario attivo. Il Gantt in produzione mostra l'ACTIVE.

Quando clicchi **"Attiva"**:
- Il backend setta `is_active = true` sullo scenario scelto
- De-attiva automaticamente tutti gli altri scenari della stessa macchina
  (vedi `update_scenario` in `schedule.py` — cerca gli altri con `is_active=True`)
- Da quel momento, i delay_event e le rischedulazioni agiscono su questo scenario

### `is_baseline` — Il piano di riferimento per i confronti

**Il baseline non ha vincoli di unicità** — puoi averne più di uno, ma di solito
ne esiste uno solo: è il "Piano firmato" concordato con il cliente come obiettivo
di partenza.

Serve per i confronti: la pagina "Confronto Scenari" mostra delta rispetto al
baseline (es. "Piano veloce: +3 giorni rispetto al baseline").

Quando clicchi **"Baseline"**:
- Setta `is_baseline = true` sullo scenario
- Non de-attiva gli altri (puoi avere Baseline ≠ Active)
- L'AI usa il baseline come riferimento nelle analisi storiche e nei what-if

**In pratica:** il flusso tipico è:
1. Crei "Scenario Base" → lo scheduli → diventa sia BASELINE che ACTIVE
2. Crei "Scenario Ottimizzato" → lo scheduli → lo confronti col baseline
3. Se è migliore, lo **Attivi** (diventa il piano operativo)
4. Il baseline rimane fermo come punto di riferimento contrattuale

---

## 5. IL PROCESSO DI SCHEDULING — Passo per passo

### Step 1 — Raccolta dati (pre-processing)

Prima di dare qualsiasi cosa al solver, `shift_preprocessor.py` converte
il calendario operatori in **slot di minuti interi dall'epoch**:

```python
# Esempio: Mario è disponibile il 13/06 turno mattina (06:00-13:30 con pausa)
# Viene convertito in:
mario_slots = [(8280, 8670)]  # minuti dall'epoch (oggi 00:00)
# 8280 = 138 ore = 5 giorni + 18 ore... (calcolo dall'epoch configurata)
```

CP-SAT lavora SOLO con interi — niente datetime, niente float. Questo semplifica
enormemente la formulazione matematica.

### Step 2 — Costruzione del DAG di precedenze

`dag_builder.py` usa `networkx` per costruire un grafo orientato dai reference point:

```
RP-001 (Struttura)  →  RP-002 (Idraulico)
RP-001 (Struttura)  →  RP-003 (Elettrico)
RP-002 (Idraulico)  →  RP-004 (Cilindro principale)
```

Il risultato è un **ordinamento topologico**: una lista di ordini da schedulare
in sequenza, garantendo che i prerequisiti siano sempre prima dei dipendenti.

Validazione: se il DAG contiene un ciclo (es. A→B→A) il sistema solleva un errore
e lo comunica al planner in italiano invece di crashare silenziosamente.

### Step 3 — Costruzione del modello CP-SAT

`cpsat_model_builder.py` traduce il problema in matematica:

**Per ogni operazione schedulabile:**
```python
# Quando può iniziare (earliest = oggi o quando finisce il prerequisito)
start_var = model.NewIntVar(earliest, horizon, f"start_{op.id}")
# Quando finisce
end_var = model.NewIntVar(earliest, horizon, f"end_{op.id}")
# Durata residua (considerando il progresso già fatto)
residual = max(planned_minutes * (1 - progress_pct/100), MIN_OP_MINUTES)
interval = model.NewIntervalVar(start_var, residual, end_var, f"interval_{op.id}")
```

**Per ogni (operazione, operatore) qualificato:**
```python
# Variabile booleana: "l'operatore X è assegnato all'operazione Y?"
assign[op_id][operator_id] = model.NewBoolVar(f"assign_{op_id}_{operator_id}")
```

**Vincoli aggiunti:**
1. **Almeno un operatore per operazione**: `sum(assign[op]) >= 1`
2. **Turni**: le ore in cui l'operatore è assente sono blocchi fissi che non si sovrappongono
3. **No parallelismo per operatore**: un operatore non può fare due cose contemporaneamente
4. **Precedenze DAG**: se A→B, allora `end(A) <= start(B)`
5. **Reference point**: l'operazione macchina con RP-X non parte finché l'ordine di RP-X non è completato
6. **Componenti mancanti**: `start(operazione) >= arrival_minute(componente_mancante)`

**Modalità SIMULTANEOUS:**
Quando più operatori sono assegnati alla stessa operazione, la durata effettiva
si riduce: 2 operatori dimezzano il tempo, 3 lo riducono a 1/3, ecc.
Questo è espresso con `AddDivisionEquality(dur_var, residual, n_assigned)`.

### Step 4 — Funzione obiettivo e risoluzione

Il planner sceglie l'obiettivo al momento della creazione dello scenario:

| Obiettivo | Cosa minimizza/massimizza | Quando usarlo |
|---|---|---|
| `FINISH_BY_DATE` | Minimizza makespan + vincolo hard sulla data | Deadline contrattuale |
| `MINIMIZE_OPERATORS` | Minimizza numero di operatori usati | Ottimizzazione costi |
| `MAXIMIZE_RESOURCE_UTILIZATION` | Massimizza ore lavorate / disponibili | Evitare idle time |
| `CUSTOM` | Combinazione pesata configurabile | Scenari complessi |

Il solver CP-SAT esplora lo spazio delle soluzioni con backtracking e propagazione
di vincoli. Ha un timeout configurabile (default: 60 secondi). Se trova una soluzione
ottimale o scade il timeout, restituisce la migliore soluzione trovata.

**Se INFEASIBLE**: il sistema non crasha — usa `infeasibility_analyzer.py` per
trovare il sotto-insieme minimo di vincoli in conflitto e lo spiega in italiano
al planner (es. "Impossibile rispettare la data: mancano 3 operatori elettrici
per la settimana 23/06-27/06").

### Step 5 — Salvataggio delle schedule_entries

`solution_extractor.py` traduce la soluzione matematica del solver in righe
nella tabella `schedule_entries`:

```
Operazione OP-042 "Collaudo idraulico"
  → Operatore: Mario Rossi (MULTI, WC-MILANO)
  → Inizio: 2026-06-16 08:00
  → Fine:   2026-06-16 14:30
  → Status: SCHEDULED
```

### Step 6 — Analisi proattiva AI

Dopo ogni scheduling, `proactive_analyzer.py` analizza il piano appena creato
senza aspettare che il planner chieda. Controlla:
- Operatori con utilizzo > 90% (rischio burnout / zero flessibilità)
- Percorso critico: sequenze di operazioni senza slack dove un ritardo causa ritardo finale
- Finestre critiche: periodi dove molte operazioni dipendono da pochi operatori
- Componenti mancanti che impattano operazioni già schedulate

Se trova anomalie, crea `ai_suggestions` nella tabella e notifica via WebSocket
il badge AI nell'header del frontend.

---

## 6. LA RISCHEDULAZIONE — Quando il piano cambia in corsa

La vita reale non è come il piano. Tre eventi tipici richiedono una rischedulazione:

### Evento 1: Componente in ritardo

```
Valvola idraulica VLV-2200 arriverà il 23/06 invece del 16/06 (+7 giorni)
```

Il planner registra un `delay_event` con `event_type=COMPONENT_DELAY`.
Il sistema:
1. Aggiorna `missing_components` con la nuova data prevista
2. Lancia automaticamente `reschedule_on_delay.delay(scenario_id)` su Celery
3. Il solver ri-parte tenendo fisse le operazioni IN_PROGRESS o COMPLETED
4. Produce un nuovo piano con le operazioni impattate spostate dopo il 23/06
5. Notifica il frontend via WebSocket

### Evento 2: Operatore assente

```
Mario Rossi (MULTI) assente dal 18/06 al 20/06 per malattia
```

Il planner modifica `operator_calendar` per quei giorni (`is_available=False`).
La rischedulazione ridistribuisce le sue operazioni agli altri operatori qualificati
disponibili in quei giorni.

### Evento 3: Ritardo manuale su operazione

```
Collaudo idraulico: ci vuole il doppio del previsto (100% in più)
```

Il planner aggiorna `actual_duration_minutes` dell'operazione.
Il solver usa la durata aggiornata come vincolo per tutte le operazioni successive.

---

## 7. IL LAYER AI — Claude Sonnet 4.6

Il sistema usa Claude non come decorazione ma come strumento operativo con 7 modalità:

### Modalità 1: Ottimizzazione su richiesta
**Trigger**: Planner clicca "Ottimizza con AI" su uno scenario
**Cosa fa**: Claude analizza il piano corrente, identifica colli di bottiglia,
suggerisce quali operatori spostare e perché, propone obiettivo alternativo

### Modalità 2: Analisi proattiva (automatica)
**Trigger**: Automaticamente dopo ogni run del solver CP-SAT
**Cosa fa**: Controlla il piano senza che nessuno lo chieda, segnala anomalie

### Modalità 3: Analisi ritardo
**Trigger**: Creazione di un delay_event
**Cosa fa**: Valuta l'impatto del ritardo sulla data di consegna finale,
suggerisce azioni compensative (es. "attivare il turno notturno per 3 giorni")

### Modalità 4: Chat libera
**Trigger**: Il planner scrive nel pannello AI
**Cosa fa**: Risponde a domande in linguaggio naturale sul piano, sulla macchina,
sugli operatori. Mantiene la storia della conversazione (max 20 messaggi)

### Modalità 5: What-if
**Trigger**: Il planner crea uno scenario "ipotetico" con parametri diversi
**Cosa fa**: Confronta due scenari, spiega le differenze, raccomanda quale scegliere

### Modalità 6: Analisi storica
**Trigger**: Quando esistono 3+ scenari o manuale
**Cosa fa**: Identifica pattern ricorrenti (es. "le operazioni ELECTRICAL WC-TORINO
sono sistematicamente in ritardo del 15% rispetto al pianificato")

### Modalità 7: Spiega barra Gantt
**Trigger**: Click su una barra nel Gantt
**Cosa fa**: Spiega in italiano perché quell'operazione è programmata in quel momento,
quali vincoli l'hanno posizionata lì, cosa succederebbe se fosse spostata

---

## 8. IL FRONTEND — Come si naviga il sistema

### Dashboard
Panoramica KPI della macchina selezionata: progresso complessivo, operazioni completate,
componenti mancanti, ritardi attivi. Si aggiorna ogni 30 secondi.

### BOM Explorer
L'albero gerarchico completo della macchina. Ogni nodo mostra lo stato
(PLANNED/IN_PROGRESS/COMPLETED/BLOCKED/MISSING). I nodi con componenti mancanti
sono evidenziati. Cliccando un nodo si vedono le sue operazioni e il routing.

### Gantt View
Due modalità:
- **Per Operatore**: una riga per ogni operatore, le barre mostrano le sue operazioni nel tempo
- **Per Ordine**: una riga per ogni ordine di produzione, mostra le operazioni di quel sottoassemblaggio

Il percorso critico può essere evidenziato (operazioni senza slack temporale).
Clicking una barra lancia l'AI "Spiega entry".

### Calendario Operatori
Griglia mensile per ogni operatore. Il planner può modificare la disponibilità
giorno per giorno: presente/assente, turno assegnato, note.

### Reference Point Config
Visualizzazione React Flow del DAG di precedenze. Il planner può aggiungere/rimuovere
archi. Il sistema valida che non si creino cicli prima di salvare.

### Scenario Manager
Gestione degli scenari di scheduling:
- **Nuovo Scenario**: sceglie obiettivo e data target, avvia CP-SAT
- **Schedula**: ri-esegue il solver su uno scenario esistente
- **Attiva**: rende questo scenario il piano operativo corrente
- **Baseline**: marca questo scenario come riferimento per i confronti
- **Confronto**: confronta due scenari con delta tabellare + analisi AI
- **What-if**: domanda AI "cosa succederebbe se...?"

### Delay Manager
Registro degli eventi di ritardo. Ogni ritardo mostra l'impatto stimato sulla
data di consegna e se richiede rischedulazione.

### Componenti Mancanti
Lista dei materiali non ancora arrivati con data prevista. Il planner può
confermare l'arrivo (è arrivato) o aggiornare la data attesa.

### AI Assistant
Pannello di chat libera con Claude. Disponibile anche come slide-over globale
(icona AI nell'header) accessibile da qualsiasi pagina.

### Export
Scarica il piano in tre formati:
- **CSV**: compatibile Excel italiano (BOM UTF-8, separatore ;)
- **JSON-SAP**: struttura pronta per importazione in SAP Digital Manufacturing
- **PDF**: report impaginato con KPI, schedule per operatore, mancanti, ritardi

### DB Admin
Pagina interna di amministrazione: griglia per visualizzare e modificare
direttamente le 19 tabelle del database senza usare psql. Utile in sviluppo.

---

## 9. WEBSOCKET — Il canale in tempo reale

Il frontend apre una connessione WebSocket su `/ws/{room_id}` dove `room_id` è
l'UUID dell'ordine macchina selezionato.

**Messaggi che il server invia al frontend:**

```json
{"type": "RESCHEDULE_COMPLETE", "scenario_id": "uuid..."}
→ Il Gantt si aggiorna automaticamente

{"type": "AI_SUGGESTION_NEW", "count": 2, "scenario_id": "uuid..."}
→ Il badge AI nell'header mostra il contatore

{"type": "SCHEDULE_INFEASIBLE", "conflicts": ["Operatori insufficienti..."]}
→ Viene mostrato un messaggio di errore con la spiegazione
```

Il frontend implementa reconnect con backoff esponenziale: se la connessione cade,
riprova dopo 1s, 2s, 4s, 8s... fino a 30s di attesa massima (max 10 tentativi).

---

## 10. REQUISITI E COME SONO RISOLTI

| Requisito | Soluzione |
|---|---|
| Schedulare 259 operazioni rispettando skill, turni e precedenze | OR-Tools CP-SAT con variabili booleane di assegnazione e vincoli NoOverlap |
| Operatore non può lavorare in due posti contemporaneamente | AddNoOverlap sugli optional interval vars per operatore |
| Struttura Portante prima del Gruppo Idraulico | DAG topologico + vincolo `end(pred) ≤ start(succ)` |
| Componente mancante blocca le operazioni del gruppo | `start(op) ≥ arrival_minute(componente)` come vincolo CP-SAT |
| Rischedulazione incrementale senza perdere il progresso | Le operazioni COMPLETED/IN_PROGRESS sono fissate come costanti nel modello |
| Risposta immediata nonostante 60s di calcolo | Celery task asincrono + WebSocket per notifica completamento |
| Spiegare in italiano perché un piano è impossibile | infeasibility_analyzer trova il Minimal Infeasible Subset (MIS) e lo passa a Claude |
| Confrontare scenari alternativi | Tabella schedule_scenarios + endpoint /scenarios/compare con delta |
| Export per SAP | JSON-SAP con struttura specifica dell'endpoint /api/export/scenario/{id}/json-sap |
| PC aziendale senza admin | PostgreSQL e Redis portable (ZIP, no installer, no servizi Windows) |
| pip.exe bloccato dalle policy | `python.exe -m pip` bypassa il blocco sugli eseguibili |
| WeasyPrint richiede GTK (non disponibile) | Sostituito con reportlab (cross-platform, zero dipendenze native) |
| Alembic + asyncpg = errori ENUM | env.py migrato a psycopg2 sync + postgresql.ENUM(create_type=False) + DO-block |

---

## 11. DATI MOCK — TURBOPRESS-X500

Il seed (`backend/app/db/seed.py`) è **idempotente**: si può rieseguire più volte
senza duplicare dati (`INSERT ... ON CONFLICT DO NOTHING`). Usa `random.seed(42)`
per garantire dati deterministici, e `uuid.uuid5(namespace, name)` per UUID stabili.

**Cosa viene creato:**
- 1 modello macchina TX500
- 1 ordine macchina ORD-MACH-001 "TURBOPRESS-X500"
- 3 macroaggregati → ~15 aggregati → ~40 gruppi → ~150 componenti
- 20 operatori su 3 workcenter (Milano, Torino, Bergamo) con skill mista
- 3 turni (Mattina/Pomeriggio/Notte) con pausa 30 minuti
- 560 righe di calendar (operatori × giorni)
- 10 Reference Point con DAG valido (no cicli)
- 5 componenti mancanti pre-settati con date di arrivo nel futuro
- 1 scenario "Scenario Base" pronto per essere schedulato

---

## 12. COME FARE LA PRIMA SCHEDULAZIONE

Prerequisiti: PostgreSQL attivo, Redis attivo, backend attivo, Celery worker attivo.

1. Apri http://localhost:5173
2. L'header mostra automaticamente "ORD-MACH-001 — TURBOPRESS-X500"
3. Vai su **Scenari** (icona Layers nella sidebar)
4. Vedi "Scenario Base" con il badge `ACTIVE`
5. Clicca il pulsante blu **Schedula** sulla card
6. Il pulsante mostra "Scheduling in corso…" con spinner
7. Nel terminale del Celery worker vedi l'attività CP-SAT
8. Dopo 10-60 secondi, il frontend riceve la notifica WebSocket e aggiorna
9. Vai su **Gantt** — le barre delle operazioni sono visibili
10. Vai su **Dashboard** — i KPI mostrano il progresso stimato
11. L'AI ha già analizzato il piano in modo proattivo: controlla il badge AI

---

## 13. ERRORI COMUNI E SOLUZIONI

| Errore | Causa | Soluzione |
|---|---|---|
| `OSError: Connect call failed ('127.0.0.1', 5432)` | PostgreSQL non avviato | `& "$env:USERPROFILE\Downloads\pgsql\bin\pg_ctl.exe" -D "...\pgsql-data" start` |
| `ModuleNotFoundError: No module named 'celery_worker'` | Celery lanciato dalla root invece che da `backend/` | `cd backend` prima di lanciare Celery |
| `No module named 'psycopg2'` nel Celery | psycopg2-binary non nel .venv | `.venv\Scripts\python.exe -m pip install psycopg2-binary` |
| `PUT 405 Method Not Allowed` su scenario | Frontend usava PUT, backend espone solo PATCH | Già corretto: le mutation usano `.patch()` |
| `FilePdf` not exported da lucide-react | Icona inesistente | Già corretto: sostituita con `FileType` |
| Gantt vuoto | Solver non ancora eseguito | Clicca "Schedula" in Scenario Manager |
| BOM Explorer vuoto | `selectedMachineOrderId` null | Il Layout ora auto-seleziona la prima macchina |
| WebSocket sempre rosso | `useWebSocket` non montato | Layout ora chiama `useWebSocket()` al mount |

---

## 14. STRUTTURA FILE DEL PROGETTO

```
gd-scheduler/
├── .github/
│   ├── copilot-instructions.md   ← Contesto persistente per l'AI di sviluppo
│   ├── copilot-agent-steps.md    ← I 21 step di implementazione usati
│   └── esecuzione-github-copilot.md ← Log dell'esecuzione
│
├── backend/
│   ├── app/
│   │   ├── api/routes/
│   │   │   ├── orders.py          ← BOM, machine orders, operations
│   │   │   ├── schedule.py        ← Scenari, schedule entries, Gantt data, run CP-SAT
│   │   │   ├── operators.py       ← Operatori, turni, calendario
│   │   │   ├── reference_points.py ← Reference point e DAG precedenze
│   │   │   ├── delays.py          ← Delay events
│   │   │   ├── missing_components.py ← Componenti mancanti
│   │   │   ├── ai.py              ← Tutti gli endpoint AI (7 modalità)
│   │   │   ├── export.py          ← CSV, JSON-SAP, PDF
│   │   │   └── admin.py           ← DB Admin (solo sviluppo)
│   │   │
│   │   ├── core/
│   │   │   ├── scheduler/
│   │   │   │   ├── dag_builder.py         ← Costruisce il DAG networkx dai reference point
│   │   │   │   ├── shift_preprocessor.py  ← Converte calendario in slot interi
│   │   │   │   ├── cpsat_model_builder.py ← Costruisce e risolve il modello CP-SAT
│   │   │   │   ├── objective_configurator.py ← Imposta la funzione obiettivo
│   │   │   │   ├── solution_extractor.py  ← Traduce la soluzione in schedule_entries
│   │   │   │   ├── infeasibility_analyzer.py ← Trova i vincoli in conflitto
│   │   │   │   └── reschedule_engine.py   ← Celery task: pipeline di rischedulazione
│   │   │   │
│   │   │   ├── ai/
│   │   │   │   ├── claude_client.py       ← Wrapper Anthropic SDK con retry
│   │   │   │   ├── prompt_builder.py      ← Costruisce i prompt per ogni modalità
│   │   │   │   ├── context_extractor.py   ← Serializza il contesto DB per Claude
│   │   │   │   ├── response_parser.py     ← Valida e parsa le risposte JSON di Claude
│   │   │   │   ├── proactive_analyzer.py  ← Celery task: analisi proattiva post-scheduling
│   │   │   │   └── chat_session_manager.py ← Gestisce la storia conversazione
│   │   │   │
│   │   │   └── export/
│   │   │       ├── csv_exporter.py        ← (non più usato, logica in export.py)
│   │   │       └── json_sap_exporter.py   ← (non più usato, logica in export.py)
│   │   │
│   │   ├── models/                ← SQLAlchemy ORM (una class per tabella)
│   │   ├── schemas/               ← Pydantic v2 (request/response validation)
│   │   ├── db/
│   │   │   ├── session.py         ← Engine async, get_db dependency
│   │   │   └── seed.py            ← Seed TURBOPRESS-X500 (idempotente)
│   │   └── websocket/
│   │       └── manager.py         ← ConnectionManager per broadcast WebSocket
│   │
│   ├── alembic/
│   │   ├── env.py                 ← Usa psycopg2 sync (non asyncpg) per le migrazioni
│   │   └── versions/
│   │       └── 001_initial_schema.py ← Crea tutte e 19 le tabelle + ENUM
│   │
│   ├── celery_worker.py           ← Configura l'app Celery (broker Redis)
│   ├── requirements.txt
│   └── .env                       ← Variabili locali (non committato)
│
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Dashboard.tsx
│       │   ├── GanttView.tsx
│       │   ├── BOMExplorer.tsx
│       │   ├── OperatorCalendar.tsx
│       │   ├── ReferencePointConfig.tsx
│       │   ├── ScenarioManager.tsx
│       │   ├── DelayManager.tsx
│       │   ├── MissingComponents.tsx
│       │   ├── AIAssistant.tsx
│       │   ├── ExportPage.tsx
│       │   └── DBAdmin.tsx         ← Griglia di amministrazione DB
│       │
│       ├── components/
│       │   ├── shared/
│       │   │   ├── Layout.tsx      ← Sidebar, header, WebSocket hook, machine selector
│       │   │   ├── AISidebar.tsx   ← Pannello AI slide-over globale
│       │   │   └── ToastContainer.tsx
│       │   ├── gantt/
│       │   │   ├── GanttByOperator.tsx
│       │   │   └── GanttByOrder.tsx
│       │   ├── bom/
│       │   │   └── BOMTree.tsx
│       │   └── ai/
│       │       └── SuggestionsList.tsx
│       │
│       ├── api/
│       │   ├── client.ts           ← axios con interceptor e X-Request-ID
│       │   ├── types.ts            ← Interfacce TypeScript speculari ai modelli
│       │   └── hooks/
│       │       ├── useOrders.ts    ← useMachineOrders, useBOMTree, useOrderOperations
│       │       ├── useSchedule.ts  ← useScenarios, useGanttData, useScheduleScenario
│       │       ├── useOperators.ts ← useOperators, useCalendar
│       │       ├── useMissing.ts   ← useMissingComponents
│       │       ├── useReferencePoints.ts
│       │       └── useAi.ts
│       │
│       ├── store/
│       │   ├── uiStore.ts         ← sidebar, tema, websocketConnected
│       │   ├── scheduleStore.ts   ← activeScenarioId, ganttViewMode
│       │   ├── machineStore.ts    ← selectedMachineOrderId
│       │   └── aiStore.ts         ← unreadCount suggerimenti AI
│       │
│       └── hooks/
│           ├── useWebSocket.ts    ← Gestisce la connessione WS con backoff
│           └── useToast.ts
│
├── start-local.ps1               ← Script PowerShell per avvio locale completo
├── docker-compose.yml
└── .env.example
```
