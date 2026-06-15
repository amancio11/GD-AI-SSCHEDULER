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

Il solver trova una assegnazione che soddisfa tutti questi vincoli.