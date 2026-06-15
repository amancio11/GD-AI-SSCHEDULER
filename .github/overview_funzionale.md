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


## ESMEPIO PIU' DETTAGLIATO:

Ottima domanda. La risposta è: **dipende dal DAG intra-livello degli aggregati**. Lasciami tracciare l'esempio completo partendo dai dati reali del seed.Perfetto. Ora ho tutti i dati reali del seed. Posso fare l'esempio **completamente accurato**.

---

## Il DAG reale del seed per MA-003

Dal `seed.py` gli archi del livello MA-003 sono:

```
RP-MA3-01 (→AGG-010 "Telaio Base")   ──► RP-MA3-02 (→AGG-011 "Montanti")
RP-MA3-01 (→AGG-010 "Telaio Base")   ──► RP-MA3-03 (→AGG-012 "Traversa")
```

Quindi AGG-010 non ha predecessori a questo livello. AGG-011 e AGG-012 devono aspettare AGG-010.

E il DAG dei gruppi dentro AGG-010 (dal seed):

```
RP-A010-01 (→GRP-032 "Longheroni Base")  ──► RP-A010-02 (→GRP-033 "Traversi Inferiori")
RP-A010-01 (→GRP-032 "Longheroni Base")  ──► RP-A010-03 (→GRP-034 "Piastre Ancoraggio")
```

GRP-032 non ha predecessori. GRP-033 e GRP-034 aspettano GRP-032.

---

## Esempio completo con dati reali — Solo il ramo MA-003

Assegno durate fittizie coerenti (random.seed(42), tra 120 e 480 min):

```
GRP-032 "Longheroni Base":     3 ops × 150min = 450min totali
GRP-033 "Traversi Inferiori":  2 ops × 200min = 400min totali  
GRP-034 "Piastre Ancoraggio":  3 ops × 120min = 360min totali

GRP-035 "Colonne Verticali":   2 ops × 180min = 360min (figlio di AGG-011)
GRP-036 "Rinforzi Laterali":   3 ops × 150min = 450min (figlio di AGG-011)
GRP-037 "Giunti Colonne":      2 ops × 120min = 240min (figlio di AGG-011)
GRP-038 "Tappi Chiusura":      2 ops × 90min  = 180min (figlio di AGG-011)

GRP-039 "Trave Superiore":     2 ops × 300min = 600min (figlio di AGG-012)
GRP-040 "Connettori Traversa": 2 ops × 150min = 300min (figlio di AGG-012)

AGG-010: 3 ops con RP → GRP-032, GRP-033, GRP-034 × ~200min
AGG-011: 4 ops con RP → GRP-035, GRP-036, GRP-037, GRP-038 × ~180min
AGG-012: 2 ops con RP → GRP-039, GRP-040 × ~240min

MA-003:  3 ops con RP → AGG-010, AGG-011, AGG-012 × ~300min
```

Operatori WC-BERGAMO: Mario (MECHANICAL), Luigi (MECHANICAL), Sara (MULTI) — 3 persone.

---

## Timeline minuto per minuto

```
════════════════════════════════════════════════════════════════════════
T=0  START — Chi può iniziare SUBITO?
════════════════════════════════════════════════════════════════════════

Regola: un gruppo può iniziare subito SE non ha predecessori nel DAG
        intra-livello dell'aggregato padre.

DAG livello AGG-010:  GRP-032 non ha predecessori → LIBERO
DAG livello AGG-011:  GRP-035 non ha predecessori → LIBERO  
DAG livello AGG-012:  GRP-039 non ha predecessori → LIBERO

Quindi a T=0 partono:
  Mario  → op-GRP032-1 "Longheroni Base" [150min]
  Luigi  → op-GRP035-1 "Colonne Verticali" [180min]
  Sara   → op-GRP039-1 "Trave Superiore" [300min]

                 Mario    Luigi    Sara
  T=0 ─────────[GRP032][GRP035][GRP039]────────────────────────────►
```

```
════════════════════════════════════════════════════════════════════════
T=150  Mario finisce op-GRP032-1
════════════════════════════════════════════════════════════════════════

GRP-032 ha ancora op-GRP032-2 e op-GRP032-3 da fare.
Mario inizia op-GRP032-2 [150min].

                 Mario    Luigi    Sara
  T=150 ────────[GRP032][GRP035][GRP039]──────────────────────────────►
                  (op2)
```

```
════════════════════════════════════════════════════════════════════════
T=180  Luigi finisce op-GRP035-1
════════════════════════════════════════════════════════════════════════

GRP-035 ha ancora op-GRP035-2.
Luigi inizia op-GRP035-2 [180min].

                 Mario    Luigi    Sara
  T=180 ────────[GRP032][GRP035][GRP039]──────────────────────────────►
                  (op2)   (op2)
```

```
════════════════════════════════════════════════════════════════════════
T=300  Mario finisce op-GRP032-2
════════════════════════════════════════════════════════════════════════

Mario inizia op-GRP032-3 [150min].

                 Mario    Luigi    Sara
  T=300 ────────[GRP032][GRP035][GRP039]──────────────────────────────►
                  (op3)   (op2)
```

```
════════════════════════════════════════════════════════════════════════
T=360  Luigi finisce op-GRP035-2  →  GRP-035 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

[TIPO B livello AGG-011]
  RP-A011-01 (→GRP-035) ──► RP-A011-02 (→GRP-036)
  RP-A011-01 (→GRP-035) ──► RP-A011-03 (→GRP-037)
  RP-A011-01 (→GRP-035) ──► RP-A011-04 (→GRP-038)

  GRP-035 completato → GRP-036, GRP-037, GRP-038 sbloccati

Luigi ora può iniziare GRP-036 "Rinforzi Laterali" [150min].

                 Mario    Luigi    Sara
  T=360 ────────[GRP032][GRP036][GRP039]──────────────────────────────►
                  (op3)   (op1)
```

```
════════════════════════════════════════════════════════════════════════
T=450  Mario finisce op-GRP032-3  →  GRP-032 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

[TIPO B livello AGG-010]
  RP-A010-01 (→GRP-032) ──► RP-A010-02 (→GRP-033)
  RP-A010-01 (→GRP-032) ──► RP-A010-03 (→GRP-034)

  GRP-032 completato → GRP-033 e GRP-034 sbloccati

Mario inizia GRP-033 "Traversi Inferiori" [200min].

                 Mario    Luigi    Sara
  T=450 ────────[GRP033][GRP036][GRP039]──────────────────────────────►
                  (op1)   (op1)
```

```
════════════════════════════════════════════════════════════════════════
T=510  Luigi finisce op-GRP036-1
════════════════════════════════════════════════════════════════════════

Luigi inizia op-GRP036-2 [150min].
```

```
════════════════════════════════════════════════════════════════════════
T=600  Sara finisce op-GRP039-1
════════════════════════════════════════════════════════════════════════

GRP-039 ha ancora op-GRP039-2.
Sara inizia op-GRP039-2 [300min].
```

```
════════════════════════════════════════════════════════════════════════
T=650  Mario finisce op-GRP033-1
       Luigi finisce op-GRP036-2
════════════════════════════════════════════════════════════════════════

Mario → op-GRP033-2 [200min]
Luigi → op-GRP036-3 [150min]
```

```
════════════════════════════════════════════════════════════════════════
T=800  Luigi finisce op-GRP036-3  →  GRP-036 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

[TIPO B livello AGG-011]
  RP-A011-02 (→GRP-036) ──► ??? (dal seed non ci sono archi uscenti da 02)

  Intanto GRP-037 e GRP-038 erano già sbloccati da T=360.
  Luigi può iniziare GRP-037 "Giunti Colonne" [120min].
```

```
════════════════════════════════════════════════════════════════════════
T=850  Mario finisce op-GRP033-2  →  GRP-033 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

Mario inizia GRP-034 "Piastre Ancoraggio" [120min].
```

```
════════════════════════════════════════════════════════════════════════
T=900  Luigi finisce GRP-037  →  GRP-037 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

Luigi inizia GRP-038 "Tappi Chiusura" [90min].
```

```
════════════════════════════════════════════════════════════════════════
T=900  Sara finisce op-GRP039-2  →  GRP-039 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

[TIPO B livello AGG-012]
  RP-A012-01 (→GRP-039) ──► RP-A012-02 (→GRP-040)
  GRP-039 completato → GRP-040 sbloccato

Sara inizia GRP-040 "Connettori Traversa" [150min].
```

```
════════════════════════════════════════════════════════════════════════
T=970  Mario finisce GRP-034  →  GRP-034 COMPLETATO ✓
T=990  Luigi finisce GRP-038  →  GRP-038 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

AGG-011 ora ha TUTTI i suoi gruppi completati:
  GRP-035 ✓ T=360,  GRP-036 ✓ T=800
  GRP-037 ✓ T=900,  GRP-038 ✓ T=990

[TIPO A]  op-MA003-2 ha RP-MA3-02 → AGG-011
          ops_target = op di AGG-011 + GRP-035..038 (tutte completate)
          → ma op-MA003-2 aspetta il completamento delle op di AGG-011 stesso!
          AGG-011 non ha ancora le sue operazioni schedulate.

AGG-010 ha i suoi gruppi completati:
  GRP-032 ✓ T=450,  GRP-033 ✓ T=850,  GRP-034 ✓ T=970

[TIPO A]  op-AGG010-1 (RP-A010-01 → GRP-032): aspettava GRP-032 ✓ T=450
          op-AGG010-2 (RP-A010-02 → GRP-033): aspettava GRP-033 ✓ T=850
          op-AGG010-3 (RP-A010-03 → GRP-034): aspettava GRP-034 ✓ T=970

          AGG-010 sbloccato a T=970 (quando l'ultimo gruppo è pronto)

Mario inizia op-AGG010-1 [200min]
Luigi inizia op-AGG011-1 [180min]  ← AGG-011 gruppi finiti, AGG-011 stesso può iniziare
```

```
════════════════════════════════════════════════════════════════════════
T=1050  Sara finisce GRP-040  →  GRP-040 COMPLETATO ✓
════════════════════════════════════════════════════════════════════════

AGG-012 ha tutti i suoi gruppi completati:
  GRP-039 ✓ T=900,  GRP-040 ✓ T=1050

[TIPO A] op-AGG012-1 (RP-A012-01 → GRP-039): aspettava GRP-039 ✓
         op-AGG012-2 (RP-A012-02 → GRP-040): aspettava GRP-040 ✓

Sara inizia op-AGG012-1 [240min]

... (continuazione fino a completamento di tutti gli aggregati) ...
```

```
════════════════════════════════════════════════════════════════════════
T≈1800  AGG-010, AGG-011, AGG-012 tutti completati
════════════════════════════════════════════════════════════════════════

[TIPO A livello MA-003]
  op-MA003-1 (RP-MA3-01 → AGG-010): aspettava AGG-010 + tutti suoi figli ✓
  op-MA003-2 (RP-MA3-02 → AGG-011): aspettava AGG-011 + tutti suoi figli ✓
  op-MA003-3 (RP-MA3-03 → AGG-012): aspettava AGG-012 + tutti suoi figli ✓

  Ora le 3 operazioni di MA-003 possono iniziare (in parallelo tra loro,
  perché il routing di MA-003 è SIMULTANEOUS e non c'è DAG tra le 3).

  Ma attenzione al DAG intra-livello MA-003:
  RP-MA3-01 (→AGG-010) ──► RP-MA3-02 (→AGG-011)
  RP-MA3-01 (→AGG-010) ──► RP-MA3-03 (→AGG-012)

  [TIPO B] ops(AGG-010 tree) devono finire prima di ops(AGG-011 tree)
           ops(AGG-010 tree) devono finire prima di ops(AGG-012 tree)

  Ma noi siamo già a T=1800 e AGG-010 è finito prima di AGG-011 e AGG-012,
  quindi il Tipo B è già soddisfatto dai tempi reali. Le 3 op di MA-003
  possono partire tutte in parallelo.

T≈1800: op-MA003-1 [300min], op-MA003-2 [300min], op-MA003-3 [300min]
        (distribuite sui 3 operatori disponibili)

T≈2100: MA-003 completato ✓

[TIPO B livello MACHINE]
  RP-M-01 (→MA-003) ──► RP-M-02 (→MA-001)
  RP-M-01 (→MA-003) ──► RP-M-03 (→MA-002)

  MA-003 tree completato → MA-001 e MA-002 sbloccati
  Da T≈2100: tutto il sotto-albero di MA-001 e MA-002 può iniziare
```

---

## Risposta diretta alla tua domanda

**GRP-032 è il primo gruppo schedulato all'interno del ramo MA-003?**

Sì, ma non da solo. A T=0 partono **contemporaneamente** tre gruppi:

| T=0 | Gruppo | Perché |
|---|---|---|
| GRP-032 | "Longheroni Base" | Non ha predecessori nel DAG di AGG-010 |
| GRP-035 | "Colonne Verticali" | Non ha predecessori nel DAG di AGG-011 |
| GRP-039 | "Trave Superiore" | Non ha predecessori nel DAG di AGG-012 |

**GRP-032 è il "primo" del suo aggregato** (AGG-010), ma parallelamente partono anche i "primi" degli altri aggregati. Il solver li avvia tutti a T=0 perché non c'è nessun vincolo che li impedisce, e massimizzare il lavoro parallelo è sempre una buona strategia per finire prima.

L'unico motivo per cui un gruppo non parte a T=0 è se ha un predecessore nel DAG intra-aggregato: GRP-033 e GRP-034 aspettano GRP-032, GRP-036/037/038 aspettano GRP-035, GRP-040 aspetta GRP-039.