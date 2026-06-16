// frontend/src/pages/SchedulerLogic.tsx
//
// Pagina di documentazione tecnica sulla logica dello scheduler CP-SAT.
// Stato aggiornato: rp_order_constraints e parent_wait_constraints sono ATTIVI.

import { useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Cpu,
  Clock,
  Target,
  AlertTriangle,
  Layers,
  Lock,
  Sparkles,
} from "lucide-react";

export default function SchedulerLogic(): JSX.Element {
  const [scenario, setScenario] = useState<string>("manual");
  return (
    <div className="space-y-4 max-w-5xl">

      {/* Overview */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Cpu className="h-5 w-5" />
            Come funziona lo scheduler
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm leading-relaxed">
          <p>
            Lo scheduler usa <strong>OR-Tools CP-SAT</strong> (Constraint Programming – Satisfiability)
            per costruire un piano di produzione che assegna ogni operazione a un operatore
            e a una finestra temporale, rispettando dipendenze, disponibilità e l&apos;ordine
            gerarchico della BOM definito dal DAG dei Reference Point.
          </p>
          <p>
            Il problema è un <strong>Resource-Constrained Project Scheduling Problem (RCPSP)</strong>:
            ogni operazione è un task con durata residua, un singolo operatore richiesto,
            e vincoli di precedenza che derivano sia dalla gerarchia BOM (tramite RP DAG)
            sia dalle relazioni padre-figlio dirette.
          </p>
          <div className="flex gap-2 flex-wrap">
            <Badge variant="outline" className="font-mono">CP-SAT</Badge>
            <Badge variant="outline" className="font-mono">linearization_level=1</Badge>
            <Badge variant="outline" className="font-mono">search_branching=6 (PORTFOLIO)</Badge>
            <Badge variant="outline" className="font-mono">max_time=30s</Badge>
            <Badge variant="outline" className="font-mono">stop_after_first_solution=true</Badge>
          </div>
        </CardContent>
      </Card>

      {/* IL PUNTO CENTRALE — il DAG è il cuore dello scheduling */}
      <Card className="border-purple-300 bg-purple-50">
        <CardHeader>
          <CardTitle className="text-base text-purple-900">
            Il DAG dei Reference Point — il cuore della logica di precedenza
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-purple-900 leading-relaxed">
          <p>
            Il DAG visualizzato nel <strong>DAG Viewer</strong> non è solo una visualizzazione:
            è la struttura dati da cui lo scheduler ricava <em>tutte</em> le precedenze tra ordini.
            Senza di esso, tutte le operazioni verrebbero schedulate in parallelo ignorando la gerarchia BOM.
          </p>
          <p>
            La logica funziona su <strong>due meccanismi distinti ma complementari</strong>,
            entrambi attivi nel codice corrente:
          </p>

          <div className="rounded-lg border border-purple-300 bg-white p-4 space-y-3">
            <div>
              <div className="font-semibold text-sm mb-1">
                Tipo A — Parent-wait (relazione padre→figlio diretta)
              </div>
              <div className="text-xs leading-relaxed mb-2">
                Ogni operazione che ha un <code className="font-mono bg-stone-100 px-1 rounded">reference_point_id</code> appartiene
                a un ordine di livello superiore (MACHINE, MACROAGGREGATE o AGGREGATE).
                Quella operazione <strong>non può iniziare finché il suo ordine target — l&apos;ordine figlio
                puntato dal RP — e tutti i suoi discendenti BOM non sono completati</strong>.
              </div>
              <div className="text-xs font-mono bg-stone-900 text-stone-100 p-2 rounded whitespace-pre">
{`# reschedule_engine.py — Step 4d (parent_wait_constraints)
for op in schedulable_ops:
    if op.reference_point_id is None: continue
    target_po_id = rp_id_to_po_id[op.reference_point_id]
    ops_target = collect_ops_recursive(target_po_id, children_map)
    parent_wait_constraints.append((ops_target, op.id))

# cpsat_model_builder.py — _add_parent_wait_constraints()
completion = model.NewIntVar(0, horizon, f"pw_completion_{idx}")
model.AddMaxEquality(completion, [op_end[t] for t in active_target])
model.Add(op_start[parent_op_id] >= completion)`}
              </div>
              <div className="text-xs mt-2 text-purple-800">
                <strong>Esempio concreto:</strong> L&apos;operazione dell&apos;ordine MACHINE con RP-M-02 (che punta a MA-001 "Gruppo Idraulico")
                non può iniziare finché MA-001 <em>e tutti i suoi aggregati AGG-001..005 e i loro gruppi</em> non sono terminati.
                La raccolta è <strong>ricorsiva</strong> sull&apos;albero BOM.
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-purple-300 bg-white p-4 space-y-3">
            <div>
              <div className="font-semibold text-sm mb-1">
                Tipo B — RP DAG (precedenze intra-livello tra ordini fratelli)
              </div>
              <div className="text-xs leading-relaxed mb-2">
                La tabella <code className="font-mono bg-stone-100 px-1 rounded">reference_point_precedences</code> definisce un DAG
                <em> tra RP dello stesso livello padre</em>. Ogni arco
                <code className="font-mono bg-stone-100 px-1 rounded">RP_pred → RP_succ</code> dice:
                <strong> l&apos;ordine target di RP_pred deve terminare completamente prima che
                le operazioni dell&apos;ordine target di RP_succ possano iniziare</strong>.
              </div>
              <div className="text-xs font-mono bg-stone-900 text-stone-100 p-2 rounded whitespace-pre">
{`# Esempio: a livello MACHINE
# MA-003 (struttura) → MA-001 (idraulico)
# MA-003 (struttura) → MA-002 (elettrico)
# → la struttura portante si monta prima

# reschedule_engine.py — Step 4d (rp_order_constraints)
for prec in prec_rows:
    ops_pred = collect_ops_recursive(rp_id_to_po_id[prec.predecessor_rp_id])
    ops_succ = collect_ops_recursive(rp_id_to_po_id[prec.rp_id])
    rp_order_constraints.append((ops_pred, ops_succ))

# cpsat_model_builder.py — _add_rp_order_constraints()
completion = model.NewIntVar(0, horizon, f"rp_completion_{idx}")
model.AddMaxEquality(completion, [op_end[p] for p in active_pred])
for succ_op in active_succ:
    model.Add(op_start[succ_op] >= completion)`}
              </div>
              <div className="text-xs mt-2 text-purple-800">
                <strong>Esempio concreto:</strong> Nel DAG a livello MACHINE esistono gli archi
                MA-003→MA-001 e MA-003→MA-002. Lo scheduler vincola:
                <em> tutte le operazioni di MA-003 e dei suoi figli BOM devono terminare prima
                che qualsiasi operazione di MA-001 o MA-002 inizi</em>.
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-purple-300 bg-white p-4">
            <div className="font-semibold text-sm mb-2">Propagazione gerarchica completa</div>
            <div className="text-xs leading-relaxed space-y-1">
              <div>Questi due meccanismi si combinano e si propagano a cascata su tutti i livelli:</div>
              <div className="font-mono text-xs bg-stone-50 p-2 rounded mt-1 space-y-0.5">
                <div>MACHINE ops[RP→MA-001]  →  aspetta completamento ricorsivo di MA-001</div>
                <div className="pl-4">MA-001 ops[RP→AGG-001]  →  aspetta completamento ricorsivo di AGG-001</div>
                <div className="pl-8">AGG-001 ops[RP→GRP-001]  →  aspetta completamento di GRP-001</div>
                <div className="pl-12">GRP-001 ops  →  nessun RP (figli sono componenti, nessuna op)</div>
              </div>
              <div className="mt-2">
                Il risultato è che lo scheduler rispetta l&apos;ordine naturale di montaggio della macchina
                dal basso verso l&apos;alto: prima i gruppi, poi gli aggregati, poi i macroaggregati,
                infine la macchina — esattamente come richiesto dalla logica produttiva.
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Pipeline */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pipeline di rescheduling — i 7 step</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <PipelineStep
            num={1}
            title="Snapshot dello stato corrente"
            desc="Legge tutte le operations attive del machine_order, calcola progress_pct e residual_minutes = max(planned × (1 - progress/100), MIN_OP_DURATION)."
          />
          <PipelineStep
            num={2}
            title="Filtro operazioni completate"
            desc="Esclude operations con status=COMPLETED o progress_pct≥100. Esclude COMPONENT (acquisto/non tracciate)."
          />
          <PipelineStep
            num={3}
            title="Pre-processing turni operatore"
            desc="build_operator_available_slots(): per ogni operatore e per ogni giorno dell'orizzonte (~28gg), costruisce i blocchi (start_minute, end_minute) dai turni nel operator_calendar, sottraendo break_duration_minutes e assenze."
          />
          <PipelineStep
            num={4}
            title="Qualificazioni operatore-operazione"
            desc="Per ogni op crea il set di qualified_operators: skill compatibile (ELECTRICAL/MECHANICAL/MULTI vs operation_type), stesso workcenter, almeno uno slot disponibile nell'orizzonte."
          />
          <PipelineStep
            num={4.5}
            title="Step 4d — Calcolo vincoli RP DAG (ATTIVO)"
            highlight
            desc="Carica tutti i RP e gli archi di precedenza del machine_model. Costruisce children_map dell'albero BOM. Per ogni arco del DAG: raccoglie ricorsivamente le op dei sottoalberi pred e succ → rp_order_constraints. Per ogni op con reference_point_id: raccoglie le op del target → parent_wait_constraints. Entrambe le liste vengono passate al CpsatModelBuilder."
          />
          <PipelineStep
            num={5}
            title="Build CP-SAT model"
            desc="Crea start_var, end_var, interval_var per ogni op; crea assign_var booleane operator×op; chiama in sequenza: _add_assignment_constraints → _add_shift_nooverlap → _add_operator_nooverlap → _add_precedence_constraints → _add_rp_order_constraints → _add_parent_wait_constraints → _add_missing_component_constraints → _set_objective."
          />
          <PipelineStep
            num={6}
            title="Solve con greedy hint"
            desc="Imposta solution_hints da una soluzione greedy (assegna op all'operatore con load minimo, ASAP), poi cp_solver.Solve(model). Stop al primo FEASIBLE. Con hint: 1-5s invece di ~30s."
          />
          <PipelineStep
            num={7}
            title="Estrazione e scrittura DB"
            desc="solution_extractor traduce assign_var attivi e start/end in schedule_entries (DELETE + INSERT sul scenario). Notifica WebSocket RESCHEDULE_COMPLETE."
          />
        </CardContent>
      </Card>

      {/* Scenarios */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Target className="h-5 w-5" />
            Modalità di obiettivo per scenario
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs value={scenario} onValueChange={setScenario}>
            <TabsList className="mb-3 flex-wrap h-auto gap-1">
              <TabsTrigger value="manual">MANUAL</TabsTrigger>
              <TabsTrigger value="makespan">MAKESPAN</TabsTrigger>
              <TabsTrigger value="deadline">DEADLINE</TabsTrigger>
              <TabsTrigger value="balanced">BALANCED</TabsTrigger>
              <TabsTrigger value="resource">RESOURCE</TabsTrigger>
            </TabsList>

            <TabsContent value="manual" className="space-y-3 text-sm">
              <ScenarioBlock
                title="MANUAL — soddisfacibilità pura (default attuale)"
                description={
                  <>
                    Modalità attualmente attiva in produzione: <code className="font-mono bg-stone-100 px-1 rounded">_set_objective</code> è
                    {" "}<code className="font-mono bg-stone-100 px-1 rounded">pass</code> e il solver cerca qualsiasi assegnazione
                    che soddisfi i vincoli, inclusi i vincoli RP DAG. Fermandosi alla prima soluzione FEASIBLE.
                    Tutti i vincoli di precedenza DAG sono comunque attivi e rispettati.
                  </>
                }
                formula="# nessun model.Minimize / Maximize\n# tutti i vincoli RP DAG sono attivi\nstop_after_first_solution = True"
                useCase="Sviluppo, debugging. Scenari dove si vuole verificare la fattibilità prima di ottimizzare, o dove qualsiasi piano consistente con la BOM è accettabile."
                tradeoffs="Nessuna garanzia di ottimalità sul tempo totale o sul carico. Due esecuzioni consecutive su stesso scenario possono dare piani diversi (PORTFOLIO è multi-strategia). La struttura BOM è comunque rispettata."
                isCurrent
              />
            </TabsContent>

            <TabsContent value="makespan" className="space-y-3 text-sm">
              <ScenarioBlock
                title="MAKESPAN — minimizza il tempo totale di completamento"
                description="Introduce una variabile makespan vincolata a essere ≥ dell'end_var di ogni operazione e la minimizza. Tutti i vincoli RP DAG rimangono attivi: il solver cerca il piano più corto che rispetti l'ordine BOM."
                formula="makespan = model.NewIntVar(0, horizon, 'makespan')\nmodel.AddMaxEquality(makespan, [end_var[op] for op in ops])\nmodel.Minimize(makespan)"
                useCase="Pre-produzione o quando la deadline è lasca e si vuole liberare capacità per la prossima macchina il prima possibile."
                tradeoffs="Tende a saturare operatori e workcenter nelle prime finestre disponibili, può lasciare ore vuote a fine giornata e penalizzare l'equilibrio del carico."
              />
            </TabsContent>

            <TabsContent value="deadline" className="space-y-3 text-sm">
              <ScenarioBlock
                title="DEADLINE — rispetta una data di consegna contrattuale"
                description={
                  <>
                    Introduce <code className="font-mono bg-stone-100 px-1 rounded">due_date_minutes</code> e una variabile
                    {" "}<code className="font-mono bg-stone-100 px-1 rounded">tardiness</code> per ogni operazione finale (ordine MACHINE).
                    Minimizza la somma delle tardiness pesate, mantenendo i vincoli RP DAG.
                  </>
                }
                formula="tardiness[op] = max(0, end_var[op] - due_date)\nmodel.Minimize(sum(tardiness))"
                useCase="Ordine vincolato a data di spedizione. Lo scheduler cerca di stare dentro la deadline rispettando la sequenza BOM; se infeasible, il modulo infeasibility_analyzer spiega il motivo."
                tradeoffs="Se la deadline è fattibile, il piano può lasciare slack volontario. Se la sequenza BOM richiede più tempo della deadline, si ottiene INFEASIBLE."
              />
            </TabsContent>

            <TabsContent value="balanced" className="space-y-3 text-sm">
              <ScenarioBlock
                title="BALANCED — bilancia il carico tra operatori"
                description="Minimizza la differenza tra il carico massimo e il carico minimo tra gli operatori qualificati per ogni tipo di operazione. Riduce stress e colli di bottiglia su singole persone, rispettando comunque la sequenza BOM."
                formula="load[opr] = sum(duration[op] * assign[op,opr])\nimbalance = max(load) - min(load)\nmodel.Minimize(imbalance)"
                useCase="Piano settimanale stabile, lontano da urgenze. Particolarmente utile in officine con operatori di skill simili e rotazione programmata."
                tradeoffs="Sacrifica il makespan totale (+5-15% tipicamente). Non sfrutta al massimo l'operatore più veloce."
              />
            </TabsContent>

            <TabsContent value="resource" className="space-y-3 text-sm">
              <ScenarioBlock
                title="RESOURCE — minimizza l'uso di operatori MULTI-skill"
                description="Penalizza l'assegnazione di operatori MULTI-skill quando l'operazione potrebbe essere svolta da uno specializzato. Preserva il pool MULTI per emergenze o operazioni GENERAL."
                formula="cost[op,opr] = 1 if opr.skill == MULTI and op.type != GENERAL else 0\nmodel.Minimize(sum(assign[op,opr] * cost[op,opr]))"
                useCase="Officine dove gli operatori MULTI sono pochi e preziosi (team leader, capi officina multi-certificati). Li si vuole tenere liberi per situazioni non prevedibili."
                tradeoffs="Può allungare il makespan se gli specializzati sono saturi e il MULTI sarebbe stato disponibile in una finestra migliore."
              />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Constraints */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Layers className="h-5 w-5" />
            I vincoli del modello CP-SAT — stato attuale
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <ConstraintCard
            name="Parent-wait (Tipo A) — op padre aspetta figlio target"
            current
            formula={`# _add_parent_wait_constraints()
completion = model.NewIntVar(0, horizon, f"pw_completion_{idx}")
model.AddMaxEquality(completion, [op_end[t] for t in active_target])
model.Add(op_start[parent_op_id] >= completion)`}
            why="Per ogni op con reference_point_id, raccoglie ricorsivamente tutte le op schedulabili dell'ordine target (e dei suoi figli BOM) e le forza a completarsi prima che l'op padre inizi. È il vincolo che garantisce la sequenza bottom-up della BOM."
          />
          <ConstraintCard
            name="RP DAG (Tipo B) — precedenze intra-livello tra ordini fratelli"
            current
            formula={`# _add_rp_order_constraints()
completion = model.NewIntVar(0, horizon, f"rp_completion_{idx}")
model.AddMaxEquality(completion, [op_end[p] for p in active_pred])
for succ_op in active_succ:
    model.Add(op_start[succ_op] >= completion)`}
            why="Per ogni arco del DAG reference_point_precedences, impone che tutte le op del sottoalbero BOM del predecessore terminino prima che qualsiasi op del successore inizi. Implementa l'ordine di montaggio (es. struttura portante prima di idraulico ed elettrico)."
          />
          <ConstraintCard
            name="Assegnazione esattamente a 1 operatore"
            current
            formula="sum(assign[op, opr] for opr in qualified_operators[op]) == 1"
            why="Ogni operazione viene eseguita da un solo operatore. La modalità multi-operatore è disabilitata: richiederebbe AddDivisionEquality non-lineare che genera INFEASIBLE."
          />
          <ConstraintCard
            name="Durata fissa (residuale)"
            current
            formula="duration_const[op] = max(planned_min × (1 - progress/100), MIN_OP_DURATION)\ninterval[op] = NewIntervalVar(start[op], duration_const[op], end[op])"
            why="La durata non scala col numero di operatori. Semplificazione consapevole che rende il modello lineare e risolvibile in pochi secondi."
          />
          <ConstraintCard
            name="No-overlap per operatore"
            current
            formula="# Per ogni operatore: no overlap tra le sue operazioni assegnate\nmodel.AddNoOverlap([interval_optional[op,opr] for op in ops])"
            why="Un operatore non può lavorare su due operazioni in contemporanea. Gli interval_optional sono attivi solo quando assign[op,opr]=true."
          />
          <ConstraintCard
            name="Vincolo turni (RILASSATO — v1)"
            current={false}
            highlight
            formula="# ATTUALE: blocca solo operatori senza NESSUNO slot nell'orizzonte\n# FUTURO v2: decomposizione slot-task con sub-intervals"
            why="Il vincolo 'l'operazione deve stare dentro un turno' è temporaneamente rimosso. Con operazioni da 480min e turni da ~225min, AddNoOverlap(fixed+optional) generava INFEASIBLE in 0.2s. Da implementare con decomposizione slot-task."
          />
          <ConstraintCard
            name="Mancanti — blocco su arrivo componenti"
            current
            formula="# Se production_order ha missing_component non arrivato:\nmodel.Add(op_start[op] >= arrival_minute)"
            why="Le operazioni bloccate da componenti mancanti non possono iniziare prima della data attesa di arrivo. Calcolato in Step 4b del reschedule_engine."
          />
          <ConstraintCard
            name="Obiettivo — ottimizzazione (DISABILITATO)"
            current={false}
            formula="# _set_objective() è pass\n# Riabilitare dopo fix vincolo turni v2"
            why="Con stop_after_first_solution=True e nessun obiettivo, il solver trova la prima soluzione fattibile e si ferma. Questo garantisce velocità (1-5s) ma non ottimalità. Va riabilitato dopo aver stabilizzato il vincolo turni v2."
          />
        </CardContent>
      </Card>

      {/* Performance */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Clock className="h-5 w-5" />
            Performance e parametri solver
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>
            Con greedy hint attivo (<code className="font-mono bg-stone-100 px-1 rounded">_add_solution_hints</code>) il tempo per trovare
            una soluzione FEASIBLE su un machine_order completo (~250 operations) scende da ~30s a 1-5s.
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li><code className="font-mono bg-stone-100 px-1 rounded">num_search_workers = min(8, cpu_count)</code> — parallelismo portfolio</li>
            <li><code className="font-mono bg-stone-100 px-1 rounded">linearization_level = 1</code> — attiva LP relaxation</li>
            <li><code className="font-mono bg-stone-100 px-1 rounded">search_branching = 6</code> — PORTFOLIO_WITH_QUICK_RESTART</li>
            <li><code className="font-mono bg-stone-100 px-1 rounded">log_search_progress = True</code> — solo in dev, da disabilitare in produzione</li>
          </ul>
        </CardContent>
      </Card>

      {/* Infeasibility */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-600" />
            Cosa succede in caso di INFEASIBLE
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>
            <code className="font-mono bg-stone-100 px-1 rounded">infeasibility_analyzer.py</code> analizza la causa probabile e produce
            un messaggio in italiano basato su:
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>operazioni senza qualified operator (skill/workcenter mismatch)</li>
            <li>componenti mancanti con data attesa oltre l&apos;orizzonte del calendario</li>
            <li>durate totali per workcenter superiori alla capacità dell&apos;orizzonte</li>
            <li>vincoli RP DAG che creano catene impossibili (es. componente mancante su critical path di un predecessore)</li>
          </ul>
          <p>
            Il frontend riceve <code className="font-mono bg-stone-100 px-1 rounded">SCHEDULE_INFEASIBLE</code> via WebSocket
            con <code className="font-mono bg-stone-100 px-1 rounded">{`{ "conflicts": [...] }`}</code>.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// ============================================================================
// SUB-COMPONENTS
// ============================================================================

function PipelineStep({
  num,
  title,
  desc,
  highlight,
}: {
  num: number;
  title: string;
  desc: string;
  highlight?: boolean;
}): JSX.Element {
  return (
    <div
      className={`flex gap-3 p-3 rounded-md border ${
        highlight ? "bg-purple-50 border-purple-300" : "bg-stone-50 border-stone-200"
      }`}
    >
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-full font-mono text-xs flex items-center justify-center ${
          highlight ? "bg-purple-700 text-white" : "bg-stone-700 text-white"
        }`}
      >
        {num}
      </div>
      <div>
        <div className="font-semibold text-sm flex items-center gap-2">
          {title}
          {highlight && <Sparkles className="h-3.5 w-3.5 text-purple-600" />}
        </div>
        <div className="text-xs text-stone-700 leading-relaxed mt-0.5">{desc}</div>
      </div>
    </div>
  );
}

function ScenarioBlock({
  title,
  description,
  formula,
  useCase,
  tradeoffs,
  isCurrent,
}: {
  title: string;
  description: React.ReactNode;
  formula: string;
  useCase: string;
  tradeoffs: string;
  isCurrent?: boolean;
}): JSX.Element {
  return (
    <div className="space-y-3">
      <div>
        <div className="font-semibold text-base mb-1 flex items-center gap-2">
          {title}
          {isCurrent && <Badge className="text-[10px]">ATTIVO ORA</Badge>}
        </div>
        <div className="text-stone-700">{description}</div>
      </div>
      <div>
        <div className="text-xs font-semibold text-stone-600 mb-1">Formulazione CP-SAT</div>
        <pre className="bg-stone-900 text-stone-100 p-3 rounded text-xs font-mono overflow-x-auto whitespace-pre-wrap">
{formula}
        </pre>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="p-2 bg-blue-50 border border-blue-200 rounded">
          <div className="text-xs font-semibold text-blue-900 mb-1">Quando usarlo</div>
          <div className="text-xs text-blue-900">{useCase}</div>
        </div>
        <div className="p-2 bg-amber-50 border border-amber-200 rounded">
          <div className="text-xs font-semibold text-amber-900 mb-1">Trade-off</div>
          <div className="text-xs text-amber-900">{tradeoffs}</div>
        </div>
      </div>
    </div>
  );
}

function ConstraintCard({
  name,
  current,
  highlight,
  formula,
  why,
}: {
  name: string;
  current: boolean;
  highlight?: boolean;
  formula: string;
  why: string;
}): JSX.Element {
  return (
    <div
      className={`p-3 rounded-md border ${
        highlight ? "bg-amber-50 border-amber-200" : "bg-stone-50 border-stone-200"
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="font-semibold text-sm flex items-center gap-2">
          <Lock className={`h-3.5 w-3.5 ${current ? "text-green-700" : "text-stone-400"}`} />
          {name}
        </div>
        <Badge variant={current ? "default" : "outline"} className="text-[10px]">
          {current ? "ATTIVO" : "DISABILITATO"}
        </Badge>
      </div>
      <pre className="bg-stone-900 text-stone-100 p-2 rounded text-xs font-mono mb-2 overflow-x-auto whitespace-pre-wrap">
{formula}
      </pre>
      <div className="text-xs text-stone-700 leading-relaxed">{why}</div>
    </div>
  );
}