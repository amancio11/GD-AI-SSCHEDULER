/**
 * ScenarioManager.tsx — Gestione scenari di scheduling
 *
 * SCENARI — cos'è e perché esistono:
 *
 * Uno Scenario è un "piano alternativo" completo per la macchina.
 * Permette di rispondere a domande come:
 *   "Se puntassimo a finire entro il 30 luglio, quanti operatori ci vogliono?"
 *   "Conviene minimizzare gli operatori o massimizzare l'utilizzo?"
 *
 * TIPI DI OBIETTIVO:
 *   FINISH_BY_DATE             — CP-SAT minimizza il rischio di sforare la data target
 *   MAXIMIZE_RESOURCE_UTILIZATION — massimizza il % di utilizzo degli operatori
 *   MINIMIZE_OPERATORS         — usa il minor numero possibile di operatori distinti
 *   CUSTOM                     — combinazione pesata dei 3 obiettivi sopra
 *
 * STATO DI UNO SCENARIO:
 *   Senza run    → scenario vuoto, nessuna schedule_entry
 *   Dopo run     → il solver CP-SAT ha assegnato ogni operazione a un operatore
 *   ACTIVE       → il piano "ufficiale" della macchina (uno solo per macchina)
 *   BASELINE     → riferimento di confronto (es. il piano originale approvato)
 *
 * CONFRONTO:
 *   delta_makespan_days < 0 → lo Scenario B è più veloce (meglio per FINISH_BY_DATE)
 *   delta_operators < 0     → lo Scenario B usa meno operatori
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useMachineStore } from '../store/machineStore';
import { useScheduleStore } from '../store/scheduleStore';
import type { ScheduleScenario, ScheduleRunSummary } from '../api/types';
import {
  Play, Plus, Loader2, Info, ArrowUpDown, TrendingDown, TrendingUp, Minus,
  CheckCircle, Target, Users, Calendar, Zap, ChevronDown,
  AlertTriangle, Link, Database,
} from 'lucide-react';

// ── Tipi ─────────────────────────────────────────────────────────────────────

interface ScenarioComparisonResult {
  delta_makespan_days: number | null;
  delta_operators: number | null;
  delta_utilization: number | null;
  gantt_a: unknown[];
  gantt_b: unknown[];
}

// ── Costanti UI ───────────────────────────────────────────────────────────────

const OBJECTIVE_META: Record<string, {
  label: string;
  description: string;
  detail: string;
  icon: React.ReactNode;
  color: string;
}> = {
  FINISH_BY_DATE: {
    label: 'Finisci entro data',
    description: 'Il piano deve chiudersi entro la data target. Se impossibile il solver segnala INFEASIBLE.',
    detail: 'Vincolo hard: makespan ≤ data_target. Poi minimizza la durata totale. Richiede che "Data target di completamento" sia impostata. Utile quando c\'è una consegna ferma al cliente.',
    icon: <Calendar size={14} />,
    color: 'blue',
  },
  MAXIMIZE_RESOURCE_UTILIZATION: {
    label: 'Massimizza utilizzo risorse',
    description: 'Minimizza la durata totale del piano e distribuisce il lavoro su tutti i gruppi disponibili.',
    detail: 'Obiettivo primario: finire il prima possibile (makespan minimo). Obiettivo secondario: usare il maggior numero di gruppi-risorsa possibile, così nessuna risorsa rimane idle mentre un\'altra è satura. Utile per massimizzare la produttività dell\'impianto.',
    icon: <Zap size={14} />,
    color: 'green',
  },
  MINIMIZE_OPERATORS: {
    label: 'Minimizza gruppi risorsa',
    description: 'Concentra il lavoro sul minor numero possibile di gruppi risorsa distinti.',
    detail: 'Obiettivo primario: minimizza i gruppi (workcenter+skill) effettivamente usati. Obiettivo secondario: minimizza makespan a parità di gruppi. Utile per pianificare con crew ridotta, turni limitati o impianti parzialmente attivi.',
    icon: <Users size={14} />,
    color: 'purple',
  },
  CUSTOM: {
    label: 'Solo makespan',
    description: 'Minimizza la durata totale del piano senza vincoli aggiuntivi sulla distribuzione delle risorse.',
    detail: 'Obiettivo puro: Minimize(makespan). Nessun peso secondario su gruppi o distribuzione. Equivale a MAXIMIZE_RESOURCE_UTILIZATION ma senza il termine di bilanciamento. Utile come baseline di confronto.',
    icon: <Target size={14} />,
    color: 'orange',
  },
};

// ── Hook polling task ─────────────────────────────────────────────────────────

interface TaskResult {
  task_id: string;
  status: string;
  ready: boolean;
  solver_status?: string;
  makespan_days?: number;
  operators_used?: number;
  conflicts?: string[];
  summary?: ScheduleRunSummary;
}

function useTaskPoller(taskId: string | null, onComplete: (result?: TaskResult) => void) {
  const [pollingId, setPollingId] = useState<ReturnType<typeof setInterval> | null>(null);

  const startPolling = (id: string) => {
    const interval = setInterval(async () => {
      try {
        const { data } = await apiClient.get<TaskResult>(`/api/schedule/tasks/${id}`);
        if (data.status === 'SUCCESS' || data.status === 'FAILURE') {
          clearInterval(interval);
          setPollingId(null);
          onComplete(data);
        }
      } catch {
        clearInterval(interval);
        setPollingId(null);
        onComplete();
      }
    }, 2000);
    setPollingId(interval);
    return interval;
  };

  return { startPolling, isPolling: !!pollingId };
}

// ── Hooks dati ────────────────────────────────────────────────────────────────

function useScenarios() {
  return useQuery<ScheduleScenario[]>({
    queryKey: ['scenarios'],
    queryFn: async () => {
      const { data } = await apiClient.get<ScheduleScenario[]>('/api/scenarios?page=1&size=50');
      return data;
    },
  });
}

// ── Componente card scenario ──────────────────────────────────────────────────

function ScenarioCard({
  scenario, isSelected, isScheduling,
  onSelect, onActivate, onBaseline, onDelete, onSchedule,
}: {
  scenario: ScheduleScenario;
  isSelected: boolean;
  isScheduling: boolean;
  onSelect: () => void;
  onActivate: () => void;
  onBaseline: () => void;
  onDelete: () => void;
  onSchedule: () => void;
}) {
  const [showReport, setShowReport] = useState(false);
  const meta = OBJECTIVE_META[scenario.objective_mode];
  const colorMap: Record<string, string> = {
    blue: 'border-blue-600/60 bg-blue-900/10',
    green: 'border-green-600/60 bg-green-900/10',
    purple: 'border-purple-600/60 bg-purple-900/10',
    orange: 'border-orange-600/60 bg-orange-900/10',
  };

  return (
    <div
      onClick={onSelect}
      className={`
        border rounded-xl p-4 cursor-pointer transition-all
        ${isSelected ? 'border-primary ring-2 ring-primary/20' : (meta ? colorMap[meta.color] : 'border-border')}
      `}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-sm truncate">{scenario.name}</p>
          <div className="flex items-center gap-1.5 mt-0.5 text-[10px] text-muted-foreground">
            {meta?.icon}
            <span>{meta?.label || scenario.objective_mode}</span>
          </div>
          {scenario.last_run_status && (
            <span
              className={`inline-flex px-2 py-1 rounded text-[10px] font-semibold ${
                scenario.last_run_status === 'OPTIMAL'
                  ? 'bg-green-900/30 text-green-400 border border-green-700/30'
                  : scenario.last_run_status === 'FEASIBLE'
                    ? 'bg-blue-900/30 text-blue-400 border border-blue-700/30'
                    : scenario.last_run_status === 'INFEASIBLE'
                      ? 'bg-red-900/30 text-red-400 border border-red-700/30'
                      : 'bg-gray-900/30 text-gray-400 border border-gray-700/30'
              }`}
            >
              {scenario.last_run_status === 'OPTIMAL' ? '✓ Ottimale'
                : scenario.last_run_status === 'FEASIBLE' ? '~ Fattibile'
                : scenario.last_run_status === 'INFEASIBLE' ? '✕ Non fattibile'
                : scenario.last_run_status}
            </span>
          )}

          {scenario.last_run_status === 'INFEASIBLE' && (
            <p className="text-xs text-red-300 mt-2">
              Vincoli incompatibili — modificare operatori, date o precedenze e riprovare
            </p>
          )}
        </div>
        <div className="flex gap-1 ml-2 shrink-0 flex-wrap justify-end">
          {scenario.is_active && (
            <span className="text-[10px] bg-green-900/50 text-green-300 rounded px-1.5 py-0.5 font-semibold flex items-center gap-1">
              <CheckCircle size={9} /> ATTIVO
            </span>
          )}
          {(scenario as ScheduleScenario & { is_baseline?: boolean }).is_baseline && (
            <span className="text-[10px] bg-blue-900/50 text-blue-300 rounded px-1.5 py-0.5 font-semibold">BASELINE</span>
          )}
          {(scenario as ScheduleScenario & { last_run_status?: string }).last_run_status && (
            <span className={`text-[10px] rounded px-1.5 py-0.5 font-semibold ${
              (scenario as ScheduleScenario & { last_run_status?: string }).last_run_status === 'OPTIMAL'
                ? 'bg-emerald-900/50 text-emerald-300'
              : (scenario as ScheduleScenario & { last_run_status?: string }).last_run_status === 'FEASIBLE'
                ? 'bg-cyan-900/50 text-cyan-300'
              : (scenario as ScheduleScenario & { last_run_status?: string }).last_run_status === 'INFEASIBLE'
                ? 'bg-red-900/50 text-red-300'
              : 'bg-gray-900/50 text-gray-400'
            }`}>
              {(scenario as ScheduleScenario & { last_run_status?: string }).last_run_status === 'OPTIMAL' && '✓ Ottimale'}
              {(scenario as ScheduleScenario & { last_run_status?: string }).last_run_status === 'FEASIBLE' && '~ Fattibile'}
              {(scenario as ScheduleScenario & { last_run_status?: string }).last_run_status === 'INFEASIBLE' && '✕ Non fattibile'}
            </span>
          )}
        </div>
      </div>

      {/* Descrizione obiettivo inline */}
      {meta && (
        <p className="text-[11px] text-muted-foreground mb-2 leading-relaxed">
          {meta.description}
        </p>
      )}

      {/* Date di scheduling */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mb-2">
        {scenario.start_date && (
          <p className="text-[10px] text-muted-foreground">
            Inizio: <span className="text-foreground font-medium">
              {new Date(scenario.start_date).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })}
            </span>
          </p>
        )}
        {scenario.target_finish_date && (
          <p className="text-[10px] text-blue-300">
            Target: <span className="font-medium">
              {new Date(scenario.target_finish_date).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })}
            </span>
          </p>
        )}
      </div>

      {/* KPI ultimo run */}
      {(scenario.last_run_makespan_days != null || scenario.last_run_operators_used != null) && (
        <div className="flex gap-3 mb-2 text-[10px] text-muted-foreground">
          {scenario.last_run_makespan_days != null && (
            <span>Durata: <span className="text-foreground font-medium">{scenario.last_run_makespan_days}gg</span></span>
          )}
          {scenario.last_run_operators_used != null && (
            <span>Operatori: <span className="text-foreground font-medium">{scenario.last_run_operators_used}</span></span>
          )}
        </div>
      )}

      {/* Link al report dettagliato */}
      {scenario.last_run_summary && (
        <button
          onClick={(e) => { e.stopPropagation(); setShowReport(true); }}
          className="text-[10px] text-primary hover:underline mb-2 flex items-center gap-1"
        >
          <ChevronDown size={10} /> Vedi report scheduling
        </button>
      )}

      <p className="text-[10px] text-muted-foreground mb-3">
        Creato: {new Date(scenario.created_at).toLocaleDateString('it-IT', {
          day: '2-digit', month: 'short', year: 'numeric',
        })}
      </p>

      {/* Azione principale */}
      <button
        onClick={(e) => { e.stopPropagation(); onSchedule(); }}
        disabled={isScheduling}
        className="w-full flex items-center justify-center gap-1.5 py-1.5 bg-primary text-primary-foreground rounded text-xs font-medium hover:opacity-90 disabled:opacity-60 mb-2"
      >
        {isScheduling ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
        {isScheduling ? 'Scheduling in corso…' : 'Esegui Scheduling'}
      </button>

      {/* Azioni secondarie */}
      <div className="flex gap-1.5">
        <button
          onClick={(e) => { e.stopPropagation(); onActivate(); }}
          className="text-xs px-2 py-0.5 border border-border rounded hover:bg-accent flex-1"
          title="Imposta come piano attivo"
        >
          Attiva
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onBaseline(); }}
          className="text-xs px-2 py-0.5 border border-border rounded hover:bg-accent flex-1"
          title="Usa come baseline di riferimento"
        >
          Baseline
        </button>
         <button
          onClick={(e) => {
            e.stopPropagation();
            if (window.confirm(`Eliminare "${scenario.name}"?\nTutte le schedule entries verranno cancellate.`)) {
              onDelete();
            }
          }}
          className="text-xs px-2 py-0.5 border border-destructive text-destructive rounded hover:bg-red-50 dark:hover:bg-red-950"
          title="Elimina scenario"
        >
          ✕
        </button>
      </div>

      {/* Report modal */}
      {showReport && scenario.last_run_summary && (
        <SchedulingReportModal
          summary={scenario.last_run_summary as ScheduleRunSummary}
          scenarioName={scenario.name}
          onClose={() => setShowReport(false)}
        />
      )}
    </div>
  );
}

// ── Modal: Guida agli scenari ─────────────────────────────────────────────────

function ScenarioGuideModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-background border border-border rounded-xl p-6 max-w-2xl w-full max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-bold">Guida agli Scenari di Scheduling</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-lg leading-none">✕</button>
        </div>

        <div className="space-y-6 text-sm">

          {/* Cos'è uno scenario */}
          <div>
            <h3 className="font-semibold mb-2 text-primary">Cos'è uno Scenario?</h3>
            <p className="text-foreground/80 leading-relaxed">
              Uno scenario è un piano di scheduling completo e indipendente per la macchina.
              Puoi creare più scenari con obiettivi diversi, eseguire il solver CP-SAT su ciascuno
              e confrontarli per scegliere il piano migliore da rendere ATTIVO.
            </p>
          </div>

          {/* ATTIVO vs BASELINE */}
          <div>
            <h3 className="font-semibold mb-3">ATTIVO e BASELINE — cosa significano</h3>
            <div className="grid grid-cols-1 gap-3">
              <div className="flex gap-3 p-3 border border-green-500/40 bg-green-500/10 rounded-lg">
                <span className="text-[10px] bg-green-600 text-white rounded px-1.5 py-0.5 font-bold shrink-0 self-start mt-0.5">ATTIVO</span>
                <div className="space-y-1">
                  <p className="font-medium text-foreground">Piano ufficiale della macchina</p>
                  <p className="text-foreground/70 text-xs leading-relaxed">
                    Un solo scenario per macchina può essere ATTIVO. Attivare uno scenario de-attiva automaticamente il precedente.
                    È il piano che il sistema usa operativamente: quando arriva un evento di ritardo, il{" "}
                    <strong>delay_propagation engine</strong> rileva lo scenario attivo e avvia automaticamente
                    il reschedule su di esso. Il Gantt mostra le schedule entries di questo scenario.
                  </p>
                  <p className="text-foreground/60 text-xs">
                    Tipicamente si attiva lo scenario migliore dopo averli confrontati.
                  </p>
                </div>
              </div>
              <div className="flex gap-3 p-3 border border-blue-500/40 bg-blue-500/10 rounded-lg">
                <span className="text-[10px] bg-blue-600 text-white rounded px-1.5 py-0.5 font-bold shrink-0 self-start mt-0.5">BASELINE</span>
                <div className="space-y-1">
                  <p className="font-medium text-foreground">Snapshot di riferimento per il confronto</p>
                  <p className="text-foreground/70 text-xs leading-relaxed">
                    Il BASELINE è un piano congelato che non viene toccato dai reschedule automatici
                    e non guida il Gantt. Il suo scopo è nella sezione <strong>Confronta scenari</strong>:
                    puoi metterlo a fianco di qualsiasi altro scenario per vedere i delta di makespan,
                    operatori usati e numero di entries.
                  </p>
                  <p className="text-foreground/60 text-xs">
                    Tipicamente si imposta come baseline il piano approvato prima di iniziare la produzione,
                    poi si confrontano i reschedule successivi contro di esso per capire quanto il piano è cambiato.
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Tipi di obiettivo */}
          <div>
            <h3 className="font-semibold mb-3">Tipi di Obiettivo</h3>
            <div className="space-y-2">
              {Object.entries(OBJECTIVE_META).map(([key, meta]) => (
                <div key={key} className="flex gap-3 p-3 border border-border rounded-lg">
                  <div className="mt-0.5 shrink-0 text-foreground/60">{meta.icon}</div>
                  <div>
                    <p className="font-medium text-sm text-foreground">{meta.label}</p>
                    <p className="text-foreground/60 text-xs mt-0.5 leading-relaxed">{meta.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Workflow */}
          <div>
            <h3 className="font-semibold mb-2">Workflow tipico</h3>
            <ol className="space-y-2 text-xs">
              {[
                'Crea 2-3 scenari con obiettivi diversi (es. "Finisci entro 30/8" e "Minimizza operatori")',
                'Esegui lo scheduling su ciascuno — CP-SAT calcola il piano ottimale per ogni obiettivo',
                'Usa "Confronta" per vedere i delta di makespan e numero operatori tra due scenari',
                'Imposta come BASELINE il piano approvato prima di iniziare',
                'Attiva lo scenario migliore → diventa il piano ufficiale, tutti i ritardi verranno rischedulati su di esso',
                'Se cambiano i dati (nuovi ritardi, componenti mancanti) → ri-esegui lo scheduling',
              ].map((step, i) => (
                <li key={i} className="flex gap-2 text-foreground/70">
                  <span className="text-primary font-bold shrink-0">{i + 1}.</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </div>

          {/* Date */}
          <div className="border border-border rounded-lg p-3 space-y-2 bg-muted/30">
            <p className="text-xs font-semibold text-foreground">Data di inizio e data target</p>
            <p className="text-foreground/70 text-xs leading-relaxed">
              <strong className="text-foreground">Data di inizio scheduling</strong>: punto zero del solver CP-SAT.
              Utile per simulare scenari futuri ("se iniziassimo il 1° luglio…") o rieseguire
              scheduling storici. Se omessa, si usa la data odierna.
            </p>
            <p className="text-foreground/70 text-xs leading-relaxed">
              <strong className="text-foreground">Data target (FINISH_BY_DATE)</strong>: il solver CP-SAT
              minimizza il ritardo rispetto a questa data. Se i vincoli rendono impossibile
              rispettare la scadenza, il risultato sarà INFEASIBLE con spiegazione dettagliata.
            </p>
          </div>

        </div>
      </div>
    </div>
  );
}

// ── Modal: Nuovo Scenario ─────────────────────────────────────────────────────

// ── Componente Report Scheduling ─────────────────────────────────────────────

function SchedulingReport({ summary, scenarioName }: {
  summary: ScheduleRunSummary;
  scenarioName?: string;
}) {
  const fmtDate = (iso: string | null) => iso
    ? new Date(iso).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })
    : '—';
  const fmtDateTime = (iso: string | null) => iso
    ? new Date(iso).toLocaleString('it-IT', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
    : '—';
  const fmtMin = (min: number) => {
    const days = Math.floor(min / 480);
    const hours = Math.round((min % 480) / 60);
    return days > 0 ? `${days}g ${hours}h` : `${Math.round(min / 60)}h`;
  };

  const statusColor = summary.solver_status === 'OPTIMAL'
    ? 'text-green-400 bg-green-900/20 border-green-700/30'
    : summary.solver_status === 'FEASIBLE'
      ? 'text-blue-400 bg-blue-900/20 border-blue-700/30'
      : 'text-red-400 bg-red-900/20 border-red-700/30';

  const coverage = summary.total_schedulable_ops > 0
    ? Math.round((summary.scheduled_ops / summary.total_schedulable_ops) * 100)
    : 0;

  const loadPct = summary.total_capacity_minutes > 0
    ? Math.round((summary.total_work_minutes / summary.total_capacity_minutes) * 100)
    : null;

  const objectiveLabels: Record<string, string> = {
    FINISH_BY_DATE: 'Finisci entro data target',
    MAXIMIZE_RESOURCE_UTILIZATION: 'Massimizza utilizzo risorse',
    MINIMIZE_OPERATORS: 'Minimizza gruppi risorsa',
    CUSTOM: 'Solo makespan',
    FEASIBILITY: 'Soddisfacibilità pura',
  };

  const engineLabels: Record<string, string> = {
    greedy: 'Greedy (CP-SAT non ha migliorato)',
    cpsat: 'CP-SAT ottimizzato',
  };

  const triggerLabels: Record<string, string> = {
    api: 'Avviato manualmente via UI',
    manual: 'Avviato manualmente',
  };
  const triggerLabel = summary.triggered_by?.startsWith('delay_event:')
    ? `Evento ritardo (${summary.triggered_by.split(':')[1]?.slice(0, 8)}…)`
    : (triggerLabels[summary.triggered_by] ?? summary.triggered_by ?? '—');

  return (
    <div className="space-y-4 text-sm">
      {scenarioName && (
        <p className="text-xs text-muted-foreground">Scenario: <span className="text-foreground font-medium">{scenarioName}</span></p>
      )}

      {/* Status + contesto run */}
      <div className={`flex items-start gap-3 p-3 border rounded-lg ${statusColor}`}>
        <div className="text-2xl font-bold mt-0.5">
          {summary.solver_status === 'OPTIMAL' ? '✓' : summary.solver_status === 'FEASIBLE' ? '~' : '✕'}
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-semibold">
            {summary.solver_status === 'OPTIMAL' ? 'Soluzione ottimale trovata'
              : summary.solver_status === 'FEASIBLE' ? 'Soluzione fattibile trovata'
              : 'Soluzione non trovata (INFEASIBLE)'}
          </p>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs opacity-80 mt-1">
            <span>Risolto in {summary.solve_time_seconds != null ? `${summary.solve_time_seconds}s` : '—'}</span>
            <span>Motore: {summary.engine_used ? engineLabels[summary.engine_used] ?? summary.engine_used : '—'}</span>
            <span>Obiettivo: {objectiveLabels[summary.objective_mode] ?? summary.objective_mode}</span>
            <span>Trigger: {triggerLabel}</span>
          </div>
        </div>
      </div>

      {/* Finestra temporale + KPI principali */}
      <div className="grid grid-cols-2 gap-3">
        <div className="border border-border rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
            <Calendar size={12} /> Finestra temporale
          </div>
          <div className="text-xs space-y-0.5">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Inizio scheduling:</span>
              <span className="font-medium">{fmtDate(summary.schedule_start_date)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Horizon solver:</span>
              <span className="font-medium">{fmtDate(summary.horizon_date)}</span>
            </div>
            {summary.earliest_start && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Prima op inizia:</span>
                <span className="font-medium">{fmtDateTime(summary.earliest_start)}</span>
              </div>
            )}
            {summary.latest_end && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Ultima op finisce:</span>
                <span className="font-medium">{fmtDateTime(summary.latest_end)}</span>
              </div>
            )}
            {summary.makespan_days != null && (
              <div className="flex justify-between border-t border-border pt-1 mt-1">
                <span className="text-muted-foreground">Durata totale:</span>
                <span className="font-bold text-foreground">{summary.makespan_days} giorni lavorativi</span>
              </div>
            )}
          </div>
        </div>

        <div className="border border-border rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
            <Database size={12} /> Operazioni
          </div>
          <div className="text-xs space-y-0.5">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Totale schedulabili:</span>
              <span className="font-medium">{summary.total_schedulable_ops}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Schedulate:</span>
              <span className="font-medium text-green-400">{summary.scheduled_ops}</span>
            </div>
            {summary.in_progress_anchored > 0 && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">IN_PROGRESS (ancorate a ora):</span>
                <span className="font-medium text-blue-400">{summary.in_progress_anchored}</span>
              </div>
            )}
            {summary.orphan_ops_count > 0 && (
              <div className="flex justify-between text-amber-400">
                <span>Senza operatori qualificati:</span>
                <span className="font-medium">{summary.orphan_ops_count}</span>
              </div>
            )}
            {summary.impossible_ops_count > 0 && (
              <div className="flex justify-between text-red-400">
                <span>Impossibili (oltre horizon):</span>
                <span className="font-medium">{summary.impossible_ops_count}</span>
              </div>
            )}
            <div className="pt-1.5">
              <div className="flex justify-between text-[10px] mb-1">
                <span className="text-muted-foreground">Copertura</span>
                <span className="font-bold">{coverage}%</span>
              </div>
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${coverage === 100 ? 'bg-green-500' : coverage > 80 ? 'bg-blue-500' : 'bg-amber-500'}`}
                  style={{ width: `${coverage}%` }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Carico vs Capacità */}
        <div className="border border-border rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
            <Zap size={12} /> Carico vs Capacità operatori
          </div>
          <div className="text-xs space-y-0.5">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Lavoro totale da schedulare:</span>
              <span className="font-medium">{summary.total_work_minutes != null ? fmtMin(summary.total_work_minutes) : '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Capacità totale disponibile:</span>
              <span className="font-medium">{summary.total_capacity_minutes != null ? fmtMin(summary.total_capacity_minutes) : '—'}</span>
            </div>
            {loadPct != null && (
              <div className="pt-1.5">
                <div className="flex justify-between text-[10px] mb-1">
                  <span className="text-muted-foreground">Saturazione capacità</span>
                  <span className={`font-bold ${loadPct > 100 ? 'text-red-400' : loadPct > 80 ? 'text-amber-400' : 'text-green-400'}`}>{loadPct}%</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${loadPct > 100 ? 'bg-red-500' : loadPct > 80 ? 'bg-amber-500' : 'bg-green-500'}`}
                    style={{ width: `${Math.min(loadPct, 100)}%` }}
                  />
                </div>
                {loadPct > 100 && (
                  <p className="text-[10px] text-red-400 mt-1">La capacità disponibile è inferiore al lavoro richiesto — la soluzione può essere solo FEASIBLE, non OPTIMAL.</p>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Operatori */}
        <div className="border border-border rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
            <Users size={12} /> Operatori
          </div>
          <div className="text-xs space-y-0.5">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Attivi nel sistema:</span>
              <span className="font-medium">{summary.operators_total}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Con turni nel periodo:</span>
              <span className="font-medium">{summary.operators_with_slots}</span>
            </div>
            {summary.operators_total - summary.operators_with_slots > 0 && (
              <div className="flex justify-between text-amber-400">
                <span>Senza turni (esclusi):</span>
                <span className="font-medium">{summary.operators_total - summary.operators_with_slots}</span>
              </div>
            )}
            {summary.operators_used != null && (
              <div className="flex justify-between border-t border-border pt-1 mt-1">
                <span className="text-muted-foreground">Effettivamente assegnati:</span>
                <span className="font-bold text-foreground">{summary.operators_used}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Workcenter breakdown */}
      {summary.workcenter_breakdown?.length > 0 && (
        <div className="border border-border rounded-lg p-3">
          <p className="text-xs font-semibold text-muted-foreground mb-2">Matching operatori ↔ workcenter</p>
          <div className="space-y-1.5">
            {summary.workcenter_breakdown.map((wc) => {
              const ratio = wc.operators_available === 0 ? 0 : Math.min(wc.operators_available / Math.max(wc.ops_count / 5, 1), 1);
              const hasGap = wc.operators_available === 0;
              return (
                <div key={wc.workcenter_id} className={`flex items-center gap-2 text-xs rounded px-2 py-1 ${hasGap ? 'bg-red-900/20 border border-red-700/30' : 'bg-muted/30'}`}>
                  <code className="font-mono text-[10px] text-muted-foreground w-32 truncate shrink-0">{wc.workcenter_id.slice(0, 8)}…</code>
                  <span className="text-muted-foreground shrink-0">{wc.ops_count} op</span>
                  <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${hasGap ? 'bg-red-500' : 'bg-blue-500'}`} style={{ width: `${Math.round(ratio * 100)}%` }} />
                  </div>
                  <span className={`font-medium shrink-0 ${hasGap ? 'text-red-400' : ''}`}>{wc.operators_available} op.</span>
                  {hasGap && <span className="text-red-400 text-[10px]">⚠ nessun operatore</span>}
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-muted-foreground mt-2">La barra mostra il rapporto tra operatori disponibili e volume di lavoro nel workcenter. Un workcenter senza operatori genera operazioni orphan non schedulabili.</p>
        </div>
      )}

      {/* Vincoli CP-SAT applicati */}
      <div className="border border-border rounded-lg p-3">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mb-2">
          <Link size={12} /> Vincoli CP-SAT applicati
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Precedenza rami BOM (Tipo B):</span>
            <span className="font-medium">{summary.rp_order_constraints_count}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Padre aspetta figli (Tipo A):</span>
            <span className="font-medium">{summary.parent_wait_constraints_count}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Blocchi componenti mancanti:</span>
            <span className={`font-medium ${summary.missing_constraints_active > 0 ? 'text-amber-400' : ''}`}>
              {summary.missing_constraints_active}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Totale vincoli attivi:</span>
            <span className="font-medium">
              {summary.rp_order_constraints_count + summary.parent_wait_constraints_count + summary.missing_constraints_active}
            </span>
          </div>
        </div>
        <div className="mt-2 space-y-1 text-[10px] text-muted-foreground border-t border-border pt-2">
          <p><strong className="text-foreground">Tipo B</strong>: impongono l'ordine di montaggio tra rami paralleli della BOM (es. struttura portante deve finire prima che inizi l'idraulico).</p>
          <p><strong className="text-foreground">Tipo A</strong>: ogni operazione padre aspetta il 100% dei figli (es. collaudo finale aspetta tutti i sottoaggregati).</p>
          {summary.missing_constraints_active > 0 && (
            <p className="text-amber-400"><strong>Componenti mancanti</strong>: {summary.missing_constraints_active} operazioni bloccate fino all'arrivo del materiale — queste non possono iniziare prima della data attesa.</p>
          )}
        </div>
      </div>

      {/* Conflitti (se INFEASIBLE) */}
      {summary.conflicts.length > 0 && (
        <div className="border border-red-600/30 bg-red-900/10 rounded-lg p-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-red-400 mb-2">
            <AlertTriangle size={12} /> Motivi dell'INFEASIBLE
          </div>
          <ul className="space-y-1">
            {summary.conflicts.map((c, i) => (
              <li key={i} className="text-xs text-red-300/80 pl-3 border-l-2 border-red-800">{c}</li>
            ))}
          </ul>
          <p className="text-[10px] text-red-300/60 mt-2">Suggerimento: verifica la disponibilità degli operatori nel calendario, rimuovi blocchi su componenti già arrivati, oppure estendi l'horizon dello scenario.</p>
        </div>
      )}

      {/* Warning non bloccanti */}
      {(summary.orphan_ops_count > 0 || summary.impossible_ops_count > 0) && summary.conflicts.length === 0 && (
        <div className="border border-amber-600/30 bg-amber-900/10 rounded-lg p-3 text-xs space-y-1">
          <div className="flex items-center gap-1.5 font-semibold text-amber-400 mb-1">
            <AlertTriangle size={12} /> Avvisi (soluzione trovata ma incompleta)
          </div>
          {summary.orphan_ops_count > 0 && (
            <p className="text-amber-300/80"><strong>{summary.orphan_ops_count} operazioni orphan</strong>: nessun operatore qualificato per quel workcenter nel periodo — aggiungi operatori al calendario o verifica le skill.</p>
          )}
          {summary.impossible_ops_count > 0 && (
            <p className="text-amber-300/80"><strong>{summary.impossible_ops_count} operazioni impossibili</strong>: la durata residua supera l'horizon disponibile — estendi la data di orizzonte dello scenario.</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Modal report scheduling ───────────────────────────────────────────────────

function SchedulingReportModal({ summary, scenarioName, onClose }: {
  summary: ScheduleRunSummary;
  scenarioName: string;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-background border border-border rounded-xl p-6 max-w-2xl w-full max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-bold">Report Scheduling</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>
        <SchedulingReport summary={summary} scenarioName={scenarioName} />
      </div>
    </div>
  );
}

// ── Modal: Nuovo Scenario ─────────────────────────────────────────────────────

function NewScenarioModal({
  machineOrderId,
  onClose,
}: {
  machineOrderId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [objective, setObjective] = useState<string>('FINISH_BY_DATE');
  const [startDate, setStartDate] = useState('');
  const [targetDate, setTargetDate] = useState('');
  const [state, setState] = useState<'idle' | 'creating' | 'scheduling' | 'done'>('idle');
  const [taskId, setTaskId] = useState<string | null>(null);

  const { startPolling } = useTaskPoller(taskId, () => {
    setState('done');
    qc.invalidateQueries({ queryKey: ['scenarios'] });
    setTimeout(onClose, 1500);
  });

  async function handleCreate() {
    if (!name.trim()) return;
    setState('creating');
    try {
      const { data: scenario } = await apiClient.post<ScheduleScenario>('/api/scenarios', {
        name: name.trim(),
        machine_order_id: machineOrderId,
        objective_mode: objective,
        start_date: startDate || null,
        target_finish_date: objective === 'FINISH_BY_DATE' && targetDate ? targetDate : null,
        is_active: false,
        is_baseline: false,
      });
      setState('scheduling');
      const { data: run } = await apiClient.post<{ task_id: string }>(`/api/scenarios/${scenario.id}/run`);
      setTaskId(run.task_id);
      startPolling(run.task_id);
    } catch {
      setState('idle');
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-background border border-border rounded-xl p-6 max-w-lg w-full max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-bold">Nuovo Scenario</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        {state === 'done' ? (
          <div className="flex flex-col items-center py-8 gap-3 text-green-400">
            <CheckCircle size={40} />
            <p className="font-semibold">Scenario creato e schedulato!</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium mb-1">Nome scenario</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="es. Piano consegna 30 agosto"
                className="w-full px-3 py-2 text-sm border border-border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>

            <div>
              <label className="block text-xs font-medium mb-2">Obiettivo di scheduling</label>
              <div className="space-y-2">
                {Object.entries(OBJECTIVE_META).map(([key, meta]) => (
                  <label
                    key={key}
                    className={`flex items-start gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                      objective === key ? 'border-primary bg-primary/5' : 'border-border hover:bg-accent/30'
                    }`}
                  >
                    <input
                      type="radio"
                      name="objective"
                      value={key}
                      checked={objective === key}
                      onChange={() => setObjective(key)}
                      className="mt-0.5"
                    />
                    <div>
                      <div className="flex items-center gap-1.5 font-medium text-sm">
                        <span className="text-muted-foreground">{meta.icon}</span>
                        {meta.label}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">{meta.description}</p>
                      {objective === key && (
                        <p className="text-[10px] text-muted-foreground/70 mt-1 italic">{meta.detail}</p>
                      )}
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Data di partenza — disponibile per tutti gli obiettivi */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium mb-1">
                  Data di inizio scheduling
                  <span className="ml-1 text-muted-foreground font-normal">(opzionale)</span>
                </label>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <p className="text-[10px] text-muted-foreground mt-1">
                  Se vuoto, usa la data odierna
                </p>
              </div>

              {objective === 'FINISH_BY_DATE' && (
                <div>
                  <label className="block text-xs font-medium mb-1">
                    Data target di completamento
                    <span className="ml-1 text-red-400 font-normal">*</span>
                  </label>
                  <input
                    type="date"
                    value={targetDate}
                    min={startDate || undefined}
                    onChange={(e) => setTargetDate(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <p className="text-[10px] text-muted-foreground mt-1">
                    Il solver vincola il makespan a questa data
                  </p>
                </div>
              )}
            </div>

            {objective === 'FINISH_BY_DATE' && !targetDate && (
              <div className="border border-amber-600/30 bg-amber-900/10 rounded p-2 text-amber-300 text-xs">
                Inserire una data target per il vincolo FINISH_BY_DATE.
                Senza data il solver minimizza il makespan senza limite temporale.
              </div>
            )}

            <button
              onClick={handleCreate}
              disabled={!name.trim() || state !== 'idle'}
              className="w-full flex items-center justify-center gap-2 py-2 bg-primary text-primary-foreground rounded font-medium text-sm hover:opacity-90 disabled:opacity-50"
            >
              {state === 'creating' && <Loader2 size={14} className="animate-spin" />}
              {state === 'scheduling' && <Loader2 size={14} className="animate-spin" />}
              {state === 'idle' && <Plus size={14} />}
              {state === 'idle' ? 'Crea e Schedula' : state === 'creating' ? 'Creazione…' : 'Scheduling…'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sezione confronto scenari ─────────────────────────────────────────────────

function DeltaCell({ value, unit = '', lowerIsBetter = true }: {
  value: number | null; unit?: string; lowerIsBetter?: boolean;
}) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">—</span>;
  }
  const isBetter = lowerIsBetter ? value < 0 : value > 0;
  const isNeutral = value === 0;
  return (
    <span className={`flex items-center gap-1 font-semibold text-sm ${
      isNeutral ? 'text-muted-foreground' :
      isBetter ? 'text-green-400' : 'text-red-400'
    }`}>
      {isNeutral ? <Minus size={12} /> : isBetter ? <TrendingDown size={12} /> : <TrendingUp size={12} />}
      {value > 0 ? '+' : ''}{value.toFixed(1)}{unit}
    </span>
  );
}

function CompareSection({ scenarios }: { scenarios: ScheduleScenario[] }) {
  const [compareA, setCompareA] = useState('');
  const [compareB, setCompareB] = useState('');
  const [result, setResult] = useState<ScenarioComparisonResult | null>(null);
  const [aiText, setAiText] = useState<string | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [isAiLoading, setIsAiLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCompare() {
    if (!compareA || !compareB || compareA === compareB) return;
    setIsComparing(true);
    setError(null);
    setResult(null);
    setAiText(null);
    try {
      const { data } = await apiClient.post<ScenarioComparisonResult>('/api/scenarios/compare', {
        scenario_a_id: compareA,
        scenario_b_id: compareB,
      });
      setResult(data);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      setError(err.response?.data?.detail || err.message || 'Errore nel confronto');
    } finally {
      setIsComparing(false);
    }
  }

  async function handleAICompare() {
    if (!compareA || !compareB) return;
    setIsAiLoading(true);
    try {
      const { data } = await apiClient.post<{ recommendation: string }>('/api/ai/compare-scenarios', {
        scenario_a_id: compareA,
        scenario_b_id: compareB,
        machine_order_id: scenarios.find((s) => s.id === compareA)?.machine_order_id,
      });
      setAiText(data.recommendation);
    } catch {
      setAiText('Errore nell\'analisi AI. Verificare che il servizio AI sia disponibile.');
    } finally {
      setIsAiLoading(false);
    }
  }

  const scA = scenarios.find((s) => s.id === compareA);
  const scB = scenarios.find((s) => s.id === compareB);

  return (
    <section className="border border-border rounded-xl p-5">
      <div className="flex items-center gap-2 mb-4">
        <ArrowUpDown size={16} className="text-primary" />
        <h2 className="text-sm font-bold">Confronto Scenari</h2>
      </div>

      <div className="flex flex-wrap gap-3 items-end mb-4">
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Scenario A (riferimento)</label>
          <select
            value={compareA}
            onChange={(e) => setCompareA(e.target.value)}
            className="border border-border rounded px-2 py-1.5 text-sm bg-background min-w-[200px]"
          >
            <option value="">Seleziona scenario A…</option>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-muted-foreground mb-1">Scenario B (da confrontare)</label>
          <select
            value={compareB}
            onChange={(e) => setCompareB(e.target.value)}
            className="border border-border rounded px-2 py-1.5 text-sm bg-background min-w-[200px]"
          >
            <option value="">Seleziona scenario B…</option>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>

        {compareA && compareB && (
          <div className="w-full text-xs text-amber-200">
            {(!scA?.last_run_status || scA.last_run_status === 'INFEASIBLE') && (
              <p>⚠ Scenario A non ha una schedulazione valida. Eseguire prima lo scheduling.</p>
            )}
            {(!scB?.last_run_status || scB.last_run_status === 'INFEASIBLE') && (
              <p>⚠ Scenario B non ha una schedulazione valida. Eseguire prima lo scheduling.</p>
            )}
          </div>
        )}
        
        <button
          onClick={handleCompare}
          disabled={!compareA || !compareB || compareA === compareB || isComparing}
          className="px-4 py-1.5 bg-primary text-primary-foreground rounded text-sm font-medium disabled:opacity-50 flex items-center gap-1.5"
        >
          {isComparing && <Loader2 size={12} className="animate-spin" />}
          {isComparing ? 'Confronto…' : 'Confronta'}
        </button>
      </div>

      {error && (
        <div className="border border-red-600/30 bg-red-900/20 rounded p-3 text-red-300 text-sm mb-4">
          ⚠ {error}
          <p className="text-xs mt-1 text-red-400">
            Assicurati che entrambi gli scenari abbiano eseguito lo scheduling (abbiano schedule entries).
          </p>
        </div>
      )}

      {result && (
        <div className="space-y-4">
          {/* Delta KPI */}
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: 'Δ Durata (giorni)', value: result.delta_makespan_days, unit: 'gg', lowerIsBetter: true,
                tooltip: 'Negativo = B è più veloce (meglio). Positivo = B è più lento.' },
              { label: 'Δ Operatori', value: result.delta_operators, unit: '', lowerIsBetter: true,
                tooltip: 'Negativo = B usa meno operatori (meglio per MINIMIZE_OPERATORS).' },
              { label: 'Δ Utilizzo %', value: result.delta_utilization, unit: '%', lowerIsBetter: false,
                tooltip: 'Positivo = B ha operatori più occupati (meglio per MAXIMIZE_UTILIZATION).' },
            ].map((kpi) => (
              <div key={kpi.label} className="border border-border rounded-lg p-3" title={kpi.tooltip}>
                <div className="text-xs text-muted-foreground mb-1">{kpi.label}</div>
                <DeltaCell value={kpi.value} unit={kpi.unit} lowerIsBetter={kpi.lowerIsBetter} />
              </div>
            ))}
          </div>

          {/* Tabella comparativa */}
          <table className="w-full text-xs border border-border rounded overflow-hidden">
            <thead className="bg-muted">
              <tr>
                <th className="text-left py-2 px-3 font-medium">Metrica</th>
                <th className="text-left py-2 px-3 font-medium">{scA?.name || 'A'}</th>
                <th className="text-left py-2 px-3 font-medium">{scB?.name || 'B'}</th>
                <th className="text-left py-2 px-3 font-medium">Delta (B-A)</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-border">
                <td className="py-2 px-3 text-muted-foreground">Operazioni schedulate</td>
                <td className="py-2 px-3 font-semibold">{result.gantt_a.length}</td>
                <td className="py-2 px-3 font-semibold">{result.gantt_b.length}</td>
                <td className="py-2 px-3">
                  <DeltaCell value={result.gantt_b.length - result.gantt_a.length} lowerIsBetter={false} />
                </td>
              </tr>
              <tr className="border-t border-border">
                <td className="py-2 px-3 text-muted-foreground">Makespan (giorni)</td>
                <td className="py-2 px-3 font-semibold">
                  {result.delta_makespan_days !== null
                    ? '—'
                    : '—'}
                </td>
                <td className="py-2 px-3 font-semibold">—</td>
                <td className="py-2 px-3">
                  <DeltaCell value={result.delta_makespan_days} unit="gg" lowerIsBetter={true} />
                </td>
              </tr>
              <tr className="border-t border-border">
                <td className="py-2 px-3 text-muted-foreground">Operatori usati</td>
                <td className="py-2 px-3 font-semibold">—</td>
                <td className="py-2 px-3 font-semibold">—</td>
                <td className="py-2 px-3">
                  <DeltaCell value={result.delta_operators} lowerIsBetter={true} />
                </td>
              </tr>
            </tbody>
          </table>

          {/* AI Analysis */}
          <div className="border border-border rounded-lg p-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold">Analisi AI (opzionale)</span>
              <button
                onClick={handleAICompare}
                disabled={isAiLoading}
                className="text-xs px-3 py-1 bg-violet-900/40 text-violet-300 border border-violet-700/40 rounded hover:bg-violet-900/60 disabled:opacity-50 flex items-center gap-1.5"
              >
                {isAiLoading && <Loader2 size={10} className="animate-spin" />}
                {isAiLoading ? 'Analisi…' : '✨ Analizza differenze con AI'}
              </button>
            </div>
            {aiText && (
              <p className="text-xs text-muted-foreground leading-relaxed">{aiText}</p>
            )}
            {!aiText && !isAiLoading && (
              <p className="text-xs text-muted-foreground italic">
                Clicca il pulsante per ottenere una raccomandazione AI su quale scenario scegliere.
              </p>
            )}
          </div>
        </div>
      )}


    </section>
  );
}

// ── Pagina principale ─────────────────────────────────────────────────────────

export default function ScenarioManager() {
  const { selectedMachineOrderId } = useMachineStore();
  const { setActiveScenarioId } = useScheduleStore();
  const qc = useQueryClient();

  const { data: scenarios = [], isLoading } = useScenarios();
  const machineScenarios = scenarios.filter(
    (s) => !selectedMachineOrderId || s.machine_order_id === selectedMachineOrderId
  );

  const [showCreate, setShowCreate] = useState(false);
  const [showGuide, setShowGuide] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [schedulingId, setSchedulingId] = useState<string | null>(null);
  const [schedulingTaskId, setSchedulingTaskId] = useState<string | null>(null);
  const [lastRunResult, setLastRunResult] = useState<TaskResult | null>(null);

  const { startPolling } = useTaskPoller(schedulingTaskId, (result) => {
    setSchedulingId(null);
    setSchedulingTaskId(null);
    if (result) setLastRunResult(result);
    qc.invalidateQueries({ queryKey: ['scenarios'] });
    qc.invalidateQueries({ queryKey: ['gantt'] });
  });

  const scheduleMutation = useMutation({
    mutationFn: async (scenario: ScheduleScenario) => {
      const { data } = await apiClient.post<{ task_id: string }>(`/api/scenarios/${scenario.id}/run`);
      return data.task_id;
    },
    onMutate: (scenario) => setSchedulingId(scenario.id),
    onSuccess: (taskId) => { setSchedulingTaskId(taskId); startPolling(taskId); },
    onError: () => setSchedulingId(null),
  });

  const activateMutation = useMutation({
    mutationFn: (id: string) => apiClient.patch(`/api/scenarios/${id}`, { is_active: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scenarios'] }),
  });

  const baselineMutation = useMutation({
    mutationFn: (id: string) => apiClient.patch(`/api/scenarios/${id}`, { is_baseline: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scenarios'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiClient.delete(`/api/scenarios/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scenarios'] }),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Caricamento scenari…
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-6 space-y-6">
      {showCreate && selectedMachineOrderId && (
        <NewScenarioModal
          machineOrderId={selectedMachineOrderId}
          onClose={() => setShowCreate(false)}
        />
      )}
      {showGuide && <ScenarioGuideModal onClose={() => setShowGuide(false)} />}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold">Scenario Manager</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Crea e confronta piani di scheduling alternativi per la macchina
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowGuide(true)}
            className="flex items-center gap-1.5 border border-border rounded-lg px-3 py-1.5 text-sm hover:bg-accent"
          >
            <Info size={14} /> Come funziona
          </button>
          <button
            onClick={() => setShowCreate(true)}
            disabled={!selectedMachineOrderId}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm hover:opacity-90 disabled:opacity-50"
          >
            <Plus size={14} /> Nuovo Scenario
          </button>
        </div>
      </div>

      {!selectedMachineOrderId && (
        <div className="border border-amber-600/30 bg-amber-900/10 rounded-lg p-4 text-amber-200 text-sm">
          Seleziona un machine order dal menu in alto per gestire i suoi scenari.
        </div>
      )}

      {/* Griglia scenari */}
      {machineScenarios.length === 0 && selectedMachineOrderId ? (
        <div className="border border-dashed border-border rounded-xl p-12 text-center text-muted-foreground text-sm">
          <p className="mb-3">Nessuno scenario. Crea il primo con il pulsante in alto a destra.</p>
          <button
            onClick={() => setShowGuide(true)}
            className="text-primary underline text-xs"
          >
            Scopri come funzionano gli scenari →
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {machineScenarios.map((sc) => (
            <ScenarioCard
              key={sc.id}
              scenario={sc}
              isSelected={selectedId === sc.id}
              isScheduling={schedulingId === sc.id}
              onSelect={() => { setSelectedId(sc.id); setActiveScenarioId(sc.id); }}
              onActivate={() => activateMutation.mutate(sc.id)}
              onBaseline={() => baselineMutation.mutate(sc.id)}
              onDelete={() => deleteMutation.mutate(sc.id)}
              onSchedule={() => scheduleMutation.mutate(sc)}
            />
            
          ))}
          
        </div>
      )}
      
        
      {/* Banner risultato scheduling */}
      {lastRunResult && lastRunResult.solver_status === 'INFEASIBLE' && (
        <div className="border border-red-600/40 bg-red-950/30 rounded-xl p-5">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 text-red-400">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" />
              </svg>
            </div>
            <div className="flex-1">
              <h3 className="text-sm font-bold text-red-300">Soluzione non fattibile</h3>
              <p className="text-xs text-red-400/80 mt-1 leading-relaxed">
                Il solver CP-SAT non ha trovato una schedulazione valida con i vincoli attuali.
                Cause possibili: troppi vincoli di precedenza, operatori insufficienti per il workcenter,
                finestra temporale troppo stretta, o componenti mancanti che bloccano troppe operazioni.
              </p>
              {lastRunResult.conflicts && lastRunResult.conflicts.length > 0 && (
                <div className="mt-3 space-y-1">
                  <p className="text-xs font-medium text-red-300">Conflitti rilevati:</p>
                  {lastRunResult.conflicts.map((c, i) => (
                    <p key={i} className="text-xs text-red-400/70 pl-3 border-l-2 border-red-800">
                      {c}
                    </p>
                  ))}
                </div>
              )}
              <button
                onClick={() => setLastRunResult(null)}
                className="mt-3 text-xs text-red-400 underline hover:text-red-300"
              >
                Chiudi
              </button>
            </div>
          </div>
        </div>
      )}
 
      {lastRunResult && (lastRunResult.solver_status === 'OPTIMAL' || lastRunResult.solver_status === 'FEASIBLE') && (
        <div className="border border-green-600/40 bg-green-950/10 rounded-xl p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <CheckCircle size={16} className="text-green-400" />
              <p className="text-sm font-semibold text-green-300">
                Scheduling {lastRunResult.solver_status === 'OPTIMAL' ? 'ottimale' : 'fattibile'} completato
              </p>
            </div>
            <button onClick={() => setLastRunResult(null)} className="text-muted-foreground hover:text-foreground text-sm">✕</button>
          </div>
          {lastRunResult.summary ? (
            <SchedulingReport summary={lastRunResult.summary} />
          ) : (
            <p className="text-xs text-green-400/70">
              {lastRunResult.makespan_days != null && `Durata: ${lastRunResult.makespan_days} giorni`}
              {lastRunResult.operators_used != null && ` · ${lastRunResult.operators_used} operatori`}
            </p>
          )}
        </div>
      )}

      {/* Confronto scenari */}
      {machineScenarios.length >= 2 && (
        <CompareSection scenarios={machineScenarios} />
      )}
    </div>
  );
}