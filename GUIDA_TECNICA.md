# MES Production Scheduler — Guida Tecnica Completa

> Documento di riferimento aggiornato alla versione attuale del sistema.
> Descrive il dominio, l'architettura, il processo di scheduling, le scelte
> tecniche (incluse quelle prese durante il debug) e come ogni requisito è risolto.
>
> **Ultimo aggiornamento**: Giugno 2026 — Aggiunta logica `parent_wait_constraints`
> (Tipo A) e correzione completa della semantica dei Reference Point.

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
| `reference_points` | Identificatori logici che rappresentano il completamento di un sottoassemblaggio |
| `reference_point_precedences` | Il DAG intra-livello: RP-M-01 (Struttura) deve completarsi prima di RP-M-02 (Idraulico) |

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

### Reference Point — la struttura corretta (v2)

I Reference Point sono il meccanismo che modella i vincoli di montaggio tra livelli BOM.
**Ogni RP punta esclusivamente ai figli diretti dell'ordine che lo contiene.**

| Livello ordine | RP puntano a |
|---|---|
| MACHINE (ORD-MACH-001) | I 3 macroaggregati: MA-001, MA-002, MA-003 |
| MACROAGGREGATE MA-001 | I 5 aggregati figli: AGG-001..005 |
| MACROAGGREGATE MA-002 | I 4 aggregati figli: AGG-006..009 |
| MACROAGGREGATE MA-003 | I 3 aggregati figli: AGG-010..012 |
| AGGREGATE AGG-001 | I 3 gruppi figli: GRP-001..003 |
| ... (ogni aggregato verso i propri gruppi) | |
| GROUP | **Nessun RP** — figli sono componenti senza routing |

**Totale: 55 Reference Point, 43 archi DAG.**

La tabella `reference_point_precedences` definisce un DAG **intra-livello**: gli archi
esistono solo tra RP dello stesso livello padre. Es. nel livello MACHINE:
`RP-M-01 (→MA-003)` precede `RP-M-02 (→MA-001)` e `RP-M-03 (→MA-002)`.

### Semantica del Reference Point — la regola fondamentale

> L'operazione dell'ordine padre che ha `reference_point_id = RP-X` **non può iniziare**
> finché l'ordine target del RP-X **e tutti i suoi figli BOM ricorsivamente** non sono completati.

Esempio concreto: l'operazione di MA-001 con `reference_point_id = RP-MA1-03` punta ad AGG-003.
Quella operazione non può iniziare finché AGG-003, GRP-008, GRP-009 e GRP-010 non
sono tutti terminati.

---

## 5. IL PROCESSO DI SCHEDULING — Passo per passo con esempio

### Il caso concreto: TURBOPRESS-X500, primo avvio

```
Input:
  259 operazioni schedulabili (nessuna COMPLETED)
  20 operatori (WC-MILANO 8, WC-TORINO 7, WC-BERGAMO 5)
  5 componenti mancanti (VLV-2200 arriva fra 7 giorni, etc.)
  DAG RP: MA-003 → MA-001, MA-003 → MA-002 (al livello MACHINE)
           + DAG intra-livello per ogni macroaggregato e aggregato

Obiettivo: trovare un piano (chi fa cosa, quando) che rispetti tutti i vincoli
```

### Step 1 — Raccolta dati e pre-processing

`shift_preprocessor.py` converte il calendario operatori in **slot di minuti interi
dall'epoch** (dove epoch = oggi alle 00:00):

```python
# Mario Rossi disponibile lunedì mattina (06:00-13:30 con pausa 30min)
mario_slots = [(360, 480), (510, 810)]  # due blocchi: pre-pausa e post-pausa
# Il CP-SAT lavora SOLO con interi — niente datetime, niente float
```

### Step 2 — Calcolo rp_order_constraints e parent_wait_constraints (Step 4d)

Questa è la fase più critica. Si costruiscono **due tipi distinti di vincoli**,
entrambi necessari per la correttezza del piano.

---

#### TIPO A — "Il padre aspetta il figlio" (`parent_wait_constraints`)

**Semantica**: ogni operazione con `reference_point_id` non null deve aspettare
che l'ordine target del RP (e tutti i suoi figli ricorsivi) siano completati.

**Esempio dettagliato**:

```
op-MACH-2: "Collaudo Gruppo Idraulico"
  reference_point_id = RP-M-02
  RP-M-02 punta a MA-001 "Gruppo Idraulico"

Quindi:
  ops_target = tutte le op schedulabili di MA-001 + AGG-001..005 + GRP-001..020
             = [op-MA001-1, op-MA001-2, ..., op-AGG001-1, ..., op-GRP020-3]
             = 87 operazioni

Vincolo CP-SAT:
  pw_completion_42 = max(op_end[op-MA001-1], op_end[op-MA001-2], ..., op_end[op-GRP020-3])
  op_start[op-MACH-2] >= pw_completion_42
```

Lo stesso vale per ogni livello:

```
op-MA001-3 (RP-MA1-03 → AGG-003):
  ops_target = op di AGG-003 + GRP-008 + GRP-009 + GRP-010
  start(op-MA001-3) >= max(end di tutte quelle op)

op-AGG001-2 (RP-A001-02 → GRP-002):
  ops_target = op di GRP-002
  start(op-AGG001-2) >= max(end di op-GRP002-*)
```

**Questo tipo di vincolo era MANCANTE nell'implementazione precedente.**

---

#### TIPO B — "Ordinamento intra-livello tra rami" (`rp_order_constraints`)

**Semantica**: il DAG `reference_point_precedences` definisce un ordine tra
i figli di uno stesso padre. Se RP-M-01 (→MA-003) precede RP-M-02 (→MA-001),
allora tutto il sotto-albero di MA-003 deve finire prima che qualsiasi operazione
del sotto-albero di MA-001 possa iniziare.

**Esempio dettagliato**:

```
Arco DAG: RP-M-01 → RP-M-02
  RP-M-01 punta a MA-003 "Struttura Portante"
  RP-M-02 punta a MA-001 "Gruppo Idraulico"

ops_pred = tutte le op di MA-003 + AGG-010..012 + GRP-032..040 = 45 op
ops_succ = tutte le op di MA-001 + AGG-001..005 + GRP-001..020 = 87 op

Vincolo CP-SAT (efficiente con variabile ausiliaria):
  rp_completion_3 = max(op_end[op] for op in ops_pred)  # quando finisce MA-003
  for op in ops_succ:
      op_start[op] >= rp_completion_3
```

---

#### La cascata completa — timeline dell'esempio

```
T=0  (oggi, minuto 0)
│
├─ GRP-032..040 iniziano SUBITO (non hanno RP, sono le foglie di MA-003)
│   Mario (WC-BERGAMO): op-GRP032-1 [90min] → finisce T=90
│   Luigi (WC-BERGAMO): op-GRP033-1 [120min] → finisce T=120
│   ...tutti i gruppi di MA-003 in parallelo...
│
│  [TIPO A] op-MA003-1 ha RP-MA3-01 → AGG-010
│           ops_target di AGG-010 = {op-AGG010-1, op-AGG010-2}
│           AGG-010 però aspetta i suoi gruppi (GRP-032..034) via altro Tipo A
│           ∴ start(op-AGG010-1) >= max(end GRP-032, GRP-033, GRP-034)
│
├─ T≈120: AGG-010, AGG-011, AGG-012 possono iniziare (i loro gruppi sono pronti)
│   op-AGG010-1: [180min] → finisce T≈300
│   op-AGG011-1: [150min] → finisce T≈270
│   op-AGG012-1: [200min] → finisce T≈320
│
│  [TIPO A] op-MA003-1 ha RP-MA3-01 → AGG-010
│           start(op-MA003-1) >= max(end op-AGG010-*) = T≈300
│           Analogamente per op-MA003-2 (→AGG-011) e op-MA003-3 (→AGG-012)
│
├─ T≈320: MA-003 può iniziare le sue operazioni (tutti gli aggregati pronti)
│   op-MA003-1: [240min] → finisce T≈560
│   op-MA003-2: [180min] → finisce T≈500
│   op-MA003-3: [200min] → finisce T≈520
│
│  [TIPO B] DAG RP-M-01→RP-M-02: MA-003 tree deve finire prima di MA-001 tree
│           rp_completion_MA003 = max(end MA-003 ops) = T≈560
│           → TUTTE le op di MA-001 + AGG-001..005 + GRP-001..020
│             non possono iniziare prima di T≈560
│
├─ T≈560: MA-001 e MA-002 sbloccati (MA-003 completato)
│   ECCEZIONE: GRP-001 ha VLV-2200 mancante → arriva T=10080 (7 giorni)
│   GRP-001: start >= max(560, 10080) = 10080
│   GRP-002..020: start >= 560 (possono iniziare)
│
│   ...MA-001 si costruisce bottom-up (gruppi → aggregati → operazioni MA-001)...
│
├─ T≈15000: MA-001 completato (include attesa VLV-2200)
│
│  [TIPO A] op-MACH-2 ha RP-M-02 → MA-001
│           start(op-MACH-2) >= max(end di tutto il sotto-albero MA-001) = T≈15000
│
└─ T≈15200: "Collaudo Gruppo Idraulico" può iniziare e terminare
```

---

### Step 3 — Costruzione del modello CP-SAT

`cpsat_model_builder.py` traduce il problema in matematica.

**Per ogni operazione schedulabile:**

```python
residual = max(planned_minutes * (1 - progress_pct/100), MIN_OP_DURATION)
start_var    = model.NewIntVar(0, horizon, f"start_{op.id}")
end_var      = model.NewIntVar(0, horizon, f"end_{op.id}")
interval_var = model.NewIntervalVar(start_var, residual, end_var, f"iv_{op.id}")
```

**Vincoli applicati nell'ordine:**

1. `_add_assignment_constraints()` — ogni op ha esattamente 1 operatore qualificato (stessa WC + skill compatibile); durata fissa
2. `_add_shift_nooverlap_constraints()` — operatori senza slot disponibili vengono esclusi (v1 rilassata)
3. `_add_operator_nooverlap_constraints()` — un operatore non può fare due op contemporaneamente
4. `_add_precedence_constraints()` — precedenze dirette op→op (`precedence_pairs`, attualmente vuoti) + `blocking_constraints` legacy
5. `_add_rp_order_constraints()` — **Tipo B**: ordinamento intra-livello via DAG RP
6. `_add_parent_wait_constraints()` — **Tipo A**: op padre aspetta completamento figlio ← **NUOVO**
7. `_add_missing_component_constraints()` — `start(op) >= arrival_minute(componente)`

**Perché `_add_precedence_constraints()` non è stato eliminato:**
Gestisce i `precedence_pairs` diretti op→op e i `blocking_constraints` per-operazione,
meccanismi utili per override manuali e future estensioni.

---

#### Come funziona `_add_rp_order_constraints()` (Tipo B)

Per ogni coppia `(ops_pred, ops_succ)` in `rp_order_constraints`:

```python
completion = model.NewIntVar(0, horizon, f"rp_completion_{idx}")
model.AddMaxEquality(completion, [op_end[id] for id in active_pred])
for succ_id in active_succ:
    model.Add(op_start[succ_id] >= completion)
```

Approccio O(|pred| + |succ|) invece di O(|pred| × |succ|) pairwise.
Funziona anche al primo run (nessuna `schedule_entry` preesistente).

---

#### Come funziona `_add_parent_wait_constraints()` (Tipo A) — NUOVO

Per ogni coppia `(ops_target, parent_op_id)` in `parent_wait_constraints`:

```python
# ops_target: tutte le op schedulabili dell'ordine figlio target + suoi discendenti
# parent_op_id: l'operazione del padre con reference_point_id = RP che punta al figlio

active_target = [op_id for op_id in ops_target if op_id in v.op_end]

if not active_target:
    # Il target è già tutto COMPLETED → nessun vincolo necessario
    continue

completion = model.NewIntVar(0, self.horizon, f"pw_completion_{idx}")
model.AddMaxEquality(completion, [v.op_end[op_id] for op_id in active_target])
model.Add(v.op_start[parent_op_id] >= completion)
```

---

### Step 4 — Hint greedy e risoluzione

Prima di lanciare il solver, `_add_solution_hints()` fornisce una soluzione
iniziale calcolata in modo greedy (assegna ogni op al primo operatore disponibile
in ordine topologico). Questo riduce il tempo da FEASIBLE da ~30s a 1-5s.

Il solver gira con `stop_after_first_solution=True` — cerca la prima soluzione
fattibile, non l'ottima (l'obiettivo è disabilitato in attesa del fix turni v2).

**Se INFEASIBLE**: `infeasibility_analyzer.py` trova i vincoli in conflitto e li
spiega in italiano al planner.

### Step 5 — Salvataggio e notifica

`solution_extractor.py` traduce la soluzione in `schedule_entries`, poi il Celery
task notifica via WebSocket `{"type": "RESCHEDULE_COMPLETE", "scenario_id": ...}`.

---

## 6. MODIFICHE PUNTUALI AL CODICE — Versione corrente

### MODIFICA 1: `reschedule_engine.py` — Step 4d

Aggiungere **dopo** il loop che costruisce `rp_order_constraints`, il calcolo
dei `parent_wait_constraints`:

```python
# ── Tipo A: ogni op con RP deve aspettare il completamento del target ────────
parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] = []

for op_sc in schedulable_ops:
    if op_sc.reference_point_id is None:
        continue
    target_po_id = rp_id_to_po_id.get(op_sc.reference_point_id)
    if target_po_id is None:
        logger.debug(
            "RP %s → nessun ordine target per op %s — skip",
            op_sc.reference_point_id, op_sc.id,
        )
        continue
    ops_target = _collect_ops_recursive(
        target_po_id, children_map, ops_by_order, schedulable_op_ids
    )
    if not ops_target:
        logger.debug("RP target %s: nessuna op schedulabile — skip", target_po_id)
        continue
    parent_wait_constraints.append((ops_target, op_sc.id))
    logger.debug(
        "Parent-wait: op %s aspetta %d op del target %s",
        op_sc.id, len(ops_target), target_po_id,
    )

logger.info("Step 4d: %d parent_wait_constraints generati", len(parent_wait_constraints))
```

Passare il parametro a `build_and_solve`:

```python
solution = builder.build_and_solve(
    objective_mode=scenario.objective_mode or "FINISH_BY_DATE",
    params={},
    blocking_constraints={},
    scenario_id=scenario_id,
    rp_order_constraints=rp_order_constraints,
    parent_wait_constraints=parent_wait_constraints,   # ← NUOVO
)
```

---

### MODIFICA 2: `cpsat_model_builder.py` — tre punti

**2a. Nel `__init__`, aggiungere il campo:**

```python
self.parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] = []
```

**2b. Nella firma di `build_and_solve`, aggiungere il parametro:**

```python
def build_and_solve(
    self,
    objective_mode: str,
    params: dict,
    blocking_constraints: dict[uuid.UUID, int] | None = None,
    scenario_id: uuid.UUID | None = None,
    rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] | None = None,
    parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] | None = None,  # ← NUOVO
) -> CpsatSolution:
```

**2b. Nel corpo di `build_and_solve`, salvare e chiamare:**

```python
self._blocking_constraints = blocking_constraints or {}
self.rp_order_constraints = rp_order_constraints or []
self.parent_wait_constraints = parent_wait_constraints or []   # ← NUOVO
```

Nella sequenza di chiamate:

```python
self._add_rp_order_constraints()
self._add_parent_wait_constraints()   # ← NUOVO (dopo rp_order, prima di missing)
self._add_missing_component_constraints()
```

**2c. Aggiungere il metodo `_add_parent_wait_constraints()`:**

```python
def _add_parent_wait_constraints(self) -> None:
    """Vincolo Tipo A: op del padre aspetta completamento del figlio target.

    Per ogni (ops_target, parent_op_id) in self.parent_wait_constraints:
      - ops_target: tutte le op schedulabili dell'ordine puntato dal RP + figli BOM
      - parent_op_id: l'op del livello padre che ha reference_point_id = quel RP

    Semantica: start(parent_op) >= max(end(op) for op in ops_target)

    Se ops_target è vuoto (tutto COMPLETED), nessun vincolo necessario.
    Se parent_op_id non è nel modello (anch'essa COMPLETED), skip.
    """
    assert self.vars is not None
    v = self.vars
    model = self.model

    import logging
    _log = logging.getLogger(__name__)

    enforced = 0
    skipped = 0

    for idx, (ops_target, parent_op_id) in enumerate(self.parent_wait_constraints):
        active_target = [op_id for op_id in ops_target if op_id in v.op_end]

        if not active_target:
            skipped += 1
            continue

        if parent_op_id not in v.op_start:
            skipped += 1
            continue

        completion = model.NewIntVar(0, self.horizon, f"pw_completion_{idx}")
        model.AddMaxEquality(completion, [v.op_end[op_id] for op_id in active_target])
        model.Add(v.op_start[parent_op_id] >= completion)
        enforced += 1

    _log.info(
        "Parent-wait constraints: %d enforced, %d skipped",
        enforced, skipped,
    )
```

---

## 7. RIEPILOGO DEI VINCOLI CP-SAT — Tabella completa

| # | Vincolo | Metodo | Stato |
|---|---|---|---|
| 1 | Ogni op ha ≥1 operatore qualificato (WC + skill) | `_add_assignment_constraints()` | ✅ Implementato |
| 2 | Operatori senza slot esclusi dall'assegnazione | `_add_shift_nooverlap_constraints()` | ✅ Implementato (v1 rilassata) |
| 3 | Un operatore non fa due op contemporaneamente | `_add_operator_nooverlap_constraints()` | ✅ Implementato |
| 4 | Precedenze dirette op→op (`precedence_pairs`) | `_add_precedence_constraints()` | ✅ Implementato (pairs vuoti) |
| 5 | **Tipo B**: ordinamento intra-livello via DAG RP | `_add_rp_order_constraints()` | ✅ Implementato |
| 6 | **Tipo A**: op padre aspetta figlio target (per ogni RP) | `_add_parent_wait_constraints()` | ⚠️ **DA AGGIUNGERE** |
| 7 | Op bloccata finché componente mancante non arriva | `_add_missing_component_constraints()` | ✅ Implementato |

---

## 8. DIFFERENZA TRA TIPO A E TIPO B — Chiarimento definitivo

```
Scenario: MA-003 deve finire prima di MA-001 (regola di montaggio)
          L'operazione di MA-001 con RP-MA1-03 punta ad AGG-003

TIPO B (rp_order_constraints) risponde a:
  "In che ordine i RAMI dello stesso livello si costruiscono?"
  Arco RP-M-01 → RP-M-02 nel DAG →
  tutto il sotto-albero MA-003 deve finire prima che il sotto-albero MA-001 inizi

TIPO A (parent_wait_constraints) risponde a:
  "Quando può iniziare l'operazione DEL PADRE che ha un certo RP?"
  op-MA001-3 con RP-MA1-03 che punta ad AGG-003 →
  op-MA001-3 aspetta che AGG-003 (e GRP-008..010) siano finiti

Entrambi sono necessari. Il Tipo B da solo non garantisce che le operazioni
del padre attendano il completamento dei loro figli diretti.
Il Tipo A da solo non garantisce l'ordine tra rami paralleli (es. MA-003 prima di MA-001).
```

---

## 9. PROBLEMI APERTI (ordinati per priorità)

### 1. `parent_wait_constraints` non implementati (ALTA) ← MODIFICHE SOPRA

Il Tipo A è la semantica fondamentale dei Reference Point.
Le modifiche descritte nella sezione 6 risolvono questo problema.

### 2. Vincolo turni v2 (MEDIA)

`_add_shift_nooverlap_constraints()` è rilassato: blocca solo gli operatori
senza nessuno slot disponibile nell'intero horizon. Non impedisce alle operazioni
di cadere in periodi di assenza.

La versione v2 richiede la decomposizione delle operazioni lunghe in task-per-slot
(un'operazione da 480min diventa N task da max 225min ciascuno). Non implementare
prima di risolvere il punto 1.

**NON ripristinare** il vecchio `AddNoOverlap(fixed + optional)` senza la
decomposizione slot-task: causa INFEASIBLE perché operazioni da 480min non
entrano in nessun turno (max ~225min per slot).

### 3. Obiettivo CP-SAT (BASSA)

`_set_objective` è `pass` (solo soddisfacibilità). Riabilitare dopo fix 1 e 2.

---

## 10. DECISIONI CRITICHE — NON modificare senza capire il perché

### CP-SAT: assegnazione 1 operatore, durata fissa

Rimossa logica SIMULTANEOUS (`AddDivisionEquality` è non-lineare). Attualmente:
`sum(assign_vars) == 1`, durata fissa = residual da `max(planned × (1 - progress/100), MIN_OP_DURATION)`.

**NON reintrodurre** `AddDivisionEquality` senza testare su subset di 10 op con `CPSAT_MAX_OPS=10`.

### CP-SAT: obiettivo disabilitato

`_set_objective` è `pass`. Il solver usa `stop_after_first_solution=True`.
Riabilitare solo dopo aver stabilizzato il vincolo turni v2.

### Workcenter ID nelle operazioni

```python
wc_id = op.workcenter_id or po.workcenter_id  # ← CORRETTO
# NON: op.workcenter_id or routing.production_order_id  ← era UUID dell'ordine!
```

### rp_order_constraints — approccio variabili CP-SAT (non blocking_constraints)

**Problema originale (v1):** `blocking_constraints` era un dict `{op_id → min_start_minute}`
calcolato a partire dalle `schedule_entries` esistenti. Al primo run il dict era vuoto
→ nessun vincolo RP veniva applicato → tutte le operazioni venivano schedulate in
parallelo ignorando la gerarchia BOM.

**Soluzione adottata (v2):** `rp_order_constraints` e `parent_wait_constraints`
costruiti in Step 4d del reschedule_engine. Funzionano su variabili CP-SAT →
corretti su ogni run, indipendentemente da schedule_entries preesistenti.

**NON ripristinare** il vecchio `blocking_constraints` per i vincoli RP.
Il dict può rimanere come meccanismo per override manuali per-operazione.

---

## 11. PARAMETRI SOLVER ATTUALI

```python
solver.parameters.max_time_in_seconds = 30
solver.parameters.num_search_workers = min(8, cpu)
solver.parameters.stop_after_first_solution = True
solver.parameters.log_search_progress = True  # solo dev
solver.parameters.linearization_level = 1
solver.parameters.search_branching = 6  # PORTFOLIO_WITH_QUICK_RESTART
```

Hint greedy attivo (`_add_solution_hints`): riduce FEASIBLE da ~30s a 1-5s.

---

## 12. MIGRATIONS ALEMBIC

- `001_initial_schema.py` — tutte le 19 tabelle, enum `targetlevel` con `MACROAGGREGATE, AGGREGATE`
- `002_add_group_to_targetlevel.py` — `ALTER TYPE targetlevel ADD VALUE IF NOT EXISTS 'GROUP'`

Alembic usa psycopg2 sync. `env.py` converte `postgresql+asyncpg://` → `postgresql+psycopg2://`.

**NON eseguire `alembic upgrade head` da fuori il venv con PostgreSQL spento.**
Comando corretto: dalla cartella `backend` con venv attivo e PostgreSQL portable avviato.

---

## 13. WEBSOCKET

Notifiche real-time al frontend:
```json
{"type": "RESCHEDULE_COMPLETE", "scenario_id": "..."}
{"type": "AI_SUGGESTION_NEW", "count": N, "scenario_id": "..."}
{"type": "SCHEDULE_INFEASIBLE", "conflicts": [...]}
```

---

## 14. STRUTTURA FILE SCHEDULER

```
backend/app/core/scheduler/
  cpsat_types.py            SchedulableOperation, QualifiedOperator, CpsatVariables, CpsatSolution
  cpsat_model_builder.py    CpsatModelBuilder con build_and_solve()
                              → include _add_parent_wait_constraints() (NUOVO)
  dag_builder.py            build_precedence_dag(), get_scheduling_order()
  shift_preprocessor.py     build_operator_available_slots(), build_unavailable_intervals()
  reschedule_engine.py      Celery task reschedule_incremental (ENTRY POINT)
                              → Step 4d calcola rp_order_constraints + parent_wait_constraints
  solution_extractor.py     Traduce soluzione CP-SAT in schedule_entries
  infeasibility_analyzer.py Spiega INFEASIBLE in italiano
```

---

## 15. ERRORI NOTI E SOLUZIONI

| Errore | Causa | Soluzione |
|---|---|---|
| `'GROUP' is not among the defined enum values` | Migration 002 non applicata | `python -m alembic upgrade head` |
| `connection refused port 5432` | PostgreSQL portable non avviato | Avviare con `pg_ctl start -D <data_dir>` |
| `alembic upgrade head` fallisce | Eseguito fuori dal venv con PG spento | Attivare venv + avviare PG + rieseguire |
| `AddDivisionEquality` → INFEASIBLE | Durata variabile non lineare | Usare durata fissa + 1 solo operatore |
| `AddNoOverlap(fixed + optional)` → INFEASIBLE | Op 480min non entra nei turni | Vincolo turni rilassato (v1 attuale) |
| `wc_id = routing.production_order_id` | Bug UUID ordine usato come WC | Fix: `wc_id = op.workcenter_id or po.workcenter_id` |
| `ModuleNotFoundError: celery_worker` | Celery lanciato da cartella sbagliata | `cd backend` prima di lanciare Celery |
| Tutte le op schedulabili in parallelo (ignorano BOM) | `parent_wait_constraints` mancanti | Aggiungere modifiche sezione 6 |

---

## 16. SEED DATI MOCK — Struttura RP (v2 corretta)

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

**NON usare** codici nella forma `RP-XXX` (vecchi RP-001..010, eliminati).

---

## 17. LA RISCHEDULAZIONE — Flusso completo

Tre eventi triggherano una rischedulazione automatica:

1. **Delay event creato** (`requires_reschedule=True`) → lo scenario attivo viene rischedulato via Celery
2. **Chiamata manuale** a `POST /api/scenarios/{id}/run`
3. **In futuro**: integrazione con SAP che notifica cambi di stato materiale

Il flusso del Celery task `reschedule_incremental`:

```
1.  Carica scenario e machine_order
2.  Marca schedule_entries esistenti come STALE
3.  Identifica operazioni IN_PROGRESS (fissate come già avviate)
4.  Carica operazioni schedulabili (status != COMPLETED)
4b. Calcola vincoli componenti mancanti
4c. Carica operatori + slot calendario (56 giorni da oggi)
4d. Calcola rp_order_constraints (Tipo B) + parent_wait_constraints (Tipo A) ← CRITICO
5.  Calcola horizon (min tra target_finish_date e fine calendario + 7gg)
6.  Costruisce e risolve modello CP-SAT
7.  Se FEASIBLE: persiste le nuove schedule_entries, cancella le STALE
8.  Notifica frontend via WebSocket RESCHEDULE_COMPLETE
9.  Avvia analisi proattiva AI in background
```

---

## 18. AI LAYER — 7 modalità

| Modalità | Trigger | Endpoint |
|---|---|---|
| 1. Ottimizzazione | Manuale "Ottimizza con AI" | POST /api/ai/optimize-schedule |
| 2. Proattiva | Auto post CP-SAT (Celery) | interno |
| 3. Analisi ritardo | Creazione delay_event | POST /api/ai/analyze-delay |
| 4. Chat libera | Input planner | POST /api/ai/chat |
| 5. What-if | Confronto scenari | POST /api/ai/compare-scenarios |
| 6. Spiega entry | Click "Perché?" su barra Gantt | GET /api/ai/explain-entry/{id} |
| 7. Pattern storici | Analisi tendenze | POST /api/ai/historical-patterns |

Tutte le chiamate AI usano `claude-sonnet-4-6` via Anthropic SDK.