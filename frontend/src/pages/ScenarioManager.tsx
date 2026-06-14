import { useState, useEffect } from 'react';
import { useScenarios } from '../api/hooks/useSchedule';
import { useMachineStore } from '../store/machineStore';
import { useScheduleStore } from '../store/scheduleStore';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import type { ObjectiveMode, ScheduleScenario, ScenarioComparisonResult } from '../api/types';
import { Plus, X, TrendingUp, TrendingDown, Minus, Bot, Loader2, Play } from 'lucide-react';

// ── Polling hook for task status ──────────────────────────────────────────────

function useTaskPoller(taskId: string | null, onComplete: () => void) {
  useEffect(() => {
    if (!taskId) return;
    const interval = setInterval(async () => {
      try {
        const { data } = await apiClient.get<{ status: string }>(`/api/schedule/task/${taskId}`);
        if (data.status === 'SUCCESS' || data.status === 'FAILURE') {
          clearInterval(interval);
          onComplete();
        }
      } catch {
        clearInterval(interval);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [taskId, onComplete]);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const OBJECTIVE_LABELS: Record<ObjectiveMode, string> = {
  FINISH_BY_DATE:               'Finisci entro il',
  MAXIMIZE_RESOURCE_UTILIZATION: 'Massimizza utilizzo risorse',
  MINIMIZE_OPERATORS:           'Minimizza operatori',
  CUSTOM:                       'Personalizzato',
};

function DeltaCell({ value, unit = '' }: { value: number | null | undefined; unit?: string }) {
  if (value == null) return <td className="px-2 py-1 text-muted-foreground text-center">—</td>;
  const isPos = value > 0;
  const isNeg = value < 0;
  return (
    <td className={`px-2 py-1 text-center font-medium ${isPos ? 'text-red-600' : isNeg ? 'text-green-600' : ''}`}>
      <span className="flex items-center justify-center gap-0.5">
        {isPos ? <TrendingUp size={12} /> : isNeg ? <TrendingDown size={12} /> : <Minus size={12} />}
        {Math.abs(value).toFixed(2)}{unit}
      </span>
    </td>
  );
}

// ── New Scenario Modal ────────────────────────────────────────────────────────

interface NewScenarioModalProps {
  machineOrderId: string;
  onClose: () => void;
  onCreated: () => void;
}

function NewScenarioModal({ machineOrderId, onClose, onCreated }: NewScenarioModalProps) {
  const [name, setName]           = useState('');
  const [objective, setObjective] = useState<ObjectiveMode>('FINISH_BY_DATE');
  const [targetDate, setTargetDate] = useState('');
  const [taskId, setTaskId]       = useState<string | null>(null);
  const [status, setStatus]       = useState<'idle' | 'creating' | 'scheduling' | 'done'>('idle');

  useTaskPoller(taskId, () => {
    setStatus('done');
    onCreated();
  });

  const qc = useQueryClient();

  const createMutation = useMutation({
    mutationFn: async () => {
      const { data: scenario } = await apiClient.post<ScheduleScenario>('/api/scenarios', {
        name,
        machine_order_id: machineOrderId,
        objective_mode: objective,
        target_finish_date: objective === 'FINISH_BY_DATE' ? targetDate || null : null,
        resource_set_json: {},
        is_active: false,
        is_baseline: false,
      });
      setStatus('scheduling');
      const { data: run } = await apiClient.post<{ task_id: string }>(`/api/scenarios/${scenario.id}/run`);
      return run.task_id;
    },
    onSuccess: (tid) => {
      qc.invalidateQueries({ queryKey: ['scenarios'] });
      setTaskId(tid);
      setStatus('scheduling');
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-card border border-border rounded-xl shadow-2xl p-6 w-[440px] text-sm">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-base font-semibold">Nuovo Scenario</h2>
          <button onClick={onClose}><X size={16} /></button>
        </div>

        {status === 'done' ? (
          <div className="text-center py-6">
            <p className="text-green-600 font-semibold">✓ Scenario creato e schedulato!</p>
            <button onClick={onClose} className="mt-4 px-4 py-1.5 bg-primary text-primary-foreground rounded text-sm">
              Chiudi
            </button>
          </div>
        ) : status === 'scheduling' ? (
          <div className="flex flex-col items-center py-8 gap-3 text-muted-foreground">
            <Loader2 className="animate-spin" size={28} />
            <p className="text-sm">Schedulazione in corso…</p>
          </div>
        ) : (
          <>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium mb-1">Nome scenario</label>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Es. Scenario Ottimizzato"
                  className="w-full border border-border rounded px-2 py-1.5 bg-background"
                />
              </div>

              <div>
                <label className="block text-xs font-medium mb-1.5">Obiettivo</label>
                <div className="space-y-1.5">
                  {(['FINISH_BY_DATE', 'MAXIMIZE_RESOURCE_UTILIZATION', 'MINIMIZE_OPERATORS', 'CUSTOM'] as ObjectiveMode[]).map((obj) => (
                    <label key={obj} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="objective"
                        value={obj}
                        checked={objective === obj}
                        onChange={() => setObjective(obj)}
                      />
                      <span className="text-xs">{OBJECTIVE_LABELS[obj]}</span>
                      {obj === 'FINISH_BY_DATE' && objective === 'FINISH_BY_DATE' && (
                        <input
                          type="date"
                          value={targetDate}
                          onChange={(e) => setTargetDate(e.target.value)}
                          className="border border-border rounded px-1.5 py-0.5 text-xs bg-background ml-1"
                        />
                      )}
                    </label>
                  ))}
                </div>
              </div>
            </div>

            {createMutation.isError && (
              <p className="text-xs text-destructive mt-2">Errore nella creazione.</p>
            )}

            <div className="flex gap-2 mt-5">
              <button onClick={onClose} className="flex-1 py-1.5 border border-border rounded hover:bg-accent">
                Annulla
              </button>
              <button
                onClick={() => { setStatus('creating'); createMutation.mutate(); }}
                disabled={!name.trim() || createMutation.isPending}
                className="flex-1 py-1.5 bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50"
              >
                Crea e Schedula
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Scenario Card ─────────────────────────────────────────────────────────────

interface ScenarioCardProps {
  scenario: ScheduleScenario;
  isSelected: boolean;
  isScheduling: boolean;
  onSelect: () => void;
  onActivate: () => void;
  onBaseline: () => void;
  onDelete: () => void;
  onSchedule: () => void;
}

function ScenarioCard({ scenario, isSelected, isScheduling, onSelect, onActivate, onBaseline, onDelete, onSchedule }: ScenarioCardProps) {
  return (
    <div
      onClick={onSelect}
      className={`border rounded-xl p-4 cursor-pointer transition-all hover:shadow-md
        ${isSelected ? 'border-primary ring-2 ring-primary/20' : 'border-border'}
      `}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-2">
        <div>
          <p className="font-semibold text-sm">{scenario.name}</p>
          <p className="text-xs text-muted-foreground">
            {OBJECTIVE_LABELS[scenario.objective_mode]} ·{' '}
            {new Date(scenario.created_at).toLocaleDateString('it-IT')}
          </p>
        </div>
        <div className="flex gap-1">
          {scenario.is_active   && <span className="text-[10px] bg-green-100 text-green-700 rounded px-1.5">ACTIVE</span>}
          {scenario.is_baseline && <span className="text-[10px] bg-blue-100 text-blue-700 rounded px-1.5">BASELINE</span>}
        </div>
      </div>

      {/* Schedula — azione primaria */}
      <button
        onClick={(e) => { e.stopPropagation(); onSchedule(); }}
        disabled={isScheduling}
        className="w-full flex items-center justify-center gap-1.5 mt-3 py-1.5 bg-primary text-primary-foreground rounded text-xs font-medium hover:opacity-90 disabled:opacity-60"
      >
        {isScheduling ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
        {isScheduling ? 'Scheduling in corso…' : 'Schedula'}
      </button>

      {/* Actions secondarie */}
      <div className="flex gap-1.5 mt-2">
        <button
          onClick={(e) => { e.stopPropagation(); onActivate(); }}
          className="text-xs px-2 py-0.5 border border-border rounded hover:bg-accent"
        >
          Attiva
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onBaseline(); }}
          className="text-xs px-2 py-0.5 border border-border rounded hover:bg-accent"
        >
          Baseline
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="text-xs px-2 py-0.5 border border-destructive text-destructive rounded hover:bg-red-50 ml-auto"
        >
          Elimina
        </button>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ScenarioManager() {
  const { selectedMachineOrderId } = useMachineStore();
  const { setActiveScenarioId }    = useScheduleStore();
  const qc = useQueryClient();

  const { data: scenarios = [], isLoading } = useScenarios();
  const machineScenarios = scenarios.filter(
    (s) => !selectedMachineOrderId || s.machine_order_id === selectedMachineOrderId
  );

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedId, setSelectedId]           = useState<string | null>(null);
  const [schedulingId, setSchedulingId]       = useState<string | null>(null);
  const [schedulingTaskId, setSchedulingTaskId] = useState<string | null>(null);

  // Polling per lo scheduling avviato da card esistente
  useTaskPoller(schedulingTaskId, () => {
    setSchedulingId(null);
    setSchedulingTaskId(null);
    qc.invalidateQueries({ queryKey: ['scenarios'] });
    qc.invalidateQueries({ queryKey: ['gantt'] });
  });

  const scheduleMutation = useMutation({
    mutationFn: async (scenario: ScheduleScenario) => {
      const { data } = await apiClient.post<{ task_id: string }>(`/api/scenarios/${scenario.id}/run`);
      return data.task_id;
    },
    onMutate: (scenario) => setSchedulingId(scenario.id),
    onSuccess: (taskId) => setSchedulingTaskId(taskId),
    onError: () => setSchedulingId(null),
  });

  // Comparison
  const [compareA, setCompareA] = useState<string>('');
  const [compareB, setCompareB] = useState<string>('');
  const [compResult, setCompResult] = useState<ScenarioComparisonResult | null>(null);
  const [aiCompare, setAiCompare]   = useState<string | null>(null);

  // What-if
  const [whatIfText, setWhatIfText]     = useState('');
  const [whatIfResult, setWhatIfResult] = useState<string | null>(null);
  const [whatIfLoading, setWhatIfLoading] = useState(false);

  // Mutations
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

  const compareMutation = useMutation({
    mutationFn: () =>
      apiClient.post<ScenarioComparisonResult>('/api/scenarios/compare', {
        scenario_a_id: compareA,
        scenario_b_id: compareB,
      }),
    onSuccess: (res) => setCompResult(res.data),
  });

  const aiCompareMutation = useMutation({
    mutationFn: () =>
      apiClient.post<{ recommendation: string }>('/api/ai/compare-scenarios', {
        scenario_a_id: compareA,
        scenario_b_id: compareB,
      }),
    onSuccess: (res) => setAiCompare(res.data.recommendation ?? JSON.stringify(res.data)),
  });

  async function handleWhatIf() {
    if (!whatIfText.trim() || !selectedId) return;
    setWhatIfLoading(true);
    try {
      // Create a temporary scenario and run it
      const { data: tmpScenario } = await apiClient.post<ScheduleScenario>('/api/scenarios', {
        name: `[What-If] ${whatIfText.slice(0, 40)}`,
        machine_order_id: selectedMachineOrderId,
        objective_mode: 'FINISH_BY_DATE',
        resource_set_json: {},
        is_active: false,
        is_baseline: false,
      });
      const { data: run } = await apiClient.post<{ task_id: string }>(`/api/scenarios/${tmpScenario.id}/run`);
      setWhatIfResult(`Task avviato (ID: ${run.task_id}). Scenario temporaneo: ${tmpScenario.name}`);
    } catch {
      setWhatIfResult('Errore nella simulazione what-if.');
    } finally {
      setWhatIfLoading(false);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Caricamento scenari…
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-6 space-y-6">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Scenario Manager</h1>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm hover:opacity-90"
        >
          <Plus size={14} /> Nuovo Scenario
        </button>
      </div>

      {/* ── Scenario cards ────────────────────────────────────────── */}
      {machineScenarios.length === 0 ? (
        <div className="border border-dashed border-border rounded-xl p-12 text-center text-muted-foreground text-sm">
          Nessuno scenario. Crea il primo scenario con il pulsante sopra.
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

      {/* ── Comparison section ──────────────────────────────────── */}
      {machineScenarios.length >= 2 && (
        <section className="border border-border rounded-xl p-4">
          <h2 className="text-sm font-semibold mb-3">Confronto Scenari</h2>

          <div className="flex flex-wrap gap-2 items-end mb-3">
            <div>
              <label className="block text-xs mb-0.5">Scenario A</label>
              <select
                value={compareA}
                onChange={(e) => setCompareA(e.target.value)}
                className="border border-border rounded px-2 py-1 text-sm bg-background"
              >
                <option value="">Seleziona…</option>
                {machineScenarios.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs mb-0.5">Scenario B</label>
              <select
                value={compareB}
                onChange={(e) => setCompareB(e.target.value)}
                className="border border-border rounded px-2 py-1 text-sm bg-background"
              >
                <option value="">Seleziona…</option>
                {machineScenarios.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <button
              onClick={() => compareMutation.mutate()}
              disabled={!compareA || !compareB || compareA === compareB || compareMutation.isPending}
              className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-sm disabled:opacity-50"
            >
              {compareMutation.isPending ? 'Confronto…' : 'Confronta'}
            </button>
          </div>

          {compResult && (
            <div className="space-y-3">
              {/* KPI delta table */}
              <table className="w-full text-xs border border-border rounded overflow-hidden">
                <thead className="bg-muted">
                  <tr>
                    <th className="text-left px-2 py-1.5">KPI</th>
                    <th className="text-center px-2 py-1.5">Delta (A−B)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr>
                    <td className="px-2 py-1">Makespan (giorni)</td>
                    <DeltaCell value={compResult.delta_makespan_days} />
                  </tr>
                  <tr>
                    <td className="px-2 py-1">Operatori usati</td>
                    <DeltaCell value={compResult.delta_operators} />
                  </tr>
                  <tr>
                    <td className="px-2 py-1">Utilizzo risorse (%)</td>
                    <DeltaCell value={
                      compResult.delta_utilization != null
                        ? compResult.delta_utilization * 100
                        : null
                    } unit="%" />
                  </tr>
                </tbody>
              </table>

              {/* AI comparison */}
              <div>
                <button
                  onClick={() => aiCompareMutation.mutate()}
                  disabled={aiCompareMutation.isPending}
                  className="flex items-center gap-1.5 text-xs border border-border rounded px-2 py-1 hover:bg-accent disabled:opacity-50"
                >
                  <Bot size={12} />
                  {aiCompareMutation.isPending ? 'Analisi AI…' : 'Analizza differenze con AI'}
                </button>
                {aiCompare && (
                  <div className="mt-2 p-3 rounded bg-muted text-xs whitespace-pre-wrap">
                    {aiCompare}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      )}

      {/* ── What-if section ──────────────────────────────────────── */}
      {selectedId && (
        <section className="border border-border rounded-xl p-4">
          <h2 className="text-sm font-semibold mb-3">Simulazione What-If</h2>
          <div className="flex gap-2">
            <input
              value={whatIfText}
              onChange={(e) => setWhatIfText(e.target.value)}
              placeholder="Se aggiungo N operatori ELECTRICAL al workcenter WC-BERGAMO…"
              className="flex-1 border border-border rounded px-2 py-1.5 text-sm bg-background"
            />
            <button
              onClick={handleWhatIf}
              disabled={!whatIfText.trim() || whatIfLoading}
              className="px-3 py-1.5 bg-primary text-primary-foreground rounded text-sm disabled:opacity-50 flex items-center gap-1"
            >
              {whatIfLoading && <Loader2 size={12} className="animate-spin" />}
              Stima impatto
            </button>
          </div>
          {whatIfResult && (
            <p className="mt-2 text-xs text-muted-foreground bg-muted rounded p-2">{whatIfResult}</p>
          )}
        </section>
      )}

      {/* Create modal */}
      {showCreateModal && selectedMachineOrderId && (
        <NewScenarioModal
          machineOrderId={selectedMachineOrderId}
          onClose={() => setShowCreateModal(false)}
          onCreated={() => {
            setShowCreateModal(false);
            qc.invalidateQueries({ queryKey: ['scenarios'] });
          }}
        />
      )}
    </div>
  );
}

