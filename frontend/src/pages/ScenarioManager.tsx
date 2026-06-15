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
import type { ScheduleScenario } from '../api/types';
import {
  Play, Plus, Loader2, Info, ArrowUpDown, TrendingDown, TrendingUp, Minus,
  CheckCircle, Target, Users, Calendar, Zap,
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
  icon: React.ReactNode;
  color: string;
}> = {
  FINISH_BY_DATE: {
    label: 'Finisci entro data',
    description: 'Il solver privilegia il completamento entro la data target. Utile quando c\'è una consegna ferma al cliente.',
    icon: <Calendar size={14} />,
    color: 'blue',
  },
  MAXIMIZE_RESOURCE_UTILIZATION: {
    label: 'Massimizza utilizzo risorse',
    description: 'Minimizza i tempi morti degli operatori. Utile per massimizzare la produttività dell\'impianto.',
    icon: <Zap size={14} />,
    color: 'green',
  },
  MINIMIZE_OPERATORS: {
    label: 'Minimizza operatori',
    description: 'Usa il minor numero possibile di operatori distinti. Utile per pianificare con risorse limitate.',
    icon: <Users size={14} />,
    color: 'purple',
  },
  CUSTOM: {
    label: 'Personalizzato',
    description: 'Combina i tre obiettivi con pesi personalizzati. Per pianificatori esperti.',
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
        <p className="text-[11px] text-muted-foreground mb-3 leading-relaxed">
          {meta.description}
        </p>
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
    </div>
  );
}

// ── Modal: Guida agli scenari ─────────────────────────────────────────────────

function ScenarioGuideModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-background border border-border rounded-xl p-6 max-w-2xl w-full max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-bold">Guida agli Scenari di Scheduling</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="space-y-5 text-sm">
          <div>
            <h3 className="font-semibold mb-2 text-primary">Cos'è uno Scenario?</h3>
            <p className="text-muted-foreground leading-relaxed">
              Uno scenario è un piano di scheduling completo e indipendente per la macchina.
              Puoi creare più scenari con obiettivi diversi e confrontarli per scegliere il piano migliore
              da rendere "attivo" (quello che diventa il piano ufficiale di produzione).
            </p>
          </div>

          <div>
            <h3 className="font-semibold mb-3">Tipi di Obiettivo</h3>
            <div className="space-y-3">
              {Object.entries(OBJECTIVE_META).map(([key, meta]) => (
                <div key={key} className="flex gap-3 p-3 border border-border rounded-lg">
                  <div className={`mt-0.5 text-${meta.color}-400`}>{meta.icon}</div>
                  <div>
                    <p className="font-medium text-sm">{meta.label}</p>
                    <p className="text-muted-foreground text-xs mt-0.5 leading-relaxed">{meta.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h3 className="font-semibold mb-2">Workflow tipico</h3>
            <ol className="space-y-2 text-muted-foreground text-xs">
              {[
                'Crea 2-3 scenari con obiettivi diversi (es. "Finisci entro 30/8" e "Minimizza operatori")',
                'Esegui lo scheduling su ciascuno — CP-SAT calcola il piano ottimale',
                'Usa "Confronta" per vedere i delta di makespan e numero operatori',
                'Attiva lo scenario migliore → diventa il piano ufficiale',
                'Se cambiano i dati (nuovi ritardi, componenti mancanti) → ri-esegui lo scheduling',
              ].map((step, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-primary font-bold">{i + 1}.</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </div>

          <div className="border border-amber-600/30 bg-amber-900/20 rounded p-3">
            <p className="text-amber-200 text-xs leading-relaxed">
              <strong>Nota:</strong> Solo uno scenario può essere ATTIVO alla volta per macchina.
              Il piano BASELINE è il riferimento di confronto (es. il piano originale approvato)
              e non cambia anche quando si eseguono nuovi scheduling.
            </p>
          </div>
        </div>
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
        className="bg-background border border-border rounded-xl p-6 max-w-lg w-full"
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
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {objective === 'FINISH_BY_DATE' && (
              <div>
                <label className="block text-xs font-medium mb-1">Data target di completamento</label>
                <input
                  type="date"
                  value={targetDate}
                  onChange={(e) => setTargetDate(e.target.value)}
                  className="w-full px-3 py-2 text-sm border border-border rounded bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                />
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
        <div className="border border-green-600/40 bg-green-950/20 rounded-xl p-4">
          <div className="flex items-center gap-3">
            <CheckCircle size={18} className="text-green-400" />
            <div>
              <p className="text-sm font-medium text-green-300">
                Scheduling completato ({lastRunResult.solver_status === 'OPTIMAL' ? 'ottimale' : 'fattibile'})
              </p>
              <p className="text-xs text-green-400/70 mt-0.5">
                {lastRunResult.makespan_days != null && `Durata: ${lastRunResult.makespan_days} giorni`}
                {lastRunResult.operators_used != null && ` · ${lastRunResult.operators_used} operatori`}
              </p>
            </div>
            <button
              onClick={() => setLastRunResult(null)}
              className="ml-auto text-xs text-green-400/60 hover:text-green-300"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Banner risultato ultimo scheduling */}
      {lastRunResult?.solver_status === 'INFEASIBLE' && (
        <div className="border border-red-600/40 bg-red-950/20 rounded-xl p-5">
          <div className="flex items-start gap-3">
            <span className="text-red-400 text-lg mt-0.5">✕</span>
            <div className="flex-1">
              <p className="text-sm font-semibold text-red-300 mb-1">Soluzione non fattibile</p>
              <p className="text-xs text-red-400/80 leading-relaxed">
                Il solver CP-SAT non ha trovato una schedulazione valida con i vincoli attuali.
                Possibili cause: operatori insufficienti nel workcenter, finestra temporale
                troppo stretta, o componenti mancanti che bloccano troppe operazioni.
              </p>
              {lastRunResult.conflicts && lastRunResult.conflicts.length > 0 && (
                <div className="mt-3 space-y-1">
                  <p className="text-xs font-medium text-red-300">Dettagli conflitti:</p>
                  {lastRunResult.conflicts.map((c, i) => (
                    <p key={i} className="text-xs text-red-400/70 pl-3 border-l-2 border-red-800">{c}</p>
                  ))}
                </div>
              )}
            </div>
            <button onClick={() => setLastRunResult(null)} className="text-red-400/60 hover:text-red-300 text-sm">✕</button>
          </div>
        </div>
      )}

      {lastRunResult?.solver_status && ['OPTIMAL', 'FEASIBLE'].includes(lastRunResult.solver_status) && (
        <div className="border border-green-600/40 bg-green-950/10 rounded-xl p-4 flex items-center gap-3">
          <CheckCircle size={18} className="text-green-400 shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-medium text-green-300">
              Scheduling {lastRunResult.solver_status === 'OPTIMAL' ? 'ottimale' : 'fattibile'} completato
            </p>
            <p className="text-xs text-green-400/70 mt-0.5">
              {lastRunResult.makespan_days != null && `Durata: ${lastRunResult.makespan_days} giorni`}
              {lastRunResult.operators_used != null && ` · ${lastRunResult.operators_used} operatori utilizzati`}
            </p>
          </div>
          <button onClick={() => setLastRunResult(null)} className="text-green-400/60 hover:text-green-300 text-sm">✕</button>
        </div>
      )}

      {/* Confronto scenari */}
      {machineScenarios.length >= 2 && (
        <CompareSection scenarios={machineScenarios} />
      )}
    </div>
  );
}