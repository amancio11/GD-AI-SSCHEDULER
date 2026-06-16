// frontend/src/pages/SchedulerLogic.tsx
//
// Pagina di documentazione tecnica sulla logica dello scheduler CP-SAT.
// v2 — Aggiornamenti:
//   - Tab obiettivi allineati all'enum ObjectiveMode del backend:
//     MANUAL (attuale) / FINISH_BY_DATE / MAXIMIZE_RESOURCE_UTILIZATION / MINIMIZE_OPERATORS / CUSTOM
//   - Rimossi tab inventati: MAKESPAN, DEADLINE, BALANCED, RESOURCE
//   - Nuova sezione "Cosa succede con i ritardi" che spiega il flusso completo

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
  Calendar,
  Zap,
  Users,
  Sliders,
  ArrowRight,
  CheckCircle,
  XCircle,
  RefreshCw,
} from "lucide-react";

// ============================================================================
// SUB-COMPONENTS
// ============================================================================

function PipelineStep({
  num,
  title,
  desc,
}: {
  num: number;
  title: string;
  desc: string;
}) {
  return (
    <div className="flex gap-3">
      <div className="w-6 h-6 rounded-full bg-primary text-primary-foreground text-xs font-bold flex items-center justify-center shrink-0 mt-0.5">
        {num}
      </div>
      <div>
        <p className="font-semibold text-sm">{title}</p>
        <p className="text-muted-foreground text-xs mt-0.5 leading-relaxed">{desc}</p>
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
  isCurrent = false,

}: {
  title: string;
  description: React.ReactNode;
  formula: string;
  useCase: string;
  tradeoffs: string;
  isCurrent?: boolean;

}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="font-semibold text-sm">{title}</h3>
        {isCurrent && (
          <Badge className="bg-emerald-600 text-white text-[10px]">
            ✓ Attivo ora
          </Badge>
        )}
      </div>
      <p className="text-muted-foreground text-xs leading-relaxed">{description}</p>
      <div>
        <p className="text-xs font-semibold mb-1 text-muted-foreground">Formula CP-SAT</p>
        <pre className="bg-stone-100 dark:bg-stone-900 rounded-lg p-3 text-xs font-mono overflow-x-auto leading-relaxed">
          {formula}
        </pre>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-blue-50 dark:bg-blue-950 rounded-lg p-3">
          <p className="text-xs font-semibold text-blue-700 dark:text-blue-300 mb-1">Caso d'uso</p>
          <p className="text-xs text-blue-600 dark:text-blue-400 leading-relaxed">{useCase}</p>
        </div>
        <div className="bg-amber-50 dark:bg-amber-950 rounded-lg p-3">
          <p className="text-xs font-semibold text-amber-700 dark:text-amber-300 mb-1">Trade-off</p>
          <p className="text-xs text-amber-600 dark:text-amber-400 leading-relaxed">{tradeoffs}</p>
        </div>
      </div>
    </div>
  );
}

function ConstraintCard({
  name,
  current,
  formula,
  why,
  highlight = false,
}: {
  name: string;
  current: boolean;
  formula: string;
  why: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border p-3 space-y-2 ${
        highlight
          ? "border-amber-400 bg-amber-50 dark:bg-amber-950"
          : current
          ? "border-emerald-300 bg-emerald-50 dark:bg-emerald-950"
          : "border-dashed border-stone-300 bg-stone-50 dark:bg-stone-900 opacity-70"
      }`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold">{name}</span>
        {current ? (
          <Badge className="bg-emerald-600 text-white text-[10px]">Attivo</Badge>
        ) : (
          <Badge variant="outline" className="text-[10px]">Disabilitato</Badge>
        )}
        {highlight && (
          <Badge className="bg-amber-500 text-white text-[10px]">⚠ Rilassato</Badge>
        )}
      </div>
      <pre className="bg-white dark:bg-stone-950 rounded p-2 text-[11px] font-mono overflow-x-auto leading-relaxed border">
        {formula}
      </pre>
      <p className="text-xs text-muted-foreground leading-relaxed">{why}</p>
    </div>
  );
}

// ── Delay Flow Box ────────────────────────────────────────────────────────────

function DelayStep({
  icon,
  title,
  body,
  color = "blue",
}: {
  icon: React.ReactNode;
  title: string;
  body: React.ReactNode;
  color?: "blue" | "orange" | "green" | "red" | "purple";
}) {
  const colors = {
    blue:   "border-blue-300 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300",
    orange: "border-orange-300 bg-orange-50 dark:bg-orange-950 text-orange-700 dark:text-orange-300",
    green:  "border-green-300 bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300",
    red:    "border-red-300 bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300",
    purple: "border-purple-300 bg-purple-50 dark:bg-purple-950 text-purple-700 dark:text-purple-300",
  };
  return (
    <div className={`rounded-lg border p-3 ${colors[color]}`}>
      <div className="flex items-center gap-2 mb-1">
        {icon}
        <span className="text-xs font-bold">{title}</span>
      </div>
      <div className="text-xs leading-relaxed opacity-90">{body}</div>
    </div>
  );
}

// ============================================================================
// MAIN
// ============================================================================

export default function SchedulerLogic(): JSX.Element {
  const [scenario, setScenario] = useState<string>("manual");

  return (
    <div className="space-y-4 max-w-5xl p-4">

      {/* ── Overview ── */}
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
            e a una finestra temporale, rispettando dipendenze, disponibilità e l'ordine
            gerarchico della BOM definito dal DAG dei Reference Point.
          </p>
          <p>
            Il problema è un <strong>Resource-Constrained Project Scheduling Problem (RCPSP)</strong>:
            ogni operazione è un task con durata residua, un singolo operatore richiesto,
            e vincoli di precedenza che derivano sia dalla gerarchia BOM (tramite RP DAG)
            sia dalle relazioni padre-figlio dirette.
          </p>
          <div className="flex gap-2 flex-wrap">
            {[
              "CP-SAT",
              "linearization_level=1",
              "search_branching=6 (PORTFOLIO)",
              "max_time=30s",
              "stop_after_first_solution=true",
            ].map((b) => (
              <Badge key={b} variant="outline" className="font-mono text-[11px]">
                {b}
              </Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* ── DAG ── */}
      <Card className="border-purple-300 bg-purple-50 dark:bg-purple-950">
        <CardHeader>
          <CardTitle className="text-base text-purple-900 dark:text-purple-100">
            Il DAG dei Reference Point — il cuore della logica di precedenza
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-purple-900 dark:text-purple-200 leading-relaxed">
          <p>
            Il DAG visualizzato nel <strong>DAG Viewer</strong> non è solo una visualizzazione:
            è la struttura dati da cui lo scheduler ricava <em>tutte</em> le precedenze tra ordini.
          </p>
          <p>
            Ogni arco <code className="font-mono bg-purple-100 dark:bg-purple-900 px-1 rounded">RP_pred → RP_succ</code> si
            traduce in un vincolo CP-SAT: tutte le operazioni dell'intero sottoalbero BOM
            del predecessore devono terminare prima che qualsiasi operazione del successore inizi.
          </p>
          <p>
            Es. <strong>MA-003 (Struttura Portante) → MA-001 (Idraulico)</strong>: il gruppo
            struttura deve essere completato al 100% prima che possa iniziare qualsiasi
            operazione del gruppo idraulico, a qualsiasi livello di profondità.
          </p>
        </CardContent>
      </Card>

      {/* ── Pipeline ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Layers className="h-5 w-5" />
            Pipeline di rischedulazione (reschedule_engine.py)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {[
            {
              num: 1,
              title: "Carica scenario e machine order",
              desc: "Recupera lo scenario attivo e il machine_order associato. Verifica che esista almeno un routing.",
            },
            {
              num: 2,
              title: "Identifica operazioni schedulabili",
              desc: "Filtra le operazioni: esclude COMPLETED e quelle senza routing. Le operazioni IN_PROGRESS vengono riprese da progress_pct (durata residua).",
            },
            {
              num: 3,
              title: "Vincoli componenti mancanti",
              desc: "Per ogni missing_component non ancora arrivato, imposta op_start[op] >= arrival_minute. Le operazioni del gruppo bloccato non possono iniziare prima dell'arrivo.",
            },
            {
              num: 4,
              title: "Operatori e slot disponibilità",
              desc: "Carica operator_calendar, costruisce gli slot disponibili per ogni operatore nell'orizzonte. Operatori senza nessuno slot vengono esclusi dal modello.",
            },
            {
              num: 5,
              title: "Costruisce vincoli DAG (rp_order_constraints)",
              desc: "Per ogni arco nel DAG dei RP: raccoglie ricorsivamente tutte le op schedulabili del predecessore e del successore, poi impone completion_pred <= start_succ via variabile ausiliaria AddMaxEquality.",
            },
            {
              num: 6,
              title: "Solve con greedy hint",
              desc: "Imposta solution_hints da una soluzione greedy (assegna op all'operatore con load minimo, ASAP), poi cp_solver.Solve(model). Stop al primo FEASIBLE. Con hint: 1–5s invece di ~30s.",
            },
            {
              num: 7,
              title: "Estrazione e scrittura DB",
              desc: "solution_extractor traduce assign_var attivi e start/end in schedule_entries (DELETE + INSERT sullo scenario). Notifica WebSocket RESCHEDULE_COMPLETE.",
            },
          ].map((s) => (
            <PipelineStep key={s.num} {...s} />
          ))}
        </CardContent>
      </Card>

      {/* ── NUOVA SEZIONE: Cosa succede con i ritardi ── */}
      <Card className="border-orange-300">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2 text-orange-700 dark:text-orange-300">
            <AlertTriangle className="h-5 w-5" />
            Cosa succede quando arrivano ritardi o completamenti fuori schedule?
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5 text-sm">

          <p className="text-muted-foreground leading-relaxed">
            Il sistema è progettato per il <strong>reschedule incrementale</strong>: non ricalcola
            tutto da zero, ma prende come fisso ciò che è già completato e ripianifica solo
            le operazioni ancora aperte. Questo è il flusso completo per ogni tipo di evento.
          </p>

          {/* Scenario A — completamento in ritardo */}
          <div>
            <h3 className="text-sm font-bold mb-3 flex items-center gap-2">
              <Clock size={15} className="text-orange-500" />
              Scenario A — Un'operazione finisce in ritardo
            </h3>
            <div className="flex flex-col gap-2">
              <DelayStep
                icon={<XCircle size={13} />}
                title="Evento: actual_end > scheduled_end"
                color="orange"
                body={
                  <>
                    Un operatore aggiorna l'operazione come COMPLETED ma con{" "}
                    <code className="font-mono bg-orange-100 dark:bg-orange-900 px-1 rounded">
                      actual_end = scheduled_end + Δt
                    </code>
                    . Lo status della schedule entry passa a <strong>DELAYED</strong>.
                  </>
                }
              />
              <div className="flex justify-center">
                <ArrowRight size={16} className="text-muted-foreground" />
              </div>
              <DelayStep
                icon={<RefreshCw size={13} />}
                title="Trigger: reschedule incrementale"
                color="blue"
                body={
                  <>
                    Il reschedule_engine viene avviato (manualmente o via webhook). Legge{" "}
                    <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">
                      actual_end
                    </code>{" "}
                    dell'operazione completata e la tratta come vincolo fisso:{" "}
                    <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">
                      horizon_start = max(actual_end di tutte le op COMPLETED)
                    </code>
                    .
                  </>
                }
              />
              <div className="flex justify-center">
                <ArrowRight size={16} className="text-muted-foreground" />
              </div>
              <DelayStep
                icon={<Layers size={13} />}
                title="CP-SAT: propagazione attraverso il DAG"
                color="purple"
                body={
                  <>
                    Il vincolo{" "}
                    <code className="font-mono bg-purple-100 dark:bg-purple-900 px-1 rounded">
                      rp_order_constraints
                    </code>{" "}
                    impone che i successori nel DAG non possano iniziare prima del completion
                    del predecessore. Se l'operazione ritardata è sul critical path, <strong>tutti
                    i successori vengono shiftati in avanti</strong> di almeno Δt minuti.
                    Se c'è slack nel predecessore, i successori potrebbero non essere impattati.
                  </>
                }
              />
              <div className="flex justify-center">
                <ArrowRight size={16} className="text-muted-foreground" />
              </div>
              <DelayStep
                icon={<CheckCircle size={13} />}
                title="Risultato: nuovo piano con date aggiornate"
                color="green"
                body="Le schedule_entries dei successori vengono riscritte con le nuove date. Il Gantt si aggiorna via WebSocket (RESCHEDULE_COMPLETE). Le entry vecchie diventano STALE e vengono sostituite."
              />
            </div>
          </div>

          {/* Scenario B — operazione interrotta */}
          <div>
            <h3 className="text-sm font-bold mb-3 flex items-center gap-2">
              <AlertTriangle size={15} className="text-amber-500" />
              Scenario B — Un'operazione viene interrotta (progress_pct &lt; 100%)
            </h3>
            <div className="flex flex-col gap-2">
              <DelayStep
                icon={<XCircle size={13} />}
                title="Evento: status → INTERRUPTED, progress_pct = N%"
                color="orange"
                body={
                  <>
                    L'operazione viene marcata INTERRUPTED con un{" "}
                    <code className="font-mono bg-orange-100 dark:bg-orange-900 px-1 rounded">
                      progress_pct
                    </code>{" "}
                    e un{" "}
                    <code className="font-mono bg-orange-100 dark:bg-orange-900 px-1 rounded">
                      interruption_reason
                    </code>
                    . Non è né completata né pianificata: è in uno stato sospeso.
                  </>
                }
              />
              <div className="flex justify-center">
                <ArrowRight size={16} className="text-muted-foreground" />
              </div>
              <DelayStep
                icon={<RefreshCw size={13} />}
                title="Reschedule: durata residua calcolata da progress_pct"
                color="blue"
                body={
                  <>
                    Il solver calcola la durata residua come{" "}
                    <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">
                      duration_residua = planned_duration × (1 - progress_pct / 100)
                    </code>
                    . L'operazione riprende come se fosse nuova, con questa durata ridotta.
                    Può essere riassegnata allo stesso operatore o a uno diverso.
                  </>
                }
              />
              <div className="flex justify-center">
                <ArrowRight size={16} className="text-muted-foreground" />
              </div>
              <DelayStep
                icon={<CheckCircle size={13} />}
                title="Risultato: l'operazione riparte, i successori aspettano"
                color="green"
                body="Il piano mostra la ripresa dell'operazione con la durata residua, e i successori nel DAG vengono schedulati rispettando il completamento atteso della ripresa."
              />
            </div>
          </div>

          {/* Scenario C — componente mancante arriva */}
          <div>
            <h3 className="text-sm font-bold mb-3 flex items-center gap-2">
              <CheckCircle size={15} className="text-green-500" />
              Scenario C — Un componente mancante arriva prima del previsto
            </h3>
            <div className="flex flex-col gap-2">
              <DelayStep
                icon={<CheckCircle size={13} />}
                title="Evento: mark-arrived su missing_component"
                color="green"
                body={
                  <>
                    L'utente fa toggle su "Arrivato" → il backend aggiorna{" "}
                    <code className="font-mono bg-green-100 dark:bg-green-900 px-1 rounded">
                      is_arrived = true
                    </code>{" "}
                    e rimuove il vincolo{" "}
                    <code className="font-mono bg-green-100 dark:bg-green-900 px-1 rounded">
                      op_start &gt;= arrival_minute
                    </code>{" "}
                    per quelle operazioni.
                  </>
                }
              />
              <div className="flex justify-center">
                <ArrowRight size={16} className="text-muted-foreground" />
              </div>
              <DelayStep
                icon={<RefreshCw size={13} />}
                title="Reschedule: le operazioni bloccate si sbloccano"
                color="blue"
                body="Il solver può ora schedulare le operazioni che erano bloccate dall'arrivo del componente. Se c'è capacità operatori disponibile, possono partire immediatamente (hora corrente), anticipando il piano originale."
              />
              <div className="flex justify-center">
                <ArrowRight size={16} className="text-muted-foreground" />
              </div>
              <DelayStep
                icon={<Zap size={13} />}
                title="Effetto: anticipo sul makespan"
                color="green"
                body="Se il componente arriva in anticipo rispetto alla data attesa, le operazioni che dipendevano da esso vengono anticipate. Il makespan totale della macchina può ridursi."
              />
            </div>
          </div>

          {/* Box riepilogativo */}
          <div className="rounded-lg border border-stone-200 bg-stone-50 dark:bg-stone-900 p-4 space-y-2">
            <p className="text-xs font-bold text-stone-700 dark:text-stone-300">
              Regola generale: cosa impatta il critical path e cosa no
            </p>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <p className="font-semibold text-red-600 mb-1">Impatta il makespan finale ↑</p>
                <ul className="space-y-1 text-muted-foreground">
                  <li>• Operazione sul <strong>critical path</strong> finisce in ritardo</li>
                  <li>• Componente mancante su operazione critical path</li>
                  <li>• Operatore assente e nessun sostituto disponibile</li>
                  <li>• Operazione interrotta senza ripresa pianificata</li>
                </ul>
              </div>
              <div>
                <p className="font-semibold text-green-600 mb-1">NON impatta il makespan finale</p>
                <ul className="space-y-1 text-muted-foreground">
                  <li>• Ritardo su operazione con <strong>slack sufficiente</strong></li>
                  <li>• Ritardo assorbito da operazioni parallele (routing SIMULTANEOUS)</li>
                  <li>• Componente mancante su percorso non critico</li>
                  <li>• Ritardo compensato da anticipo di un altro predecessore</li>
                </ul>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Obiettivi per scenario — AGGIORNATI ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Target className="h-5 w-5" />
            Modalità di obiettivo per scenario
          </CardTitle>
        </CardHeader>
        <CardContent>
          {/* ── FIX: valori allineati all'enum ObjectiveMode del backend ── */}
          <Tabs value={scenario} onValueChange={setScenario}>
            <TabsList className="mb-4 flex-wrap h-auto gap-1">
              <TabsTrigger value="manual" className="text-xs">
                Attuale (soddisfacibilità)
              </TabsTrigger>
              <TabsTrigger value="FINISH_BY_DATE" className="text-xs">
                FINISH_BY_DATE
              </TabsTrigger>
              <TabsTrigger value="MAXIMIZE_RESOURCE_UTILIZATION" className="text-xs">
                MAXIMIZE_RESOURCE_UTILIZATION
              </TabsTrigger>
              <TabsTrigger value="MINIMIZE_OPERATORS" className="text-xs">
                MINIMIZE_OPERATORS
              </TabsTrigger>
              <TabsTrigger value="CUSTOM" className="text-xs">
                CUSTOM
              </TabsTrigger>
            </TabsList>

            {/* ── Tab 1: modalità attuale ── */}
            <TabsContent value="manual" className="space-y-3 text-sm">
              <ScenarioBlock
                title="Soddisfacibilità pura — modalità attuale in produzione"
                isCurrent
                description={
                  <>
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      _set_objective
                    </code>{" "}
                    è{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      pass
                    </code>
                    : il solver cerca qualsiasi assegnazione che soddisfi i vincoli, senza
                    ottimizzare alcuna metrica. Si ferma alla prima soluzione FEASIBLE.
                    Tutti i vincoli di precedenza DAG sono comunque attivi e rispettati.
                    Corrisponde a creare uno scenario senza specificare un{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      objective_mode
                    </code>{" "}
                    esplicito.
                  </>
                }
                formula={
                  "# nessun model.Minimize / Maximize\n" +
                  "# vincoli RP DAG sempre attivi\n" +
                  "stop_after_first_solution = True\n" +
                  "# → trova il primo piano fattibile e si ferma"
                }
                useCase="Sviluppo, debugging, verifica di fattibilità prima di ottimizzare. Velocissimo (1–5s con greedy hint)."
                tradeoffs="Nessuna garanzia di ottimalità. Due run consecutivi sullo stesso scenario possono dare piani diversi perché PORTFOLIO esplora strategie multiple in parallelo."
              />
            </TabsContent>

            {/* ── Tab 2: FINISH_BY_DATE ── */}
            <TabsContent value="FINISH_BY_DATE" className="space-y-3 text-sm">
              <ScenarioBlock
                title="FINISH_BY_DATE — rispetta una data di consegna contrattuale"
                isCurrent
                description={
                  <>
                    Introduce una variabile{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      tardiness
                    </code>{" "}
                    per le operazioni finali (ordine MACHINE) e minimizza la somma pesata
                    delle tardiness rispetto alla{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      target_finish_date
                    </code>{" "}
                    impostata sullo scenario. Se la data è raggiungibile, il solver troverà
                    il piano più compatto per rispettarla. Se non è raggiungibile, restituisce
                    INFEASIBLE con spiegazione dal{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      infeasibility_analyzer
                    </code>
                    .
                  </>
                }
                formula={
                  "due_date_min = datetime_to_minutes(scenario.target_finish_date)\n" +
                  "tardiness = model.NewIntVar(0, horizon, 'tardiness')\n" +
                  "model.AddMaxEquality(tardiness,\n" +
                  "    [end_var[op] - due_date_min for op in machine_ops] + [0])\n" +
                  "model.Minimize(tardiness)"
                }
                useCase="Ordine con data di spedizione ferma al cliente. Il solver concentra le risorse per stare dentro la deadline rispettando la BOM."
                tradeoffs="Se la deadline è fattibile, il piano può lasciare slack a fine progetto. Se la sequenza BOM richiede più tempo della deadline → INFEASIBLE."
              />
            </TabsContent>

            {/* ── Tab 3: MAXIMIZE_RESOURCE_UTILIZATION ── */}
            <TabsContent value="MAXIMIZE_RESOURCE_UTILIZATION" className="space-y-3 text-sm">
              <ScenarioBlock
                title="MAXIMIZE_RESOURCE_UTILIZATION — minimizza i tempi morti"
                isCurrent
                description={
                  <>
                    Minimizza la somma dei gap temporali tra operazioni assegnate allo stesso
                    operatore. Equivale a massimizzare il{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      load_pct
                    </code>{" "}
                    degli operatori nell'orizzonte temporale del piano. Tutti i vincoli DAG
                    rimangono attivi: il solver cerca il piano più denso che rispetti
                    comunque la sequenza BOM.
                  </>
                }
                formula={
                  "# Per ogni operatore: calcola idle time\n" +
                  "idle[opr] = makespan - sum(duration[op] * assign[op,opr] for op in ops)\n" +
                  "# Minimizza il totale di tempi morti\n" +
                  "model.Minimize(sum(idle[opr] for opr in operators))"
                }
                useCase="Piano settimanale quando si vuole massimizzare la produttività dell'impianto. Utile per capire se c'è capacità in eccesso o se siamo al limite."
                tradeoffs="Tende a concentrare le operazioni nelle prime finestre disponibili. Può generare piani con sovraccarico su alcuni operatori e sottoutilizzo di altri."
              />
            </TabsContent>

            {/* ── Tab 4: MINIMIZE_OPERATORS ── */}
            <TabsContent value="MINIMIZE_OPERATORS" className="space-y-3 text-sm">
              <ScenarioBlock
                title="MINIMIZE_OPERATORS — usa il minor numero di operatori distinti"
                isCurrent
                description={
                  <>
                    Introduce variabili booleane{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      used[opr]
                    </code>{" "}
                    per ogni operatore e minimizza il conteggio di quelli effettivamente
                    usati nel piano. Utile per costruire piani con team ridotti, o per
                    capire il numero minimo di operatori necessari per completare la macchina.
                  </>
                }
                formula={
                  "used[opr] = model.NewBoolVar(f'used_{opr}')\n" +
                  "# used[opr] = 1 se almeno un'op è assegnata a opr\n" +
                  "for op in ops:\n" +
                  "    model.Add(sum(assign[op,opr2] for opr2 in q[op]) >= used[opr])\n" +
                  "        .OnlyEnforceIf(assign[op,opr])\n" +
                  "model.Minimize(sum(used[opr] for opr in operators))"
                }
                useCase="Pianificazione con risorse limitate (es. ferie estive, cantiere esterno). Risponde a 'quante persone ci vogliono al minimo per completare questa macchina?'"
                tradeoffs="Può allungare il makespan perché concentra più lavoro su meno operatori, creando code. Da usare quando il costo-operatore è il vincolo principale."
              />
            </TabsContent>

            {/* ── Tab 5: CUSTOM ── */}
            <TabsContent value="CUSTOM" className="space-y-3 text-sm">
              <ScenarioBlock
                title="CUSTOM — combinazione pesata dei tre obiettivi"
                isCurrent
                description={
                  <>
                    Permette di combinare i tre obiettivi con pesi personalizzati passati
                    in{" "}
                    <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">
                      resource_set_json.weights
                    </code>
                    . I pesi vengono normalizzati a 1.0 totale. Richiede che gli obiettivi
                    siano espressi nella stessa unità (minuti) per essere sommabili.
                  </>
                }
                formula={
                  "weights = scenario.resource_set_json.get('weights', {})\n" +
                  "w_date   = weights.get('finish_by_date', 0.0)\n" +
                  "w_util   = weights.get('resource_utilization', 0.0)\n" +
                  "w_ops    = weights.get('minimize_operators', 0.0)\n\n" +
                  "# Obiettivo composito (tutto in minuti o unità normalizzate)\n" +
                  "model.Minimize(\n" +
                  "    w_date * tardiness\n" +
                  "    + w_util * total_idle\n" +
                  "    + w_ops  * operators_count * 10_000  # scala comparabile\n" +
                  ")"
                }
                useCase="Pianificatori esperti che vogliono bilanciare esplicitamente velocità, efficienza e numero di risorse. Es: 60% rispetta data, 30% massimizza utilizzo, 10% minimizza operatori."
                tradeoffs="La calibrazione dei pesi richiede esperienza. Pesi sbilanciati possono far degenerare l'ottimizzazione verso uno solo dei tre obiettivi."
              />
            </TabsContent>
          </Tabs>

          <div className="mt-4 p-3 rounded-lg border border-blue-300 bg-blue-50 dark:bg-blue-950 text-xs text-blue-700 dark:text-blue-300">
            <strong>Stato implementazione:</strong> tutti e 4 gli obiettivi sono implementati in{" "}
            <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">
              cpsat_model_builder._set_objective()
            </code>
            . Quando lo scenario ha un obiettivo attivo,{" "}
            <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">
              stop_after_first_solution
            </code>{" "}
            viene impostato a{" "}
            <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">
              False
            </code>{" "}
            automaticamente così il solver ottimizza davvero invece di fermarsi alla prima
            soluzione fattibile. Solo la modalità soddisfacibilità pura usa{" "}
            <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">
              stop_after_first_solution = True
            </code>
            .
          </div>
        </CardContent>
      </Card>

      {/* ── Vincoli CP-SAT ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Layers className="h-5 w-5" />
            I vincoli del modello CP-SAT — stato attuale
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <ConstraintCard
            name="Parent-wait (Tipo A) — op padre aspetta il completamento del figlio target"
            current
            formula={
              "# _add_parent_wait_constraints()\n" +
              "completion = model.NewIntVar(0, horizon, f'pw_completion_{idx}')\n" +
              "model.AddMaxEquality(completion, [op_end[t] for t in active_target])\n" +
              "model.Add(op_start[parent_op_id] >= completion)"
            }
            why="Per ogni op con reference_point_id, raccoglie ricorsivamente tutte le op schedulabili dell'ordine target (e dei suoi figli BOM) e le forza a completarsi prima che l'op padre inizi. Garantisce la sequenza bottom-up della BOM."
          />
          <ConstraintCard
            name="RP DAG (Tipo B) — precedenze intra-livello tra ordini fratelli"
            current
            formula={
              "# _add_rp_order_constraints()\n" +
              "completion = model.NewIntVar(0, horizon, f'rp_completion_{idx}')\n" +
              "model.AddMaxEquality(completion, [op_end[p] for p in active_pred])\n" +
              "for succ_op in active_succ:\n" +
              "    model.Add(op_start[succ_op] >= completion)"
            }
            why="Per ogni arco del DAG reference_point_precedences, impone che tutte le op del sottoalbero BOM del predecessore terminino prima che qualsiasi op del successore inizi. Implementa l'ordine di montaggio (es. struttura portante → idraulico → elettrico)."
          />
          <ConstraintCard
            name="Assegnazione esattamente a 1 operatore"
            current
            formula="sum(assign[op, opr] for opr in qualified_operators[op]) == 1"
            why="Ogni operazione viene eseguita da un solo operatore. La modalità multi-operatore è disabilitata: richiederebbe AddDivisionEquality non-lineare che genera INFEASIBLE con OR-Tools."
          />
          <ConstraintCard
            name="Durata fissa (residuale da progress_pct)"
            current
            formula={
              "duration_const[op] = max(\n" +
              "    planned_min × (1 - progress_pct / 100),\n" +
              "    MIN_OP_DURATION\n" +
              ")\n" +
              "interval[op] = NewIntervalVar(start[op], duration_const[op], end[op])"
            }
            why="La durata non scala col numero di operatori. Semplificazione consapevole che rende il modello lineare e risolvibile in pochi secondi. Le operazioni interrotte riprendono con la durata residua calcolata da progress_pct."
          />
          <ConstraintCard
            name="No-overlap per operatore"
            current
            formula={
              "# Per ogni operatore: no overlap tra le sue operazioni assegnate\n" +
              "model.AddNoOverlap(\n" +
              "    [interval_optional[op,opr] for op in ops]\n" +
              ")  # interval_optional attivo solo quando assign[op,opr]=true"
            }
            why="Un operatore non può lavorare su due operazioni in contemporanea. Gli interval_optional sono attivati solo quando l'operazione è assegnata a quell'operatore."
          />
          <ConstraintCard
            name="Componenti mancanti — blocco su data arrivo"
            current
            formula={
              "# Se production_order ha missing_component non ancora arrivato:\n" +
              "model.Add(op_start[op] >= arrival_minute)\n" +
              "# arrival_minute = datetime_to_minutes(expected_arrival_date)"
            }
            why="Le operazioni bloccate da componenti mancanti non possono iniziare prima della data attesa di arrivo. Quando il componente arriva (mark-arrived), il vincolo viene rimosso al prossimo reschedule."
          />
          <ConstraintCard
            name="Vincolo turni (RILASSATO — v1)"
            current={false}
            highlight
            formula={
              "# ATTUALE: blocca solo operatori senza NESSUNO slot nell'orizzonte\n" +
              "# FUTURO v2: decomposizione slot-task con sub-intervals\n" +
              "#\n" +
              "# Il problema: op da 480min non entra in nessun turno (~225min)\n" +
              "# → AddNoOverlap(fixed_intervals + optional) → INFEASIBLE in 0.2s\n" +
              "# Fix v2: spezzare ogni op in sub-tasks che stanno dentro i turni"
            }
            why="Il vincolo 'l'operazione deve stare dentro un turno' è temporaneamente rimosso. Verrà implementato con decomposizione slot-task nella v2 dello scheduler."
          />
          <ConstraintCard
            name="Obiettivo — ottimizzazione (DISABILITATO)"
            current={false}
            formula={
              "# _set_objective() è 'pass'\n" +
              "# Riabilitare dopo fix vincolo turni v2\n" +
              "# → attualmente: soddisfacibilità pura, stop al primo FEASIBLE"
            }
            why="Con stop_after_first_solution=True e nessun obiettivo, il solver trova la prima soluzione fattibile e si ferma. Garantisce velocità (1–5s) ma non ottimalità."
          />
        </CardContent>
      </Card>

      {/* ── Performance ── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Clock className="h-5 w-5" />
            Performance e parametri solver
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            Con greedy hint attivo (
            <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded text-xs">
              _add_solution_hints
            </code>
            ) il tempo per trovare una soluzione FEASIBLE su un machine_order completo
            (~250 operazioni) scende da ~30s a <strong className="text-foreground">1–5s</strong>.
          </p>
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: "max_time_in_seconds", value: "30" },
              { label: "num_search_workers", value: "min(8, cpu_count)" },
              { label: "stop_after_first_solution", value: "True" },
              { label: "linearization_level", value: "1" },
              { label: "search_branching", value: "6 (PORTFOLIO_WITH_QUICK_RESTART)" },
              { label: "log_search_progress", value: "True (solo dev)" },
            ].map((p) => (
              <div key={p.label} className="flex justify-between bg-stone-50 dark:bg-stone-900 rounded px-3 py-2 text-xs">
                <code className="font-mono text-stone-600 dark:text-stone-400">{p.label}</code>
                <code className="font-mono font-bold text-foreground">{p.value}</code>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

    </div>
  );
}