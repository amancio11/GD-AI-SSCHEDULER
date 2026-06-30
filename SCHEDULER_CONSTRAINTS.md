# Scheduler Tecnico — Vincoli, Algoritmi e Architettura

Documento tecnico di riferimento per il motore di scheduling del sistema MES.

> **v3 — 2026-06-26 — Motore a due stadi: greedy list-scheduling + CP-SAT cumulativo a capacità di gruppo.**
>
> Il modello CP-SAT v2 (segmenti per-operatore + `NoOverlap` a coppie) era UNKNOWN su 240+ op: simmetria degli operatori "con nome" + coupling esplosivo dei NoOverlap a dimensione variabile. Il modello a **capacità di gruppo** elimina entrambi i killer e porta OPTIMAL in pochi secondi.
>
> Le sezioni v1/v2 (CP-SAT a segmenti per-operatore e per-turno) sono **rimosse**: il codice di riferimento è `capacity_scheduler.py` e `capacity_cpsat.py`.

---

## 1. Architettura generale

```
reschedule_engine.py (Celery task)
    │
    ├── Step 5: ResourceType (DB) → ResourceGroup
    │
    ├── Step 6: DAG Reference Point → rp_order_constraints + parent_wait_constraints
    │
    ├── Stadio 1 ─ capacity_scheduler.py (CapacityScheduler)
    │       greedy list-scheduling, sempre fattibile in ms
    │       → CapacityResult (entries, makespan_minutes, completion)
    │
    └── Stadio 2 ─ capacity_cpsat.py (CapacityCpsatScheduler)   [se CPSAT_CAPACITY_ENABLED=1]
            CP-SAT cumulativo a minuti
            warm-start dal greedy
            → CapacityResult ottimizzato (fallback greedy se timeout/infeasible)
```

**Unità di tempo:** tutti i valori interni sono **minuti da `epoch`**.  
`epoch = start_date a 00:00 UTC` — calcolato da `compute_epoch()` in `shift_preprocessor.py`.  
`start_date = scenario.start_date` se impostato, altrimenti `date.today()`.

---

## 2. Modello delle risorse

Le risorse **non sono individui con nome** ma *tipi configurabili*, memorizzati nella tabella `resource_types`:

| Campo | Tipo | Significato |
|-------|------|-------------|
| `workcenter_id` | UUID FK | Workcenter di appartenenza |
| `skill` | SkillType | `ELECTRICAL` \| `MECHANICAL` \| `MULTI` |
| `daily_capacity_hours` | float | Ore/giorno di UNA singola risorsa (default lun–ven) |
| `count` | int | Numero risorse di questo tipo (default lun–ven) |
| `weekday_schedule` | JSON (nullable) | Override per-giorno: `{"0": {"count": 2, "hours": 8}, …}` (0=lun…6=dom) |
| `is_active` | bool | Solo i tipi attivi entrano nel piano |

**Capacità di gruppo:** `count × daily_capacity_hours` per giorno standard.  
Se `weekday_schedule` è valorizzato, sovrascrive count/ore per quel giorno; sab/dom default a 0.

**In memoria scheduler** (`ResourceGroup` in `capacity_scheduler.py`):

```python
ResourceGroup(
    workcenter_id: UUID,
    skill: SkillType,
    resource_type_id: UUID | None,
    weekday_count: dict[int, int],    # wd → numero risorse quel giorno
    weekday_minutes: dict[int, int],  # wd → minuti/giorno di UNA risorsa
)
```

**Compatibilità operazione ↔ skill** (`_SKILL_CAN_DO` in `cpsat_types.py`, invariato):

| Skill \ OperationType | ELECTRICAL | MECHANICAL | GENERAL |
|----------------------|:---------:|:---------:|:-------:|
| ELECTRICAL           | ✓ | ✗ | ✓ |
| MECHANICAL           | ✗ | ✓ | ✓ |
| MULTI                | ✓ | ✓ | ✓ |

---

## 3. Stadio 1 — Greedy list-scheduling (`capacity_scheduler.py`)

Scheduler costruttivo a regole di dispatching (RCPSP-style).

### 3.1 Ordinamento topologico e priorità dispatch

**Vincolo HARD (solo BOM):**
Il grafo di precedenza `_build_preds()` usa **solo** `parent_wait_constraints`:
- Ogni op di un ordine padre non può iniziare finché tutte le op di tutti i figli BOM non sono finite.
- Il topological sort garantisce che le foglie (GROUP) vengano processate prima degli AGGREGATE, che prima dei MACROAGGREGATE, ecc.

**Priorità di dispatch (SOFT — non predecessori hard):**
Con più risorse disponibili, operazioni a diversi livelli RP e/o con sequenza RP diversa possono lavorare in parallelo. La priorità di dispatch le mette solo "in coda" davanti alle altre quando le risorse sono limitate. Se un'op è bloccata (mancanti o altro), il dispatcher passa alla prossima senza attendere:

```python
# op_priority[op_id] = rp_level(ordine) × 10000 + intra_routing_depth
# rp_level: BFS sul DAG degli ordini RP (0 = nessun predecessore RP)
# intra_routing_depth: DAG RP intra-routing (rp_direct_pairs) — profondità dell'op nel routing
op_priority = rp_level.get(op.production_order_id, 0) * 10000 + intra_routing_depth.get(op.id, 0)
```

La ready queue viene ordinata per `(earliest_start, op_priority)`:
- Op con RP level basso → dispatchate per prime (GRP-032 prima di GRP-033 se limitate le risorse)
- Op con seq basso → dispatchate prima di quelle con seq alto nello stesso ordine
- Con risorse libere: **entrambe partono immediatamente in parallelo**, la priorità non le blocca.

### 3.2 Earliest-fit su corsie

Per ogni operazione (durata residua `D` minuti):

1. Calcola `est = max(op.earliest_start_minutes, missing_constraint, completion[pred])`
2. Per ogni `ResourceGroup` candidato (workcenter + skill compatibili): trova la corsia (`lane`) e il giorno con lo start più precoce ≥ `est`
3. Sceglie il best (start più basso tra tutti i gruppi)
4. Alloca `min(D, finestra_residua_nel_giorno)` minuti in quella corsia; `cursor = end`; ripete se `D > 0`

**Una risorsa alla volta per operazione:** il `cursor` avanza dopo ogni blocco, garantendo sequenzialità.  
**Split multi-giorno:** se `D` non entra nella finestra giornaliera corrente, l'operazione si spalma automaticamente su giorni/corsie successive (hand-off).

### 3.3 Output

```python
CapacityResult(
    status: str,               # "OPTIMAL" | "INFEASIBLE"
    entries: list[CapacityEntry],
    makespan_minutes: int | None,
    conflicts: list[str],      # ops senza gruppo risorse o senza capacità
    completion: dict[UUID, int],  # op_id → minuto di fine
)

CapacityEntry(
    operation_id, workcenter_id, resource_type_id, skill,
    lane_index, start_minutes, end_minutes,
)
```

---

## 4. Stadio 2 — CP-SAT cumulativo (`capacity_cpsat.py`)

Ottimizzatore CP-SAT (OR-Tools) a minuti, attivabile con `CPSAT_CAPACITY_ENABLED=1` (default).

### 4.1 Perché è trattabile (vs. v2)

| Killer v2 | Soluzione v3 |
|-----------|-------------|
| Simmetria N operatori identici → N! permutazioni | Nessun operatore nominale → zero simmetria |
| N² `NoOverlap` a coppie tra segmenti di operatori diversi | Un `AddCumulative` per gruppo (capacità = `count`) |

### 4.2 Variabili — per ogni operazione

Per ogni `ResourceGroup` compatibile con l'op, si creano `k = ceil(D / day_len) + 1` segmenti opzionali:

```
pres[i]   BoolVar       — 1 ⟺ il segmento porta lavoro
size[i]   IntVar[0, min(day_len, D)]  — minuti di lavoro nel segmento
start[i]  IntVar[est, H]
end[i]    IntVar[est, H]
iv[i]     OptionalIntervalVar(start, size, end, pres)
```

**Vincoli per operazione:**
```
Σ size[seg] == D                  (tutto il lavoro allocato)
NoOverlap([iv per tutti i segs])  (una risorsa alla volta — hand-off sequenziale)
op_start = min(start[pres])       (AddMinEquality su effective_starts)
op_end   = max(end[pres])         (AddMaxEquality su effective_ends)
```

### 4.3 Vincolo di gruppo — `AddCumulative`

Per ogni gruppo `g` (workcenter + skill):

```
AddCumulative(
    [iv di ogni segmento in g] + [blockers calendario],
    demands = [1, …, 1, dem_blocker, …],
    capacity = g.max_count,
)
```

**Blockers calendario:** intervalli fissi a `demand = cap` fuori orario o su giorni non lavorati.  
Per giorni con `count < max_count`: intervallo riduttore `demand = max_count - count` nella finestra lavorativa.

### 4.4 Precedenze hard nel CP-SAT

```
op_start[parent] >= op_end[child]    ∀ (child, parent) in parent_wait_constraints
```

**Solo `parent_wait_constraints` è in `_preds()`** — il CP-SAT vede esclusivamente il vincolo BOM ordine-livello.

`precedence_pairs` (intra-routing RP) e `rp_order_constraints` (RP cross-subtree) **non sono in `_preds()`**: entrambi sono SOFT priority. Il CP-SAT è libero di schedulare le op di diversi ordini in qualsiasi ordine purché rispetti la BOM. Se un'op RP-pred è bloccata (mancanti), la CP-SAT può comunque schedulare l'op RP-succ.

La priorità RP è trasmessa al CP-SAT **indirettamente tramite il warm-start greedy**: il CP-SAT parte da una soluzione che rispetta le priorità e tende a mantenerla se non porta vantaggio al makespan cambiarla.

### 4.5 Obiettivi di scheduling

Il CP-SAT supporta quattro `ObjectiveMode` con comportamenti distinti:

#### FINISH_BY_DATE
```
makespan <= target_finish_minutes   (vincolo HARD: infeasible se impossibile)
Minimize(makespan)
```
Garantisce che il piano chiuda entro la data target. Se non è raggiungibile, il solver
restituisce INFEASIBLE (il greedy è comunque usato come fallback senza vincolo hard).

#### MAXIMIZE_RESOURCE_UTILIZATION
```
used_g[i]  BoolVar = 1 ↔ almeno un segmento del gruppo i è attivo
Minimize(makespan × (n_groups + 1) − Σ used_g[i])
```
**Primario:** minimizza makespan (ogni minuto vale `n_groups + 1` unità).  
**Secondario:** massimizza il numero di gruppi-risorsa distinti usati (ogni gruppo
aggiuntivo vale 1 unità di risparmio).  
Effetto: tra piani con stessa durata, sceglie quello che distribuisce il lavoro
su più risorse — nessuna rimane idle mentre un'altra è satura.

#### MINIMIZE_OPERATORS
```
used_g[i]  BoolVar = 1 ↔ almeno un segmento del gruppo i è attivo
Minimize(Σ used_g[i] × (H + 1) + makespan)
```
**Primario:** minimizza il numero di gruppi-risorsa usati (ogni gruppo costa `H + 1`
unità, ovvero più di qualsiasi possibile makespan).  
**Secondario:** minimizza makespan a parità di gruppi.  
Effetto: concentra tutto il lavoro sul minor numero possibile di risorse.
Utile con crew ridotta o impianto parzialmente attivo.

#### CUSTOM
```
Minimize(makespan)
```
Minimizza makespan puro, senza peso secondario sulla distribuzione delle risorse.
Baseline di confronto rispetto agli altri modi.

---

**Nota implementativa `used_g`:**
```python
# used_g = 1 ↔ OR(pres[seg] for seg in group g)
model.AddBoolOr(pres_list).OnlyEnforceIf(used_g)      # se usato → almeno 1 segmento
model.AddBoolAnd([p.Not() for p in pres_list]).OnlyEnforceIf(used_g.Not())  # se non usato → nessuno
```

---

**Warm-start e obiettivi:** il warm-start dal greedy vale per tutti i modi. Il greedy
stesso non conosce l'objective mode — produce sempre la soluzione earliest-fit, che
CP-SAT poi ottimizza secondo il modo scelto.

**Engine usato:** salvato in `last_run_summary["engine_used"]` (`"greedy"` | `"cpsat"`).
Visibile nel report dello scenario in ScenarioManager.

### 4.6 Warm-start e fallback

- Il greedy fornisce hint (present, start, size) per il segmento più vicino di ogni op
- Solver timeout: `CPSAT_CAPACITY_TIMEOUT` (default 30s), `num_search_workers=8`
- Se CP-SAT ritorna INFEASIBLE/UNKNOWN o nessuna entry → **fallback al greedy**

---

## 5. Pipeline `reschedule_incremental`

**File:** `backend/app/core/scheduler/reschedule_engine.py`

| Step | Cosa succede |
|------|-------------|
| 1 | Carica `ScheduleScenario` (machine_order_id, start_date, target_finish_date, objective_mode) |
| 2 | Marca tutte le entries esistenti `STALE` |
| 3 | Identifica ops IN_PROGRESS (`actual_start` set, `actual_end` null) → `earliest_start = now_minutes` |
| 4 | Carica ops schedulabili (non-COMPLETED); workcenter_id = op.workcenter_id OR po.workcenter_id |
| 4b | **NO intra-routing**: `precedence_pairs = []` (valorizzata in Step 4d dai rp_direct_pairs) |
| 5 | Carica `ResourceType.is_active=True` → `ResourceGroup` con weekday_count/weekday_minutes |
| 6 | Costruisce `rp_direct_pairs` (HARD), `rp_order_constraints` (per priority), `parent_wait_constraints` (HARD) |
| 7 | Calcola epoch + horizon (target_finish_date o start_date+365g) |
| 8 | **Stadio 1**: greedy → `CapacityResult` |
| 9 | **Stadio 2**: CP-SAT (se abilitato e greedy OPTIMAL) → fallback greedy se necessario |
| 10 | Persiste `ScheduleEntry` (operator_id=null, resource_type_id, workcenter_id, scheduled_start/end) |
| 11 | Elimina entries STALE |
| 12 | Broadcast WebSocket (`RESCHEDULE_COMPLETE` / `SCHEDULE_INFEASIBLE`) |
| 13 | Avvia analisi AI proattiva (`analyze_proactive.delay`) |

---

## 6. Vincoli di precedenza — costruzione nel reschedule engine

### 6.0 Riepilogo vincoli — modello finale

| Tipo | Hard/Soft | Implementazione | Effetto |
|------|-----------|----------------|---------|
| **BOM ordine-livello** | **HARD** | `parent_wait_constraints` in `_build_preds`/`_preds` | Il padre non inizia prima che TUTTI i figli BOM (ricorsivi) siano finiti; parallelismo libero tra sottoalberi fratelli |
| Componenti mancanti | **HARD** | `earliest_start_minutes` | L'op non parte prima dell'arrivo del componente |
| IN_PROGRESS anchor | **HARD** | `earliest_start_minutes = now` | L'op già iniziata non viene riposizionata nel passato |
| FINISH_BY_DATE | **HARD** (CP-SAT) | `makespan <= target` | INFEASIBLE se impossibile |
| **RP-direct (intra-routing)** | **SOFT** (priorità) | `rp_direct_pairs` → `op_priority` intra-depth | Dispatch priority intra-routing; se op bloccata si lavora la successiva |
| **RP ordering (cross-subtree)** | **SOFT** (priorità) | `op_priority = rp_level × 10000` | Dispatch priority; con risorse libere → parallelo |
| ~~sequence_number~~ | ~~rimosso~~ | — | Non esiste: l'ordinamento viene SOLO dai RP |

**Come funziona il bypass dei componenti mancanti (v5 RP-aware):**
```
Scenario: GRP-032 (in AGG-010, sotto MA-003) ha componente mancante fino al giorno 10.
           GRP-035 (in AGG-011, sotto MA-003) non ha vincoli.
           MA-003-op1 (RP-MA3-01 → AGG-010), MA-003-op2 (RP-MA3-02 → AGG-011)

  Con parent_wait RP-aware:
  - MA-003-op1 aspetta SOLO AGG-010 subtree (GRP-032, 033, 034)
  - MA-003-op2 aspetta SOLO AGG-011 subtree (GRP-035, 036, 037, 038)
    + MA-003-op1 deve essere finito (rp_direct: RP-MA3-01 → RP-MA3-02)

  t=0:     GRP-035..038 e GRP-033,034 lavorano in parallelo (no vincoli tra loro)
  t=0:     GRP-032 BLOCCATA (earliest_start = giorno 10, componente mancante)
  t=0..4:  AGG-011 subtree progredisce (GRP-035..038 → agg11-op1..4)
  t=6:     AGG-011 subtree DONE (integration complete)
  t=10:    GRP-032 inizia (componente arrivato)
  t=12:    GRP-032 e resto AGG-010 subtree DONE
  t=12:    MA-003-op1 inizia (AGG-010 done)
  t=13:    MA-003-op1 finisce → MA-003-op2 parte subito (AGG-011 era done dal t=6)
```

---

### 6.1 RP-direct — `rp_direct_pairs` (Step 4d) — SOFT (solo priorità)

**Cosa sono:** Le operazioni con `reference_point_id` impostato sono le operazioni di "integrazione/assemblaggio" che in un routing referenziano direttamente un nodo del DAG RP. Per ogni arco `RP_pred → RP_succ`, l'op con `reference_point_id = RP_pred` ha PRIORITÀ più alta (intra-depth minore) rispetto all'op con `reference_point_id = RP_succ`.

**NON è un vincolo hard:** se l'op RP_pred è bloccata (mancanti o altre impedenze), l'op RP_succ può comunque lavorare. Le due op possono anche lavorare in parallelo se ci sono risorse disponibili.

**Come viene calcolata:**
```python
# Step 4d di reschedule_engine.py
rp_to_op_id: dict[rp_id, op_id] = {
    op.reference_point_id: op.id
    for op, routing, po in ops_rows
    if op.id in schedulable_op_ids and op.reference_point_id
}

rp_direct_pairs = []
for prec in prec_rows:   # ReferencePointPrecedence: pred_rp → succ_rp
    pred_op = rp_to_op_id.get(prec.predecessor_reference_point_id)
    succ_op = rp_to_op_id.get(prec.reference_point_id)
    if pred_op and succ_op:
        rp_direct_pairs.append((pred_op, succ_op))

precedence_pairs = rp_direct_pairs   # passato agli scheduler solo per calcolare op_priority
```

**Uso:** `rp_direct_pairs` NON è in `_build_preds()` o `_preds()`. Viene usato solo per calcolare `intra_routing_depth` (profondità topologica nel DAG RP intra-routing), che viene sommato a `op_priority`:
```python
op_priority[op_id] = rp_level(ordine) × 10000 + intra_routing_depth[op_id]
```

**Perché NON è sequence_number:** il sequence_number è un dettaglio implementativo del routing, non un vincolo di assemblaggio. L'ordinamento di priorità viene dai RP definiti nel machine model.

### 6.2 RP ordering (subtree) — `rp_order_constraints` (Step 4d) — solo per priority

**Cosa sono:** per ogni arco DAG RP, vengono raccolte ricorsivamente TUTTE le op dei sottoalberi BOM (non solo le op di integrazione). Usate **solo** per calcolare `op_priority`, NON come hard predecessori.

**Come viene calcolata:**
```python
# DFS ricorsiva per raccogliere tutte le op schedulabili di un ordine e dei suoi figli BOM
ops_pred = _collect_ops_recursive(RP_pred.target_order, children_map, ops_by_order, ...)
ops_succ = _collect_ops_recursive(RP_succ.target_order, children_map, ops_by_order, ...)
rp_order_constraints.append((ops_pred, ops_succ))
```

**Come viene usata — solo per `op_priority`:**
```python
# BFS sul DAG degli ordini (derivato da rp_order_constraints) → rp_level per ordine
# op_priority[op_id] = rp_level(production_order) × 10000
# (nessun sequence_number: l'ordinamento viene SOLO dai RP)
```

Op dello stesso sottoalbero RP ottengono la stessa priorità di base → il dispatcher le lancia in parallelo se ci sono risorse. Op di sottoalberi con RP level diverso: il dispatcher preferisce il level più basso, ma se ci sono risorse spare il level più alto parte subito.

### 6.3 BOM ordine-livello — `parent_wait_constraints` (HARD)

**Semantica:** ogni op di un ordine padre deve aspettare che TUTTE le op di TUTTI i figli BOM (diretti e ricorsivi) siano completate prima di poter iniziare.

Questo è un vincolo **HARD**: prima di lavorare qualsiasi operazione di un ordine, tutti gli ordini nella sua BOM devono essere completati.

**Esempio MA-001 con 5 AGG figli:**
- OGNI op di MA-001 aspetta: tutti i ops di AGG-001..005 + tutti i GRP-001..017

**Parallelismo garantito dalla struttura:**
Il vincolo BOM agisce SOLO tra padre e i SUOI figli. Non esiste nessun vincolo hard tra figli di padri diversi, né tra figli dello stesso padre. Quindi:
- GRP-001..003 (figli di AGG-001) lavorano in **parallelo** con GRP-004..007 (figli di AGG-002) — nessun vincolo tra loro
- AGG-001 ops partono quando GRP-001..003 sono tutti done; AGG-002 ops quando GRP-004..007 sono tutti done — i due eventi possono accadere in momenti diversi, senza ordine imposto tra di loro

**Priorità tramite RP (SOFT):**
L'RP DAG (`rp_order_constraints`) fornisce la **priorità** di quale sottoalbero lavorare prima. Con risorse limitate, il greedy preferisce i sottoalberi con RP level più basso (AGG-001 prima di AGG-002). Con risorse spare, tutti i sottoalberi partono in parallelo.

```python
parent_wait_constraints: list[tuple[list[op_id], op_id]]
# [(tutti_ops_figli_ricorsivi_dell_ordine, op_id_padre), ...]
```

Implementazione:
```
completion_target = max(op_end[t] for t in ops_target)
op_start[parent_op] >= completion_target
```

### 6.3 Componenti mancanti

```
op.earliest_start_minutes = max(…, arrival_minutes)
```

`arrival_minutes = datetime_to_minutes(MissingComponent.expected_arrival_date, epoch)`

### 6.4 IN_PROGRESS anchor

Ops con `actual_start` set e `actual_end` null:
```
earliest_start_minutes = max(now_minutes, 0)
```

Garantisce che il solver non riposizioni nel passato un'operazione già iniziata.

---

## 7. Esempio concreto di schedulazione (seed TURBOPRESS-X500)

### 7.1 Priorità degli ordini allo stesso livello

La priorità di lavorazione di ordini allo stesso livello BOM è determinata dagli **archi RP del DAG del livello padre**. Un arco `RP_pred → RP_succ` impone che l'intero sottoalbero di `RP_pred.target` sia completato prima che qualsiasi op del sottoalbero di `RP_succ.target` inizi.

**Esempio: ordinamento dei MACROAGGREGATI (livello MACHINE)**

RP DAG a livello MACHINE:
```
RP-M-01 (→ MA-003)  ──→  RP-M-02 (→ MA-001)
                    └──→  RP-M-03 (→ MA-002)
```

Vincoli generati:
```
ops_pred(RP-M-01) = TUTTO il sottoalbero MA-003:
   MA-003 Op1,Op2,Op3 + AGG-010 ops + GRP-032,033,034 ops
                       + AGG-011 ops + GRP-035,036,037,038 ops
                       + AGG-012 ops + GRP-039,040 ops   ≈ 39 op

ops_succ(RP-M-02) = TUTTO il sottoalbero MA-001:
   AGG-001..005 ops + GRP-001..017 ops                   ≈ 80 op

→ Nessuna delle ~80 op di MA-001 può iniziare finché le ~39 op di MA-003
  (incluse le op di collaudo MA-003 Op1,Op2,Op3) non sono tutte finite.

ops_succ(RP-M-03) = TUTTO il sottoalbero MA-002:
   AGG-006..009 ops + GRP-018..031 ops

→ Nessuna op di MA-002 può iniziare finché MA-003 non è completo.
→ MA-001 e MA-002 possono procedere IN PARALLELO (nessun arco tra RP-M-02 e RP-M-03).
```

### 7.2 Esempio dettagliato: AGG-002 (Pompa Olio)

**Setup:** op = 240 min (½ giornata); WC-MILANO MECHANICAL, 3 corsie parallele. AGG-001 subtree finisce giorno D; AGG-002 subtree può iniziare giorno D+1 (rp_order RP-MA1-01→RP-MA1-02).

**RP DAG ad AGG-002 level** (determina ordine dei GROUP figli):
```
RP-A002-01(→GRP-004) ──→ RP-A002-02(→GRP-005) ──→ RP-A002-03(→GRP-006)
                     └──→ RP-A002-04(→GRP-007)   [parallelo a GRP-005/006]
```

```
GIORNO   │ CORSIA 1          CORSIA 2          CORSIA 3     │ VINCOLO
─────────┼─────────────────────────────────────────────────────────────────
D+1 am   │ GRP-004 Op1       —                 —            │ AGG-001 done
D+1 pm   │ GRP-004 Op2       —                 —            │ seq
D+2 am   │ GRP-004 Op3       —                 —            │ seq
─────────┼─────────────────────────────────────────────────────────────────
D+2 pm   │ GRP-005 Op1       GRP-007 Op1       AGG-002 Op1  │ GRP-004 DONE
         │                                                   │ rp→GRP-005 ✓
         │                                                   │ rp→GRP-007 ✓ (parallelo)
         │                                                   │ parent_wait Op1 ✓
D+3 am   │ GRP-005 Op2       GRP-007 Op2       [Op1 DONE]   │ seq
D+3 pm   │ GRP-005 Op3       GRP-007 Op3       —            │ seq
─────────┼─────────────────────────────────────────────────────────────────
D+4 am   │ GRP-006 Op1       [GRP-007 DONE]    AGG-002 Op2  │ GRP-005 DONE
         │                                                   │ rp→GRP-006 ✓
         │                                                   │ parent_wait Op2 +
         │                                                   │ intra-routing Op1 done ✓
D+4 pm   │ GRP-006 Op2       —                 [Op2 DONE]   │ seq
D+5 am   │ GRP-006 Op3       —                 —            │ seq
─────────┼─────────────────────────────────────────────────────────────────
D+5 pm   │ —                 —                 AGG-002 Op3  │ GRP-006 DONE
         │                                                   │ parent_wait Op3 +
         │                                                   │ intra-routing Op2 done ✓
D+6 am   │ —                 —                 [Op3 DONE]   │
D+6 pm   │ —                 —                 AGG-002 Op4  │ GRP-007 done[D+3pm]
         │                                                   │ + intra-routing Op3 done ✓
D+7 am   │ —                 —                 [Op4 DONE]   │
         │                   AGG-002 subtree COMPLETO        │ → sblocca AGG-003 subtree
         │                                                   │ → sblocca MA-001 Op2
```

**Punti chiave:**
- GRP-007 (Kit Tenute, parallelo) parte contemporaneamente a GRP-005 su una corsia diversa — corretto, nessun arco RP tra loro
- AGG-002 Op4 (collaudo GRP-007) avviene solo il giorno D+6, anche se GRP-007 era finito il D+3, perché la sequenza intra-routing Op3→Op4 lo impone
- `ops_pred` per il vincolo RP-MA1-02→RP-MA1-03 **include AGG-002 Op4** → AGG-003 subtree non può iniziare finché anche il collaudo finale di AGG-002 non è done

### 7.3 Come i tre vincoli si combinano per garantire l'ordine BOM

```
GRP-004 ops           (no predecessori dentro AGG-002)
   ↓ rp_order
GRP-005 ops                    GRP-007 ops          (in parallelo)
   ↓ rp_order
GRP-006 ops
   ↓ parent_wait + intra-routing
AGG-002 Op1 → Op2 → Op3 → Op4   (sequential; ogni op aspetta il suo GROUP)
   ↓ il tutto è in ops_pred del prossimo vincolo RP
AGG-003 subtree
```

La **sequenza intra-routing** è il collegamento critico: senza di essa AGG-002 Op2 potrebbe partire in parallelo con Op1, rendendo i collaudi non sequenziali. Con essa, la catena è garantita.

### 7.4 Effetto degli obiettivi sullo stesso piano (esempio numerico)

Supponiamo 3 gruppi disponibili: G1 (MECH, 3 risorse), G2 (MECH, 2 risorse), G3 (ELEC, 2 risorse).
Lavoro totale: 240 op-ore distribuite tra MECHANICAL e ELECTRICAL.

| Modo | Risultato tipico | Makespan | Gruppi usati |
|------|-----------------|----------|-------------|
| `CUSTOM` | Earliest-fit puro, tutto su G1 se possibile | 8 giorni | 1–2 |
| `MAXIMIZE_RESOURCE_UTILIZATION` | Distribuisce su G1+G2+G3 | 8 giorni | 3 |
| `MINIMIZE_OPERATORS` | Concentra su G1 e G3 (uno per skill) | 9 giorni | 2 |
| `FINISH_BY_DATE` (target=7gg) | Forza parallelismo massimo per rispettare target | 7 giorni | 3 |

**Perché MINIMIZE_OPERATORS può aumentare il makespan:** concentrando tutto su meno risorse
la capacità è più bassa → le operazioni si accodano → il piano si allunga.

**Perché MAXIMIZE_RESOURCE_UTILIZATION non riduce il makespan rispetto a CUSTOM:**
il termine secondario vale al massimo `n_groups` unità, meno di 1 minuto di makespan → non
sacrifica mai la durata per guadagnare un gruppo aggiuntivo.

---

## 8. Output DB — `ScheduleEntry`

| Campo | Valore nel modello a capacità |
|-------|------------------------------|
| `operator_id` | `null` — nessun operatore nominale |
| `resource_type_id` | ID del `ResourceType` usato |
| `workcenter_id` | workcenter dell'operazione |
| `scheduled_start` | `minutes_to_datetime(start_minutes, epoch)` |
| `scheduled_end` | `minutes_to_datetime(end_minutes, epoch)` |
| `status` | `SCHEDULED` |

Un'operazione spalmata su più giorni produce **più entries** con lo stesso `operation_id` (una per blocco giornaliero): rappresentazione corretta dell'hand-off nel Gantt.

---

## 9. Variabili d'ambiente

| Variabile | Default | Significato |
|-----------|---------|-------------|
| `CPSAT_CAPACITY_ENABLED` | `1` | 0 = solo greedy, 1 = greedy + CP-SAT |
| `CPSAT_CAPACITY_TIMEOUT` | `30` | Timeout CP-SAT in secondi |
| `CPSAT_CAPACITY_HORIZON_MARGIN` | `1.5` | Orizzonte CP-SAT = greedy_makespan × margin + 1440 |

---

## 10. File chiave

| File | Responsabilità |
|------|---------------|
| `reschedule_engine.py` | Celery task: orchestrazione completa, carica dati, vincoli, stadi, persist |
| `capacity_scheduler.py` | Greedy list-scheduling (Stadio 1) |
| `capacity_cpsat.py` | CP-SAT cumulativo ottimizzante (Stadio 2) |
| `cpsat_types.py` | Dataclass: `SchedulableOperation`, `QualifiedOperator`, `_SKILL_CAN_DO` |
| `shift_preprocessor.py` | Utility: `compute_epoch`, `compute_horizon_minutes`, `datetime_to_minutes`, `minutes_to_datetime` |
| `scheduler_orchestrator.py` | Façade: `run_schedule(scenario_id, use_celery=True/False)` |
| `models/resource.py` | `ResourceType` con `weekday_schedule` |
| `models/schedule.py` | `ScheduleScenario` (start_date, objective_mode, last_run_*), `ScheduleEntry` |
| `cpsat_model_builder.py` | **LEGACY — non usato in produzione** (vecchio modello per-operatore) |

---

## 11. Glossario

| Termine | Significato |
|---------|-------------|
| `epoch` | `start_date` a 00:00 UTC — origine degli interi CP-SAT |
| `horizon` | Minuti da `epoch` al limite dell'orizzonte di scheduling |
| `work_residual` / `D` | Minuti residui = `ceil(planned_duration × (1 - progress%/100))` |
| `ResourceGroup` | Gruppo (workcenter+skill) con capacità variabile per giorno della settimana |
| `lane` | "Corsia" parallela dentro un gruppo (una risorsa del tipo) |
| `hand-off` | Op spalmata su più giorni/corsie — più entries con stesso operation_id |
| `rp_order_constraints` | Vincoli Tipo B: ordine intra-livello via DAG Reference Point |
| `parent_wait_constraints` | Vincoli Tipo A: operazione padre attende completamento target RP |
| `makespan` | Fine dell'ultima operazione schedulata (minuti da epoch) |
| `warm-start` | Hint iniziale al CP-SAT derivato dalla soluzione greedy |
| `blocker` | Intervallo fisso nel CP-SAT che modella il fuori-orario per gruppo |
