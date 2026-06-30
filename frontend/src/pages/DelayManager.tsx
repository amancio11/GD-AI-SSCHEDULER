import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useOperators } from '../api/hooks/useOperators';
import { useMachineStore } from '../store/machineStore';
import { useScheduleStore } from '../store/scheduleStore';
import { triggerToast } from '../hooks/useToast';
import type { DelayEvent, DelayEventType, DelayImpactResponse } from '../api/types';
import { Plus, X, AlertTriangle, Bot, CheckCircle2 } from 'lucide-react';

interface OperationFlat {
  operation_id: string;
  operation_description: string;
  production_order_material: string;
  entry_status: string;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const URGENCY_COLORS = {
  CRITICAL: 'bg-red-100 text-red-700 border-red-300',
  HIGH:     'bg-orange-100 text-orange-700 border-orange-300',
  MEDIUM:   'bg-yellow-100 text-yellow-700 border-yellow-300',
  LOW:      'bg-gray-100 text-gray-600 border-gray-300',
};

function urgencyForDelay(d: DelayEvent): keyof typeof URGENCY_COLORS {
  const now = Date.now();
  const until = new Date(d.delay_until).getTime();
  const diff = (until - now) / 86400_000; // days
  if (diff < 0) return 'LOW';
  if (diff < 1) return 'CRITICAL';
  if (diff < 3) return 'HIGH';
  if (diff < 7) return 'MEDIUM';
  return 'LOW';
}

const TYPE_LABELS: Record<DelayEventType, string> = {
  OPERATOR_ABSENCE:       'Assenza Operatore',
  COMPONENT_DELAY:        'Componente in Ritardo',
  MANUAL_OPERATION_DELAY: 'Operazione Ritardata',
  OTHER:                  'Altro',
};

// ── New Delay Modal ───────────────────────────────────────────────────────────

interface NewDelayModalProps {
  machineOrderId: string;
  activeScenarioId: string | null;
  onClose: () => void;
}

function NewDelayModal({ machineOrderId, activeScenarioId, onClose }: NewDelayModalProps) {
  const qc = useQueryClient();
  const { data: operators = [] } = useOperators();
  const now = new Date().toISOString().slice(0, 16);

  const [type, setType]         = useState<DelayEventType>('OPERATOR_ABSENCE');
  const [from, setFrom]         = useState(now);
  const [until, setUntil]       = useState(now);
  const [desc, setDesc]         = useState('');
  const [entityId, setEntityId] = useState('');
  const [reschedule, setReschedule] = useState(true);

  // Carica operazioni dallo scenario attivo quando il tipo è MANUAL_OPERATION_DELAY
  const { data: operationsFlat = [], isLoading: opsLoading } = useQuery<OperationFlat[]>({
    queryKey: ['operations-flat', activeScenarioId],
    queryFn: async () => {
      const { data } = await apiClient.get<OperationFlat[]>(
        `/api/gantt/${activeScenarioId}/operations-flat`
      );
      return data;
    },
    enabled: type === 'MANUAL_OPERATION_DELAY' && !!activeScenarioId,
  });

  // Deduplicata per operation_id (una stessa op può avere più entries)
  const uniqueOps = operationsFlat.filter(
    (op, idx, arr) => arr.findIndex((o) => o.operation_id === op.operation_id) === idx
  );

  const affectedEntityType =
    type === 'OPERATOR_ABSENCE' ? 'operator'
    : type === 'MANUAL_OPERATION_DELAY' ? 'operation'
    : null;

  const createMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/api/delays', {
        machine_order_id: machineOrderId,
        event_type: type,
        delay_from: new Date(from).toISOString(),
        delay_until: new Date(until).toISOString(),
        description: desc,
        reported_at: new Date().toISOString(),
        requires_reschedule: reschedule,
        affected_entity_id: entityId || null,
        affected_entity_type: entityId ? affectedEntityType : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['delays', machineOrderId] });
      triggerToast('Ritardo registrato con successo.');
      onClose();
    },
  });

  // Reset entity selection when type changes
  function handleTypeChange(t: DelayEventType) {
    setType(t);
    setEntityId('');
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-card border border-border rounded-xl shadow-2xl p-6 w-[480px] text-sm">
        <div className="flex justify-between mb-4">
          <h2 className="text-base font-semibold">Nuovo Ritardo</h2>
          <button onClick={onClose}><X size={16} /></button>
        </div>

        <div className="space-y-3">
          {/* Type */}
          <div>
            <label className="block text-xs font-medium mb-1">Tipo</label>
            <div className="space-y-1">
              {(['OPERATOR_ABSENCE', 'COMPONENT_DELAY', 'MANUAL_OPERATION_DELAY', 'OTHER'] as DelayEventType[]).map((t) => (
                <label key={t} className="flex items-center gap-2 text-xs cursor-pointer">
                  <input type="radio" name="type" value={t} checked={type === t} onChange={() => handleTypeChange(t)} />
                  {TYPE_LABELS[t]}
                </label>
              ))}
            </div>
          </div>

          {/* Operator selector (OPERATOR_ABSENCE) */}
          {type === 'OPERATOR_ABSENCE' && (
            <div>
              <label className="block text-xs font-medium mb-1">Operatore</label>
              <select
                value={entityId}
                onChange={(e) => setEntityId(e.target.value)}
                className="w-full border border-border rounded px-2 py-1.5 bg-background text-xs"
              >
                <option value="">Seleziona operatore…</option>
                {operators.map((op) => (
                  <option key={op.id} value={op.id}>{op.full_name} ({op.skill})</option>
                ))}
              </select>
            </div>
          )}

          {/* Operation selector (MANUAL_OPERATION_DELAY) */}
          {type === 'MANUAL_OPERATION_DELAY' && (
            <div>
              <label className="block text-xs font-medium mb-1">Operazione</label>
              {!activeScenarioId ? (
                <p className="text-xs text-amber-500 bg-amber-500/10 border border-amber-500/30 rounded px-2 py-1.5">
                  Nessuno scenario attivo. Attiva uno scenario prima di registrare un ritardo su un'operazione.
                </p>
              ) : opsLoading ? (
                <p className="text-xs text-muted-foreground">Caricamento operazioni…</p>
              ) : uniqueOps.length === 0 ? (
                <p className="text-xs text-muted-foreground">Nessuna operazione schedulata nello scenario attivo.</p>
              ) : (
                <select
                  value={entityId}
                  onChange={(e) => setEntityId(e.target.value)}
                  className="w-full border border-border rounded px-2 py-1.5 bg-background text-xs"
                >
                  <option value="">Seleziona operazione…</option>
                  {uniqueOps.map((op) => (
                    <option key={op.operation_id} value={op.operation_id}>
                      [{op.production_order_material}] {op.operation_description} — {op.entry_status}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* Date range */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-xs font-medium mb-1">Dal</label>
              <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)}
                className="w-full border border-border rounded px-2 py-1 bg-background text-xs" />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1">Al</label>
              <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)}
                className="w-full border border-border rounded px-2 py-1 bg-background text-xs" />
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-medium mb-1">Descrizione</label>
            <textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={2}
              className="w-full border border-border rounded px-2 py-1.5 bg-background resize-none text-xs"
            />
          </div>

          {/* Reschedule checkbox */}
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={reschedule} onChange={(e) => setReschedule(e.target.checked)} />
            Richiede rischedulazione
          </label>
        </div>

        {createMutation.isError && (
          <p className="text-xs text-destructive mt-2">Errore nella creazione del ritardo.</p>
        )}

        <div className="flex gap-2 mt-4">
          <button onClick={onClose} className="flex-1 py-1.5 border border-border rounded hover:bg-accent text-xs">Annulla</button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="flex-1 py-1.5 bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50 text-xs"
          >
            {createMutation.isPending ? 'Creando…' : 'Crea ritardo'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Impact Modal ──────────────────────────────────────────────────────────────

function ImpactModal({ delay, scenarioId, onClose }: { delay: DelayEvent; scenarioId: string | null; onClose: () => void }) {
  const { data: impact, isLoading } = useQuery<DelayImpactResponse>({
    queryKey: ['delay-impact', delay.id],
    queryFn: async () => {
      const { data } = await apiClient.get<DelayImpactResponse>(`/api/delays/${delay.id}/impact`);
      return data;
    },
  });

  const [aiResp, setAiResp]       = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  const qc = useQueryClient();
  const rescheduleMutation = useMutation({
    mutationFn: () => apiClient.post(`/api/schedule/scenario/${scenarioId}/reschedule`),
    onSuccess: () => {
      triggerToast('Rischedulazione avviata.');
      qc.invalidateQueries({ queryKey: ['gantt', scenarioId] });
    },
  });

  async function analyzeWithAI() {
    setAiLoading(true);
    try {
      const { data } = await apiClient.post<{ summary: string }>('/api/ai/analyze-delay', { delay_id: delay.id });
      setAiResp(data.summary ?? JSON.stringify(data));
    } finally {
      setAiLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-card border border-border rounded-xl shadow-2xl p-6 w-[520px] text-sm max-h-[80vh] overflow-y-auto">
        <div className="flex justify-between mb-4">
          <h2 className="text-base font-semibold">Impatto Ritardo</h2>
          <button onClick={onClose}><X size={16} /></button>
        </div>

        {isLoading ? (
          <p className="text-muted-foreground">Caricamento…</p>
        ) : impact ? (
          <div className="space-y-3">
            {impact.critical_path_affected && (
              <div className="flex items-center gap-2 p-2 bg-red-50 border border-red-200 rounded text-red-700 text-xs">
                <AlertTriangle size={14} />
                Il critical path è coinvolto!
              </div>
            )}

            <p className="text-xs">
              <span className="font-medium">Delta stimato:</span> {impact.estimated_delta_days.toFixed(1)} giorni
            </p>

            <div>
              <p className="text-xs font-medium mb-1">Operazioni impattate ({impact.impacted_entries.length}):</p>
              <ul className="space-y-0.5 max-h-32 overflow-y-auto">
                {impact.impacted_entries.slice(0, 20).map((e) => (
                  <li key={e.id} className="text-[10px] text-muted-foreground">
                    {e.operation_id} — {e.status}
                  </li>
                ))}
              </ul>
            </div>

            {aiResp && (
              <div className="p-2 bg-muted rounded text-xs whitespace-pre-wrap">{aiResp}</div>
            )}

            <div className="flex gap-2 flex-wrap">
              {scenarioId && (
                <button
                  onClick={() => rescheduleMutation.mutate()}
                  disabled={rescheduleMutation.isPending}
                  className="text-xs px-2 py-1 border border-border rounded hover:bg-accent disabled:opacity-50"
                >
                  Rischedula
                </button>
              )}
              <button
                onClick={analyzeWithAI}
                disabled={aiLoading}
                className="flex items-center gap-1 text-xs px-2 py-1 border border-border rounded hover:bg-accent disabled:opacity-50"
              >
                <Bot size={12} />
                {aiLoading ? 'Analisi…' : 'Analizza con AI'}
              </button>
            </div>
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">Nessun dato disponibile.</p>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DelayManager() {
  const { selectedMachineOrderId } = useMachineStore();
  const { activeScenarioId }       = useScheduleStore();
  const qc = useQueryClient();

  const [showNew, setShowNew]           = useState(false);
  const [impactDelay, setImpactDelay]   = useState<DelayEvent | null>(null);
  const [filterType, setFilterType]     = useState<DelayEventType | ''>('');
  const [filterResolved, setFilterResolved] = useState(false);

  const { data: delays = [], isLoading } = useQuery<DelayEvent[]>({
    queryKey: ['delays', selectedMachineOrderId],
    queryFn: async () => {
      const { data } = await apiClient.get<DelayEvent[]>(
        `/api/delays/machine/${selectedMachineOrderId}`
      );
      return data;
    },
    enabled: !!selectedMachineOrderId,
  });

  const resolveMutation = useMutation({
    mutationFn: (id: string) => apiClient.patch(`/api/delays/${id}/resolve`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['delays', selectedMachineOrderId] });
      triggerToast('Ritardo risolto.');
    },
  });

  const filtered = delays.filter((d) => {
    if (filterType && d.event_type !== filterType) return false;
    return true;
  });

  if (!selectedMachineOrderId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Seleziona un ordine macchina per visualizzare i ritardi.
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Gestione Ritardi</h1>
        <button
          onClick={() => setShowNew(true)}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm"
        >
          <Plus size={14} /> Nuovo Ritardo
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-2 flex-wrap">
        <select
          value={filterType}
          onChange={(e) => setFilterType(e.target.value as DelayEventType | '')}
          className="border border-border rounded px-2 py-1 text-sm bg-background"
        >
          <option value="">Tutti i tipi</option>
          {Object.entries(TYPE_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      {isLoading ? (
        <p className="text-muted-foreground text-sm">Caricamento…</p>
      ) : filtered.length === 0 ? (
        <div className="border border-dashed border-border rounded-xl p-12 text-center text-muted-foreground text-sm">
          Nessun ritardo attivo.
        </div>
      ) : (
        <table className="w-full text-xs border border-border rounded-lg overflow-hidden">
          <thead className="bg-muted">
            <tr>
              {['Tipo', 'Dal', 'Al', 'Descrizione', 'Urgenza', 'Azioni'].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.map((d) => {
              const urgency = urgencyForDelay(d);
              return (
                <tr key={d.id} className="hover:bg-accent">
                  <td className="px-3 py-2">{TYPE_LABELS[d.event_type]}</td>
                  <td className="px-3 py-2">{new Date(d.delay_from).toLocaleDateString('it-IT')}</td>
                  <td className="px-3 py-2">{new Date(d.delay_until).toLocaleDateString('it-IT')}</td>
                  <td className="px-3 py-2 max-w-[200px] truncate" title={d.description ?? ''}>{d.description ?? '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`border rounded px-1.5 py-0.5 text-[10px] ${URGENCY_COLORS[urgency]}`}>
                      {urgency}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1.5">
                      <button
                        onClick={() => setImpactDelay(d)}
                        className="text-xs px-1.5 py-0.5 border border-border rounded hover:bg-accent"
                      >
                        Impatto
                      </button>
                      <button
                        onClick={() => resolveMutation.mutate(d.id)}
                        className="text-xs px-1.5 py-0.5 border border-green-300 text-green-700 rounded hover:bg-green-50"
                      >
                        <CheckCircle2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {showNew && selectedMachineOrderId && (
        <NewDelayModal
          machineOrderId={selectedMachineOrderId}
          activeScenarioId={activeScenarioId}
          onClose={() => setShowNew(false)}
        />
      )}
      {impactDelay && (
        <ImpactModal delay={impactDelay} scenarioId={activeScenarioId} onClose={() => setImpactDelay(null)} />
      )}
    </div>
  );
}

