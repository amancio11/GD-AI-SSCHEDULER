## Come funziona lo scheduler, con un esempio reale

Usiamo la TURBOPRESS-X500. Immagina che il planner premi "Schedula".

### Il problema da risolvere

Hai **259 operazioni** da assegnare a **20 operatori** in un arco di **90 giorni**. Le operazioni hanno durate diverse, tipi diversi (ELECTRICAL/MECHANICAL), e devono rispettare un ordine preciso (non puoi montare l'idraulica se la struttura non è pronta).

Il solver CP-SAT trova una assegnazione `(operazione → operatore, orario_inizio)` che rispetti tutti i vincoli.

---

### Step-by-step con l'esempio

#### Dati di input (cosa viene caricato)

```
Operazioni schedulabili (status != COMPLETED):
  op-MACH-1: "Collaudo Struttura"     MECHANICAL  480min  RP=RP-M-01  → deve aspettare MA-003
  op-MACH-2: "Collaudo Idraulico"     MECHANICAL  480min  RP=RP-M-02  → deve aspettare MA-001
  op-MA003-1: "Montaggio Telaio Base" MECHANICAL  240min  RP=RP-MA3-01 → deve aspettare AGG-010
  op-MA003-2: "Montaggio Montanti"    MECHANICAL  180min  RP=RP-MA3-02 → deve aspettare AGG-011
  op-AGG010-1: "Lavorazione Telaio"   MECHANICAL  120min  (no RP → ops libere)
  op-GRP032-1: "Assemblaggio flangia" MECHANICAL  90min   (no RP → ops libere)
  ... (259 totali)

Operatori disponibili:
  Mario Bianchi  MECHANICAL  WC-BERGAMO  turni: Lun-Ven mattina
  Luigi Rossi    MECHANICAL  WC-BERGAMO  turni: Lun-Ven pomeriggio
  ...

Componenti mancanti:
  VLV-2200: arriva fra 7 giorni → minuto 10080 dall'epoch
  (in GRP-001 → tutte le op di GRP-001 bloccate fino al minuto 10080)
```

---

#### STEP PRE-CP-SAT: Costruzione dei vincoli di precedenza (Step 4d)

Questo è il cuore della logica. Si costruiscono **due tipi di vincoli**:

**Tipo A — "Il padre aspetta il figlio" (MANCANTE nell'implementazione attuale)**

Il seed crea l'operazione `op-MACH-2` con `reference_point_id = RP-M-02`. RP-M-02 punta a MA-001. Questo significa:

```
op-MACH-2 NON PUÒ INIZIARE finché MA-001 (e tutti i suoi figli: 
AGG-001..005, GRP-001..020) non sono TUTTI COMPLETATI
```

In CP-SAT:
```python
# completion_MA001 = il momento in cui finisce l'ULTIMA operazione di MA-001 + figli
completion_MA001 = max(
  end(op-MA001-1), end(op-MA001-2), end(op-MA001-3), end(op-MA001-4), end(op-MA001-5),
  end(op-AGG001-1), end(op-AGG001-2), ..., end(op-GRP020-4)
)
start(op-MACH-2) >= completion_MA001
```

Questo vale per **ogni livello**:
- `op-MA001-3` (con RP-MA1-03 che punta ad AGG-003) aspetta `completion(AGG-003 + GRP-007..009)`
- `op-AGG001-2` (con RP-A001-02 che punta a GRP-002) aspetta `completion(GRP-002)`

**Tipo B — "Ordinamento intra-livello tra figli" (già implementato, parzialmente)**

Il DAG dice: nel livello MACHINE, MA-003 deve essere completato prima di MA-001 (arco RP-M-01 → RP-M-02). Quindi:

```
TUTTE le op di MA-003 (+ AGG-010..012 + GRP-032..040) devono finire
prima che QUALSIASI op di MA-001 (+ figli) inizi
```

In CP-SAT:
```python
completion_MA003_tree = max(end(tutte le op di MA-003 + figli))
for op in tutte_le_op_di_MA001_e_figli:
    start(op) >= completion_MA003_tree
```

---

#### Come si concatenano i vincoli (la cascata completa)

```
GIORNO 0
  │
  ├─ GRP-032..040 iniziano SUBITO (non hanno RP, sono figli di MA-003)
  │   Mario: op-GRP032-1 [90min] → finisce minuto 90
  │   Luigi: op-GRP033-1 [120min] → finisce minuto 120
  │   ...
  │
  │  [Tipo A] op-MA003-1 ha RP-MA3-01 → AGG-010
  │           AGG-010 ha RP-A010-01 → GRP-032
  │           Quindi: start(op-MA003-1) >= max(end(op-GRP032-*))
  │
  ├─ AGG-010..012 iniziano quando i loro gruppi figli sono pronti
  │   op-AGG010-1: start >= minuto 120 (quando GRP-032..034 finiscono)
  │   ...
  │
  │  [Tipo A] op-MA003-1 ha RP-MA3-01 → AGG-010
  │           start(op-MA003-1) >= max(end(op-AGG010-*)) = minuto 360
  │
  ├─ MA-003 finisce → [Tipo B] sblocca MA-001 e MA-002
  │   completion_MA003_tree = minuto 480
  │   start(tutte le op di MA-001 e MA-002) >= minuto 480
  │
  ├─ GRP-001..020 iniziano al minuto 480 (MA-003 completato)
  │   ECCEZIONE: GRP-001 ha VLV-2200 mancante → start >= minuto 10080
  │
  ├─ AGG-001..005 iniziano quando i loro gruppi sono pronti
  │
  │  [Tipo A] op-MA001-3 ha RP-MA1-03 → AGG-003
  │           start(op-MA001-3) >= max(end(op-AGG003-*))
  │
  └─ op-MACH-2 ("Collaudo Idraulico") inizia quando MA-001 + figli sono TUTTI finiti
     start(op-MACH-2) >= completion_MA001_tree
```

---

#### I vincoli CP-SAT applicati dal builder

Una volta calcolati i vincoli di precedenza, il builder aggiunge in ordine:

1. **Assignment**: ogni operazione ha almeno 1 operatore qualificato (stessa WC, skill compatibile)
2. **Shift nooverlap**: se Mario non è disponibile il giovedì, nessuna operazione può usare Mario quel giorno
3. **Operator nooverlap**: Mario non può fare `op-GRP032-1` e `op-GRP033-1` contemporaneamente
4. **Precedenze dirette** (`precedence_pairs`): attualmente vuoto
5. **RP order constraints** (Tipo B): ordinamento intra-livello via DAG RP ← già implementato
6. **Parent wait constraints** (Tipo A): op padre aspetta completamento figlio ← **MANCANTE**
7. **Missing components**: `start(op-GRP001-*) >= minuto 10080`


## La cascata bottom-up

Il solver CP-SAT non "sceglie" un ordine top-down. I vincoli lo **forzano** a lavorare bottom-up automaticamente.

### Esempio concreto: RP-M-01 punta a MA-003 (nessun predecessore)

```
MACHINE
  └── op-MACH-1  (reference_point_id = RP-M-01 → MA-003)

MA-003 "Struttura Portante"
  ├── op-MA003-1  (reference_point_id = RP-MA3-01 → AGG-010)
  ├── op-MA003-2  (reference_point_id = RP-MA3-02 → AGG-011)
  └── op-MA003-3  (reference_point_id = RP-MA3-03 → AGG-012)

AGG-010 "Telaio Base"
  ├── op-AGG010-1  (reference_point_id = RP-A010-01 → GRP-032)
  └── op-AGG010-2  (reference_point_id = RP-A010-02 → GRP-033)

GRP-032 "Longheroni Base"
  ├── op-GRP032-1  (nessun RP)
  ├── op-GRP032-2  (nessun RP)
  └── op-GRP032-3  (nessun RP)
```

### I vincoli Tipo A che vengono generati

```
parent_wait_constraints contiene, tra gli altri:

(A1) ops_target=[op-GRP032-*], parent_op=op-AGG010-1
     → start(op-AGG010-1) >= max(end di tutte le op di GRP-032)

(A2) ops_target=[op-GRP033-*], parent_op=op-AGG010-2
     → start(op-AGG010-2) >= max(end di tutte le op di GRP-033)

(A3) ops_target=[op-AGG010-*, op-GRP032-*, op-GRP033-*, ...], parent_op=op-MA003-1
     → start(op-MA003-1) >= max(end di AGG-010 + tutti i suoi gruppi)

(A4) ops_target=[tutte le op di MA-003 + AGG-010..012 + GRP-032..040], parent_op=op-MACH-1
     → start(op-MACH-1) >= max(end di tutto il sotto-albero di MA-003)
```

### L'ordine che emerge automaticamente

```
T=0   GRP-032, GRP-033, GRP-034 iniziano subito (nessun vincolo li blocca)
      GRP-035, GRP-036, GRP-037, GRP-038 iniziano subito (figli di AGG-011)
      GRP-039, GRP-040 iniziano subito (figli di AGG-012)

T=90  GRP-032 finito → (A1) sblocca op-AGG010-1
      GRP-033 finito → (A2) sblocca op-AGG010-2

T=200 AGG-010 finito → contribuisce a (A3) per op-MA003-1
      AGG-011 finito → contribuisce al vincolo per op-MA003-2
      AGG-012 finito → contribuisce al vincolo per op-MA003-3

T=380 op-MA003-1, op-MA003-2, op-MA003-3 possono iniziare

T=560 MA-003 completamente finito → (A4) sblocca op-MACH-1
      E il Tipo B sblocca tutto il sotto-albero di MA-001 e MA-002
```

---

## La risposta diretta alla tua domanda

Sì. Se RP-M-01 (che punta a MA-003) non ha predecessori nel DAG, il solver lavorerà **prima i gruppi GRP-032..040, poi gli aggregati AGG-010..012, poi le operazioni di MA-003, e solo alla fine op-MACH-1**. Questa cascata emerge dai vincoli Tipo A a ogni livello, non da logica esplicita di ordinamento.

L'unica cosa che il solver può fare in parallelo sono i rami indipendenti: GRP-032 e GRP-035 (che stanno su rami diversi dello stesso livello) possono essere lavorati contemporaneamente da operatori diversi, perché non c'è nessun vincolo tra loro.