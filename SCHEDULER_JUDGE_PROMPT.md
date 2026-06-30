# Prompt per giudice AI — Valutazione motore di scheduling MES

Sei un esperto di scheduling industriale e ottimizzazione combinatoria (RCPSP, CP-SAT,
list-scheduling). Ti viene chiesto di **valutare la correttezza logica e tecnica**
dell'implementazione del motore di scheduling di un sistema MES (Manufacturing Execution
System) per la produzione di macchinari industriali complessi (esempio: presse idrauliche).

---

## 1. Contesto di business

Un **ordine macchina** (MachineOrder) rappresenta la produzione di una macchina.
Ogni macchina è composta da una gerarchia di **ordini di produzione** (ProductionOrder)
strutturati in livelli BOM:

```
MACHINE (ordine macchina)
  └── MACROAGGREGATE (MA-001, MA-002, MA-003)
        └── AGGREGATE (AGG-001..012)
              └── GROUP (GRP-001..040)
                    └── COMPONENT (componenti acquistati, senza scheduling)
```

Ogni ordine di produzione non-COMPONENT ha un **routing** con N **operazioni**
(Operation). Ogni operazione ha:
- `operation_type`: ELECTRICAL | MECHANICAL | GENERAL
- `workcenter_id`: quale officina la esegue
- `planned_duration_minutes`: durata pianificata
- `progress_pct`: percentuale di avanzamento (0–100)
- `reference_point_id`: (opzionale) collegamento a un Reference Point nel DAG RP
- `status`: PENDING | IN_PROGRESS | COMPLETED | BLOCKED | INTERRUPTED

---

## 2. Modello delle risorse

Le risorse non sono individui nominali ma **ResourceType** (gruppi risorsa):
- Ogni gruppo è identificato da `(workcenter_id, skill)` dove `skill ∈ {ELECTRICAL, MECHANICAL, MULTI}`
- `count × daily_capacity_hours` = capacità giornaliera del gruppo
- `weekday_schedule`: JSON opzionale per sovrascrivere count/ore per giorno della settimana

Compatibilità skill ↔ tipo operazione:
```
ELECTRICAL  → può fare: ELECTRICAL, GENERAL
MECHANICAL  → può fare: MECHANICAL, GENERAL
MULTI       → può fare: ELECTRICAL, MECHANICAL, GENERAL
```

Un'operazione può essere lavorata da UN SOLO operatore/risorsa alla volta, ma può
spalmarsi su più giorni (hand-off) e su più corsie parallele dello stesso gruppo.

---

## 3. Reference Point (RP) e DAG di precedenze

Il **machine model** definisce un insieme di **Reference Point** (RP) e un DAG di
**ReferencePointPrecedence** (archi di precedenza tra RP).

Ogni RP ha un `target_order_material` che lo collega a un ProductionOrder (via
`material_code`). La struttura è multi-livello:
- RP-M-xx → puntano a MACROAGGREGATE (dalla macchina)
- RP-MAx-xx → puntano ad AGGREGATE (dal macroaggregato x)
- RP-Axxx-xx → puntano a GROUP (dall'aggregato xxx)

**Esempio di DAG RP:**
```
Livello MACHINE:
  RP-M-01 (→MA-003) ──→ RP-M-02 (→MA-001)
                    └──→ RP-M-03 (→MA-002)

Livello MA-001:
  RP-MA1-01 (→AGG-001) ──→ RP-MA1-02 (→AGG-002) ──→ RP-MA1-03 (→AGG-003)
                       └──→ RP-MA1-04 (→AGG-004)    └──→ RP-MA1-05 (→AGG-005)

Livello AGG-002:
  RP-A002-01 (→GRP-004) ──→ RP-A002-02 (→GRP-005) ──→ RP-A002-03 (→GRP-006)
                         └──→ RP-A002-04 (→GRP-007)   (parallelo a GRP-005/006)
```

Ogni operazione con `reference_point_id = RP-X` è un'operazione di
"integrazione/assemblaggio" del target di RP-X nella struttura padre.

---

## 4. I due vincoli fondamentali dello scheduler

### VINCOLO 1 — BOM (HARD, obbligatorio)

**Regola:** prima di eseguire QUALSIASI operazione di un ordine padre, TUTTI gli ordini
della sua BOM (figli diretti e ricorsivi) devono essere completati.

Formalmente:
```
∀ ordine P con figli BOM {C₁, C₂, ..., Cₙ} (ricorsivi):
∀ op ∈ ops(P):
  op.start ≥ max(op_child.end  ∀ op_child ∈ ops_ricorsive(C₁ ∪ C₂ ∪ ... ∪ Cₙ))
```

**Esempio:**
- AGG-002 ha figli: GRP-004, GRP-005, GRP-006, GRP-007
- OGNI op di AGG-002 non può iniziare finché tutte le op di GRP-004..007 non sono complete
- OGNI op di MA-001 non può iniziare finché tutte le op di AGG-001..005
  (e i loro GRP figli) non sono complete

**Implicazione importante:**
Il vincolo agisce SOLO tra un ordine padre e i SUOI figli. NON esiste alcun vincolo
hard tra figli di padri diversi o tra figli dello stesso padre.
- GRP-001..003 (figli di AGG-001) possono lavorare in **parallelo** con GRP-004..007
  (figli di AGG-002): nessun vincolo hard tra loro.
- AGG-001 e AGG-002 possono iniziare le loro operazioni di integrazione in **momenti
  diversi** senza ordine imposto tra loro (ognuno inizia quando i SUOI figli sono pronti).

### VINCOLO 2 — RP Ordering (SOFT, solo priorità)

Il DAG RP fornisce la **priorità di dispatch** — quale operazione/sottoalbero lavorare
prima quando le risorse sono limitate. NON è mai un vincolo hard che blocca il parallelismo.

Questo vale sia per il **RP intra-routing** (coppie `rp_direct_pairs`: op con
`reference_point_id = RP_pred` ha priorità sull'op con `reference_point_id = RP_succ`)
sia per il **RP cross-subtree** (`rp_order_constraints`: ordine tra interi sottoalberi BOM).

**Se op RP_pred è bloccata (mancanti, impedenze):** op RP_succ può comunque lavorare.
**Con risorse disponibili:** op di livelli RP diversi partono in **parallelo** senza aspettarsi.

**Meccanismo:**
```
op_priority[op] = rp_level(ordine_di_appartenenza_di_op) × 10000
               + intra_routing_depth[op]
```
- `rp_level`: BFS sul DAG degli ordini derivato da `rp_order_constraints` (0 = nessun predecessore)
- `intra_routing_depth`: profondità topologica dell'op nel DAG RP intra-routing (`rp_direct_pairs`)

**Comportamento con risorse limitate:**
- Il dispatcher preferisce op con `op_priority` più bassa (RP level basso, intra-depth basso)
- es. AGG-op1 (depth=0) viene schedulata prima di AGG-op2 (depth=1) se le risorse non bastano per entrambe
- es. sottoalbero AGG-001 (rp_level=0) prima di AGG-002 (rp_level=1) se risorse limitate

**Comportamento con risorse disponibili (corsie libere):**
- Tutte le op pronte partono subito in parallelo, indipendentemente da `op_priority`

### VINCOLO 4 — Componenti mancanti (HARD, per operazione)

```
op.start ≥ max(op.earliest_start_minutes, missing_arrival_minutes)
```

Se un componente per un ordine GRP non è ancora arrivato, tutte le op di quell'ordine
non possono iniziare prima della data di arrivo prevista. Il vincolo BOM (1) si propaga:
un GRP bloccato ritarda AGG padre → ritarda MA padre → ritarda MACHINE padre.

### VINCOLO 5 — IN_PROGRESS anchor (HARD)

Le operazioni già iniziate (`actual_start` set, `actual_end` null) vengono ancorate
al momento corrente: `earliest_start_minutes = max(now_minutes, 0)`. Il solver non
può riposizionarle nel passato.

---

## 5. Architettura del motore di scheduling

```
reschedule_engine.py (Celery task)
  │
  ├── Step 4d: Costruisce 3 strutture vincoli:
  │     ├── precedence_pairs    = rp_direct_pairs   [SOFT, solo per op_priority intra-depth]
  │     ├── rp_order_constraints = subtree ordering  [SOFT, solo per op_priority rp_level]
  │     └── parent_wait_constraints = BOM wait       [HARD, unico vincolo bloccante]
  │
  ├── Stadio 1: CapacityScheduler (greedy list-scheduling)
  │     • Ordine topologico basato SOLO su parent_wait_constraints (HARD)
  │     • Dispatch priority: (earliest_start, op_priority)
  │         op_priority = rp_level×10000 + intra_routing_depth
  │     • Se op bloccata: il dispatcher passa avanti, non aspetta
  │     • Earliest-fit su corsie: per ogni op, trova la prima capacità disponibile
  │     • Always feasible in ms
  │
  └── Stadio 2: CapacityCpsatScheduler (OR-Tools CP-SAT)
        • Modello cumulativo a capacità di gruppo (AddCumulative)
        • Vincoli hard: SOLO parent_wait_constraints in _preds()
        • Warm-start dalla soluzione greedy (che già rispetta la priorità RP)
        • Obiettivo: minimizza makespan (± distribuzione risorse)
```

### 5.1 Greedy — costruzione precedenze

`_build_preds()` nel greedy costruisce il grafo di precedenza per il topological sort:
```python
# UNICO vincolo HARD: BOM parent-wait
for ops_target, parent_op_id in parent_wait_constraints:
    for target_id in ops_target:
        preds[parent_op_id].add(target_id)
```

`precedence_pairs` (rp_direct_pairs) NON entra in `_build_preds()`: l'RP intra-routing
è SOFT e influenza solo `op_priority` (intra_routing_depth).
`rp_order_constraints` NON entra in `_build_preds()` — influenza solo `op_priority` (rp_level).

### 5.2 CP-SAT — costruzione vincoli

`_preds()` nel CP-SAT usa solo `parent_wait_constraints`:
```python
# op_start[parent] >= op_end[child]  per ogni (child_ops, parent) in parent_wait_constraints
```

`precedence_pairs` e `rp_order_constraints` NON entrano in `_preds()` CP-SAT.
La priorità RP è trasmessa indirettamente tramite il warm-start greedy.

### 5.3 Modello CP-SAT (capacità di gruppo)

Per ogni operazione:
- `k` segmenti opzionali per ogni gruppo risorsa compatibile
- `Σ size[seg] == D` (tutto il lavoro allocato)
- `NoOverlap([seg])` (una risorsa alla volta per operazione)
- `op_start = min(start[seg_presente])`, `op_end = max(end[seg_presente])`

Per ogni gruppo risorsa:
- `AddCumulative([tutti i segmenti del gruppo + blockers calendario], demands, capacity)`

Blockers calendario: intervalli fissi che modellano fuori-orario e giorni non lavorati.

---

## 6. Costruzione di `rp_direct_pairs` e `rp_order_constraints`

### rp_direct_pairs (SOFT — solo per op_priority)
```python
# rp_to_op_id: mappa RP_id → op_id (solo op schedulabili con reference_point_id)
rp_to_op_id = {op.reference_point_id: op.id
               for op, routing, po in ops_rows
               if op.id in schedulable_op_ids and op.reference_point_id}

# Per ogni arco (pred_rp → succ_rp) nel DAG:
rp_direct_pairs = [(rp_to_op_id[prec.pred_rp_id], rp_to_op_id[prec.succ_rp_id])
                   for prec in prec_rows
                   if prec.pred_rp_id in rp_to_op_id and prec.succ_rp_id in rp_to_op_id]
```

Solo le coppie dove ENTRAMBI i RP sono referenziati da op schedulabili vengono catturate.
`rp_direct_pairs` NON entra in `_build_preds()` o `_preds()`. Viene usato per calcolare
`intra_routing_depth` (topological depth nel DAG), poi sommato in `op_priority`.

### rp_order_constraints (SOFT, solo priorità)
```python
# Per ogni arco pred_rp → succ_rp nel DAG RP:
ops_pred = _collect_ops_recursive(rp_id_to_po_id[pred_rp_id], children_map, ...)
ops_succ = _collect_ops_recursive(rp_id_to_po_id[succ_rp_id], children_map, ...)
rp_order_constraints.append((ops_pred, ops_succ))
# → op_priority[op] = rp_level(ordine) × 10000  (via BFS su questi vincoli)
```

### parent_wait_constraints (HARD, BOM)
```python
# Per ogni ordine padre con figli BOM:
for order_id, child_ids in children_map.items():
    all_child_ops = [tutte le op schedulabili di tutti i figli ricorsivi]
    for parent_op_id in ops_by_order[order_id]:
        parent_wait_constraints.append((all_child_ops, parent_op_id))
```

---

## 7. Modalità obiettivo (ObjectiveMode)

| Modalità | Comportamento CP-SAT |
|----------|---------------------|
| `FINISH_BY_DATE` | makespan ≤ target (hard) + minimizza makespan |
| `MAXIMIZE_RESOURCE_UTILIZATION` | minimizza `makespan × (n_g+1) − Σ used_g` |
| `MINIMIZE_OPERATORS` | minimizza `Σ used_g × (H+1) + makespan` |
| `CUSTOM` | minimizza makespan puro |

---

## 8. Esempio completo — Trace di scheduling

**Setup:** 3 risorse disponibili (3 corsie parallele), 1 AGG con 4 GRP figli.

```
AGG-002: ops [agg2-op1(RP-A002-01→GRP-004), agg2-op2(RP-A002-02→GRP-005),
              agg2-op3(RP-A002-03→GRP-006), agg2-op4(RP-A002-04→GRP-007)]
         RP archi: 01→02, 01→04, 02→03

GRP-004: ops [g4-op1, g4-op2]   (200 min totali)
GRP-005: ops [g5-op1, g5-op2]   (200 min totali)
GRP-006: ops [g6-op1, g6-op2]   (200 min totali)
GRP-007: ops [g7-op1, g7-op2]   (150 min totali)
```

**Vincoli attivi:**
- BOM: agg2-op1..4 aspettano TUTTI: g4-op1, g4-op2, g5-op1, g5-op2, g6-op1, g6-op2, g7-op1, g7-op2
- RP-direct: agg2-op1 → agg2-op2 (hard), agg2-op1 → agg2-op4 (hard), agg2-op2 → agg2-op3 (hard)
- RP-priority: GRP-004 ha priorità su GRP-005 (RP-A002-01 → RP-A002-02)

**Piano atteso (3 corsie parallele):**
```
GIORNO   │ CORSIA 1     CORSIA 2     CORSIA 3     │ NOTE
─────────┼─────────────────────────────────────────────────────────
D am     │ GRP-004 op1  GRP-005 op1  GRP-007 op1  │ tutti partono in parallelo
D pm     │ GRP-004 op2  GRP-005 op2  GRP-007 op2  │ (RP soft: 004 in prio ma risorse ci sono)
─────────┼─────────────────────────────────────────────────────────
D+1 am   │ GRP-006 op1  [GRP-007 ok] [GRP-005 ok] │ 006 aspettava risorse
D+1 pm   │ GRP-006 op2  —            —             │
─────────┼─────────────────────────────────────────────────────────
D+2 am   │ agg2-op1     —            —             │ BOM soddisfatto (tutti GRP done)
D+2 pm   │ agg2-op2     agg2-op4     —             │ RP: op1→op2 (hard), op1→op4 (hard)
                                                   │ op2 e op4 PARALLELO (nessun arco tra loro)
D+3 am   │ agg2-op3     [op4 done]   —             │ RP: op2→op3 (hard)
D+3 pm   │ [op3 done]   —            —             │
```

**Con componente mancante su GRP-004 (arrivo giorno D+3):**
```
D am     │ GRP-005 op1  GRP-006 op1  GRP-007 op1  │ GRP-004 BLOCCATA
D pm     │ GRP-005 op2  GRP-006 op2  GRP-007 op2  │ altri GRP procedono (no vincolo tra loro)
D+1      │ [tutti GRP done tranne GRP-004]          │
D+3 am   │ GRP-004 op1  —            —             │ componente arrivato
D+3 pm   │ GRP-004 op2  —            —             │
D+4 am   │ agg2-op1..4 sequenza come sopra          │ BOM soddisfatto (GRP-004 ora done)
```

---

## 9. Invarianti da verificare nella soluzione

Una soluzione corretta DEVE rispettare:

1. **BOM HARD (obbligatorio):** per ogni ordine P con figli BOM, ogni op di P inizia
   DOPO la fine dell'ultima op di tutti i figli ricorsivi.
   ```
   ∀ op_parent ∈ ops(P): op_parent.start ≥ max(op_child.end ∀ op_child ∈ ops_bom_ricorsive(P))
   ```

2. **Missing component HARD:** `op.start ≥ op.earliest_start_minutes`.

3. **Capacità risorse:** in ogni momento, il numero di operazioni attive su un gruppo
   risorsa (workcenter, skill) non supera `count` del gruppo per quel giorno.

4. **No overlap per operazione:** i segmenti della STESSA operazione non si sovrappongono
   (una risorsa alla volta per operazione).

5. **RP ordering SOFT (preferenza):** quando le risorse sono limitate, le op con
   `op_priority` più bassa (rp_level basso + intra_depth basso) devono essere schedulate
   prima. Con risorse spare, è accettabile che op di rp_level/intra_depth diverso lavorino
   in parallelo. Se un'op è bloccata (mancanti), le altre devono poter procedere.

6. **Correttezza temporale:** nessuna operazione inizia prima di `epoch` (start_date=00:00).

---

## 10. Cosa valutare

Analizza il codice Python fornito e verifica:

### A. Correttezza logica dei vincoli
- [ ] `parent_wait_constraints`: ogni op di un ordine padre aspetta TUTTE le op di
  TUTTI i figli BOM ricorsivi? (HARD — unico vincolo bloccante)
- [ ] `rp_direct_pairs` e `rp_order_constraints` sono usati SOLO per `op_priority`
  (SOFT), NON in `_preds()` di greedy o CP-SAT?
- [ ] `op_priority` incorpora sia `rp_level × 10000` sia `intra_routing_depth`?
- [ ] `missing_constraints`: `earliest_start_minutes` viene rispettato da entrambi gli
  scheduler? (HARD)

### B. Correttezza dell'implementazione CP-SAT
- [ ] `_preds()` include SOLO `parent_wait_constraints`, senza `precedence_pairs` né
  `rp_order_constraints`?
- [ ] `AddCumulative` modella correttamente la capacità variabile per giorno della
  settimana tramite i capacity blockers?
- [ ] I segmenti `NoOverlap` garantiscono che una stessa operazione usi una risorsa
  alla volta?
- [ ] `Σ size[seg] == D` garantisce che tutta la durata residua sia allocata?

### C. Correttezza del greedy
- [ ] `_build_preds()` include SOLO `parent_wait_constraints`, senza `precedence_pairs`?
- [ ] Il topological sort gestisce i cicli (eventualmente presenti per bug nei dati)?
- [ ] `_earliest_slot()` rispetta il calendario settimanale variabile (weekday_count/minutes)?
- [ ] L'hand-off multi-giorno (op che si spalma su più giorni) è gestito correttamente?

### D. Flessibilità e parallelismo
- [ ] Con risorse multiple: le op di sottoalberi BOM fratelli (es. GRP-001..003 e
  GRP-004..007) possono partire in parallelo?
- [ ] Con componenti mancanti: un GRP bloccato NON blocca i GRP fratelli (che hanno
  padri diversi)?
- [ ] Il parallelismo all'interno dello stesso sottoalbero (es. op2 e op4 di AGG-002
  in parallelo) è possibile quando non c'è arco RP tra loro?

### E. Robustezza
- [ ] Se `machine_model_id` è None, `parent_wait_constraints` e `rp_order_constraints`
  sono inizializzati come liste vuote (no NameError)?
- [ ] Se un RP non risolve a nessun ordine, viene gestito con `continue` (no KeyError)?
- [ ] Il fallback greedy se CP-SAT va in timeout/INFEASIBLE è attivo?

---

## 11. File di codice rilevanti

```
backend/app/core/scheduler/
├── reschedule_engine.py       ← costruzione vincoli + orchestrazione
├── capacity_scheduler.py      ← greedy list-scheduling
├── capacity_cpsat.py          ← CP-SAT cumulativo
├── cpsat_types.py             ← dataclass SchedulableOperation, _SKILL_CAN_DO
└── shift_preprocessor.py     ← epoch, horizon, datetime↔minutes
```

La logica centrale dei 2 vincoli fondamentali è in `reschedule_engine.py` nello
Step 4d (righe ~450–600 circa), dove vengono costruiti:
- `rp_direct_pairs` → passato come `precedence_pairs`
- `rp_order_constraints`
- `parent_wait_constraints`

e successivamente usati da entrambi gli scheduler.

---

## 12. Domanda di valutazione

**"L'implementazione rispetta correttamente i 2 vincoli fondamentali?**
**Il BOM è HARD (tutti i figli BOM devono essere completati prima di qualsiasi op**
**del padre — unico vincolo bloccante)? Entrambi i vincoli RP (intra-routing e**
**cross-subtree) sono SOFT (forniscono solo priorità di dispatch tramite op_priority,**
**senza bloccare il parallelismo — se un'op è bloccata per mancanti si lavora la**
**successiva)? Ci sono bug logici o casi limite non gestiti?"**

Fornisci:
1. Verdetto generale (CORRETTO / PARZIALMENTE CORRETTO / ERRATO) con giustificazione
2. Elenco di eventuali bug o violazioni trovate, con riga di codice e spiegazione
3. Esempi concreti di scenari in cui la soluzione fallisce (se ce ne sono)
4. Suggerimenti di miglioramento (se applicabili)
