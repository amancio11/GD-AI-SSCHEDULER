// frontend/src/pages/SchedulerLogic.tsx
//
// Documentazione tecnica della logica dello scheduler.
// AGGIORNATO 2026-06-26: pivot da CP-SAT (modello a segmenti per-operatore) a
// scheduling greedy per CAPACITÀ DI GRUPPO (resource_type). Vedi capacity_scheduler.py.

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Cpu,
  Clock,
  Layers,
  Calendar,
  Users,
  GitBranch,
  Settings,
  Zap,
  Split,
} from "lucide-react";

function PipelineStep({ num, title, desc }: { num: number; title: string; desc: string }) {
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

export default function SchedulerLogic(): JSX.Element {
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
            Lo scheduler lavora sul modello a <strong>capacità di gruppo</strong> in <strong>due stadi</strong>:
          </p>
          <ol className="text-xs space-y-1 list-decimal ml-5">
            <li>
              <strong>Greedy</strong> (<code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">capacity_scheduler.py</code>):
              list-scheduling earliest-fit, sempre fattibile in millisecondi → fornisce l'<strong>orizzonte
              stretto</strong> e il <strong>warm-start</strong>.
            </li>
            <li>
              <strong>CP-SAT cumulativo</strong> (<code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">capacity_cpsat.py</code>):
              <strong> ottimizza il makespan</strong> (<code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">AddCumulative</code> per
              gruppo, capacità = <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">count</code>). Parte dall'hint del
              greedy; se va in timeout → <strong>fallback</strong> al greedy.
            </li>
          </ol>
          <p>
            Le <strong>risorse non sono individui con nome</strong>, ma <strong>tipi configurabili</strong>:{" "}
            <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">(workcenter, skill, ore/giorno, count)</code>.
            La capacità di un gruppo è <code className="font-mono bg-stone-100 dark:bg-stone-800 px-1 rounded">count × ore/giorno</code>:
            due risorse da 8h nello stesso gruppo = <strong>16h/giorno</strong>.
          </p>
          <div className="flex gap-2 flex-wrap">
            {[
              "capacità di gruppo (RCPSP)",
              "greedy warm-start",
              "CP-SAT AddCumulative",
              "ottimo + sempre fattibile",
              "fallback garantito",
            ].map((b) => (
              <Badge key={b} variant="outline" className="font-mono text-[11px]">{b}</Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* ── Perché il cambio ── */}
      <Card className="border-amber-300 bg-amber-50 dark:bg-amber-950">
        <CardHeader>
          <CardTitle className="text-base text-amber-900 dark:text-amber-100 flex items-center gap-2">
            <Zap className="h-4 w-4" />
            Perché CP-SAT ora funziona (prima no)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-amber-900 dark:text-amber-200 leading-relaxed">
          <p className="text-xs">
            Il modello precedente decomponeva ogni operazione in un <strong>segmento opzionale per ogni
            coppia (operatore × slot)</strong> con due famiglie di <code className="font-mono bg-amber-100 dark:bg-amber-900 px-1 rounded">NoOverlap</code>:
            su 241 operazioni → 2000+ segmenti, il solver <strong>non trovava nemmeno una soluzione in 60s</strong> (UNKNOWN).
          </p>
          <p className="text-xs">
            Due i killer: <strong>(1)</strong> simmetria degli operatori "con nome" identici (migliaia di
            orbite); <strong>(2)</strong> il <code className="font-mono bg-amber-100 dark:bg-amber-900 px-1 rounded">NoOverlap</code> per-operatore su
            migliaia di intervalli. Il modello a <strong>capacità di gruppo li elimina entrambi</strong>:
            niente individui (niente simmetria) e la contesa risorse diventa un singolo{" "}
            <code className="font-mono bg-amber-100 dark:bg-amber-900 px-1 rounded">AddCumulative</code> per gruppo (≤ <code className="font-mono bg-amber-100 dark:bg-amber-900 px-1 rounded">count</code> in parallelo).
            È un RCPSP cumulativo classico → CP-SAT lo risolve all'ottimo, in fretta.
          </p>
        </CardContent>
      </Card>

      {/* ── Modello risorse a capacità ── */}
      <Card className="border-blue-300 bg-blue-50 dark:bg-blue-950">
        <CardHeader>
          <CardTitle className="text-base text-blue-900 dark:text-blue-100 flex items-center gap-2">
            <Users className="h-4 w-4" />
            Risorse a capacità di gruppo (resource_type)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-blue-900 dark:text-blue-200 leading-relaxed">
          <div className="space-y-2">
            <h3 className="font-semibold flex items-center gap-2"><Settings size={14} /> Configurazione</h3>
            <p className="text-xs">
              Un <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">resource_type</code> definisce
              un gruppo di risorse intercambiabili: <strong>workcenter</strong>, <strong>skill/certificazione</strong>,
              <strong> ore di capacità giornaliera</strong> (di una singola risorsa) e <strong>count</strong>{" "}
              (quante risorse). Si gestisce dalle API <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">/api/resource-types</code>.
            </p>
            <pre className="bg-white dark:bg-blue-950/50 rounded p-2 text-[11px] font-mono border border-blue-200 dark:border-blue-800 leading-relaxed overflow-x-auto">
{`ResourceType(workcenter=WC-MILANO, skill=MECHANICAL,
             daily_capacity_hours=8, count=3)
   →  gruppo = 3 corsie × 8h = 24h/giorno di capacità MECHANICAL a Milano`}
            </pre>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold flex items-center gap-2"><Users size={14} /> Matching skill ↔ tipo operazione</h3>
            <p className="text-xs">
              Un gruppo può eseguire un'operazione se il suo workcenter coincide e la skill è
              compatibile col tipo (<code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">_SKILL_CAN_DO</code>).
            </p>
            <pre className="bg-white dark:bg-blue-950/50 rounded p-2 text-[11px] font-mono border border-blue-200 dark:border-blue-800 leading-relaxed">
{`ELECTRICAL → {ELECTRICAL, GENERAL}
MECHANICAL → {MECHANICAL, GENERAL}
MULTI      → {ELECTRICAL, MECHANICAL, GENERAL}`}
            </pre>
            <p className="text-xs">
              Op senza alcun gruppo compatibile → riportata in <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">conflicts</code>{" "}
              (status INFEASIBLE), non viene mai ignorata.
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold flex items-center gap-2"><Calendar size={14} /> Disponibilità per giorno</h3>
            <p className="text-xs">
              Ogni tipo risorsa ha una <strong>disponibilità per giorno della settimana</strong>
              (<code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">weekday_schedule</code>):
              numero di risorse (<em>count</em>) e <em>ore</em> per ciascun giorno — es. lun 2 risorse/8h,
              mar–ven 3/8h, sab/dom 0. Si configura dalla pagina <strong>Risorse</strong>. Orario d'inizio
              comune <strong>08:00</strong>. L'orizzonte è solo un limite anti-loop, <strong>slegato</strong>
              dalla lunghezza del calendario.
            </p>
          </div>

          <div className="space-y-2">
            <h3 className="font-semibold flex items-center gap-2"><Clock size={14} /> Durata residua (IN_PROGRESS)</h3>
            <p className="text-xs">
              Le operazioni IN_PROGRESS rientrano con durata ridotta{" "}
              <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">residua = planned × (1 − progress/100)</code>{" "}
              e <code className="font-mono bg-blue-100 dark:bg-blue-900 px-1 rounded">earliest_start = now</code> (non riposizionate nel passato).
            </p>
          </div>
        </CardContent>
      </Card>

      {/* ── Algoritmo greedy ── */}
      <Card className="border-emerald-300 bg-emerald-50 dark:bg-emerald-950">
        <CardHeader>
          <CardTitle className="text-base text-emerald-900 dark:text-emerald-100 flex items-center gap-2">
            <Layers className="h-4 w-4" />
            Algoritmo — greedy list-scheduling (capacity_scheduler.py)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-emerald-900 dark:text-emerald-200 leading-relaxed">
          <p className="text-xs">
            Le operazioni si processano in <strong>ordine topologico</strong> costruito
            esclusivamente sui <strong>vincoli BOM hard</strong> (<code className="font-mono bg-emerald-100 dark:bg-emerald-800 px-1 rounded">parent_wait_constraints</code>).
            La ready queue viene ordinata per <code className="font-mono bg-emerald-100 dark:bg-emerald-800 px-1 rounded">(earliest_start, op_priority)</code>: le
            op ad alta priorità (RP level basso, seq basso) vengono dispatchate per prime, ma con
            più risorse libere partono <strong>tutte in parallelo</strong>. Ogni op viene piazzata{" "}
            <strong>earliest-fit</strong> nella prima corsia disponibile.
          </p>
          <pre className="bg-emerald-100 dark:bg-emerald-900 rounded p-2 text-[10px] font-mono leading-relaxed overflow-x-auto">
{`# topo_order usa SOLO parent_wait_constraints come hard predecessori
for op in topo_order(parent_wait_constraints):
    est = max(earliest_start, max(fine predecessori BOM), arrivo componenti)
    remaining = durata_residua
    while remaining > 0:
        (start, gruppo, corsia) = earliest_disponibile(groups, cursor=est)
        m = min(remaining, fine_finestra_giorno - start)
        emetti blocco [start, start+m] su (gruppo, corsia)
        cursor = start + m   # una risorsa alla volta
        remaining -= m
    completion[op] = fine ultimo blocco

# Ready queue: (earliest_start, rp_level×10000 + seq_number) → dispatch priority`}
          </pre>
        </CardContent>
      </Card>

      {/* ── Split / hand-off ── */}
      <Card className="border-emerald-300 bg-emerald-50 dark:bg-emerald-950">
        <CardHeader>
          <CardTitle className="text-base text-emerald-900 dark:text-emerald-100 flex items-center gap-2">
            <Split className="h-4 w-4" />
            Split su più giorni e hand-off tra risorse
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-emerald-900 dark:text-emerald-200 leading-relaxed">
          <p className="text-xs">
            Un'operazione è lavorata da <strong>una risorsa alla volta</strong>: al più la capacità di
            una singola risorsa al giorno (es. 8h). Se dura di più, le ore residue continuano nella
            finestra successiva — <strong>stessa corsia il giorno dopo</strong> o <strong>un'altra
            corsia</strong> (hand-off). Mai due risorse sulla stessa op nello stesso istante.
          </p>
          <pre className="bg-emerald-100 dark:bg-emerald-900 rounded p-2 text-[10px] font-mono leading-relaxed overflow-x-auto">
{`Gruppo MECHANICAL = 2 corsie × 8h/giorno
Op da 10h (600 min):
   lun 08:00–16:00  corsia 0   (480 min)   ← 8h primo giorno
   mar 08:00–10:00  corsia 0   (120 min)   ← 2h residue il giorno dopo
Op da 5h in parallelo:
   lun 08:00–13:00  corsia 1   (300 min)   ← seconda risorsa, stesso giorno`}
          </pre>
          <p className="text-[10px] opacity-75">
            Ogni blocco diventa una <strong>schedule_entry</strong> (con <code className="font-mono bg-emerald-100 dark:bg-emerald-800 px-1 rounded">resource_type_id</code>,
            <code className="font-mono bg-emerald-100 dark:bg-emerald-800 px-1 rounded">operator_id = null</code>): un'op spezzata appare sul Gantt come più tratti dello stesso colore.
          </p>
        </CardContent>
      </Card>

      {/* ── Vincoli hard ── */}
      <Card className="border-purple-300 bg-purple-50 dark:bg-purple-950">
        <CardHeader>
          <CardTitle className="text-base text-purple-900 dark:text-purple-100 flex items-center gap-2">
            <GitBranch className="h-4 w-4" />
            Vincoli HARD — RP-direct e BOM ordine-livello
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-purple-900 dark:text-purple-200 leading-relaxed">
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-purple-300 bg-white dark:bg-purple-900/30 p-3 space-y-1">
              <p className="text-xs font-bold">RP-direct (da rp_direct_pairs) — HARD</p>
              <p className="text-xs opacity-90">
                Per ogni arco <code className="font-mono bg-purple-100 dark:bg-purple-800 px-1 rounded">RP_pred → RP_succ</code> nel DAG:
                l'op con <code className="font-mono bg-purple-100 dark:bg-purple-800 px-1 rounded">reference_point_id = RP_pred</code> deve finire prima
                dell'op con <code className="font-mono bg-purple-100 dark:bg-purple-800 px-1 rounded">reference_point_id = RP_succ</code>.
                Solo le op di integrazione (con RP) sono vincolate — le op foglia (GROUP) lavorano libere.
              </p>
            </div>
            <div className="rounded-lg border border-purple-300 bg-white dark:bg-purple-900/30 p-3 space-y-1">
              <p className="text-xs font-bold">BOM ordine-livello — HARD</p>
              <p className="text-xs opacity-90">
                Ogni op di un ordine padre aspetta che <strong>tutte</strong> le op di{" "}
                <strong>tutti</strong> i figli BOM (diretti e ricorsivi) siano completate prima di iniziare.
                Attivo in greedy e CP-SAT. Garantisce che il montaggio del padre parta solo dopo i sotto-componenti.
              </p>
            </div>
          </div>
          <pre className="bg-white dark:bg-purple-900/30 rounded p-2 text-[10px] font-mono border border-purple-200 dark:border-purple-800 leading-relaxed overflow-x-auto">
{`# rp_direct_pairs: costruito in Step 4d
rp_to_op = {op.reference_point_id: op.id for op in ops if op.reference_point_id}
for prec in rp_dag_edges:
    pred_op = rp_to_op.get(prec.predecessor_rp_id)
    succ_op = rp_to_op.get(prec.rp_id)
    if pred_op and succ_op:
        rp_direct_pairs.append((pred_op, succ_op))  # → precedence_pairs HARD

# BOM: ogni op di AGG-010 aspetta GRP-032 + GRP-033 + GRP-034 (tutti i figli ricorsivi)`}
          </pre>
        </CardContent>
      </Card>

      {/* ── Priorità soft ── */}
      <Card className="border-orange-300 bg-orange-50 dark:bg-orange-950">
        <CardHeader>
          <CardTitle className="text-base text-orange-900 dark:text-orange-100 flex items-center gap-2">
            <GitBranch className="h-4 w-4" />
            Priorità di dispatch SOFT — RP level (no sequence_number)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-orange-900 dark:text-orange-200 leading-relaxed">
          <p className="text-xs">
            Le op foglia (GROUP/COMPONENT, senza <code className="font-mono bg-orange-100 dark:bg-orange-800 px-1 rounded">reference_point_id</code>) non hanno hard lock tra di loro.
            La dispatch priority guida quale op viene schedulata per prima quando le risorse sono limitate.
            Con risorse libere <strong>tutte partono in parallelo</strong>. Il{" "}
            <strong>sequence_number non contribuisce alla priorità</strong>: l'ordinamento deriva solo dai RP.
          </p>
          <pre className="bg-orange-100 dark:bg-orange-900 rounded p-2 text-[10px] font-mono leading-relaxed overflow-x-auto">
{`# op_priority = rp_level(production_order) × 10000
# GRP-032 (sotto AGG-001, RP level 0) → priority = 0
# GRP-035 (sotto AGG-002, RP level 1) → priority = 10000
# → con 1 risorsa: GRP-032 prima; con 2+ risorse: parallelo

# Bypass componente mancante:
# GRP-032 bloccata (earliest_start = giorno 10) → GRP-035 parte a t=0
# Nessun hard lock tra le op foglia, solo la op di integrazione (con RP) è vincolata`}
          </pre>
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
            { num: 1, title: "Scenario + machine order + epoch", desc: "Recupera scenario attivo e machine_order; calcola epoch (inizio piano UTC) e start_date." },
            { num: 2, title: "Operazioni schedulabili", desc: "Esclude COMPLETED e quelle senza routing. IN_PROGRESS entrano con durata residua e ancorate a now. Senza workcenter → scartate con warning." },
            { num: 3, title: "Carica i gruppi risorse (resource_type)", desc: "Per ogni ResourceType attivo costruisce un ResourceGroup: capacità di gruppo = count × ore/giorno. Niente più operatori/slot di calendario." },
            { num: 4, title: "Vincoli componenti mancanti", desc: "Componenti mancanti → earliest_start >= arrivo. Nessuna intra-routing da sequence_number (rimossa): l'ordinamento viene solo dai RP." },
            { num: 5, title: "Precedenze RP-direct (HARD) + BOM (HARD) + priority (SOFT)", desc: "rp_direct_pairs: per ogni arco RP DAG, l'op con reference_point_id=RP_pred → l'op con reference_point_id=RP_succ (hard entrambi i solver). parent_wait_constraints (HARD): padre aspetta tutti i figli BOM. rp_order_constraints: calcola rp_level → op_priority (soft)." },
            { num: 6, title: "Scheduling (greedy → CP-SAT)", desc: "Greedy: topo-sort su hard (RP-direct + BOM) + dispatch per (earliest_start, op_priority). CP-SAT: RP-direct + BOM hard, ottimizza makespan, warm-start dal greedy. Timeout → fallback greedy." },
            { num: 7, title: "Persisti entries + summary", desc: "Una schedule_entry per blocco (resource_type_id valorizzato, operator_id null). Summary con makespan, risorse usate, conflitti. Entries STALE rimosse." },
          ].map((s) => (
            <PipelineStep key={s.num} {...s} />
          ))}
        </CardContent>
      </Card>

    </div>
  );
}
