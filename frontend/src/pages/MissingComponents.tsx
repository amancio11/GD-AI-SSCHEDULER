import { useState, useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useMissingComponents } from '../api/hooks/useMissing';
import { useMachineStore } from '../store/machineStore';
import { useScheduleStore } from '../store/scheduleStore';
import { triggerToast } from '../hooks/useToast';
import type { MissingComponent } from '../api/types';
import { Plus, X, CheckSquare, Calendar } from 'lucide-react';

// ── Urgency helpers ───────────────────────────────────────────────────────────

type Urgency = 'CRITICO' | 'ALTO' | 'MEDIO' | 'BASSO';

function calcUrgency(arrivalDate: string | null): Urgency {
  if (!arrivalDate) return 'BASSO';
  const days = (new Date(arrivalDate).getTime() - Date.now()) / 86400_000;
  if (days <= 2) return 'CRITICO';
  if (days <= 5) return 'ALTO';
  if (days <= 10) return 'MEDIO';
  return 'BASSO';
}

const URGENCY_COLORS: Record<Urgency, string> = {
  CRITICO: 'bg-red-100 text-red-700',
  ALTO:    'bg-orange-100 text-orange-700',
  MEDIO:   'bg-yellow-100 text-yellow-700',
  BASSO:   'bg-gray-100 text-gray-600',
};

// ── Add Missing Modal ─────────────────────────────────────────────────────────

function AddMissingModal({ machineOrderId, onClose }: { machineOrderId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [material, setMaterial] = useState('');
  const [desc, setDesc]         = useState('');
  const [arrival, setArrival]   = useState('');
  const [orderId, setOrderId]   = useState('');

  const createMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/api/missing-components', {
        production_order_id: orderId,
        component_material: material,
        description: desc,
        expected_arrival_date: arrival || null,
        manually_flagged: true,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['missing-components', machineOrderId] });
      triggerToast('Componente mancante aggiunto.');
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-card border border-border rounded-xl shadow-2xl p-6 w-96 text-sm">
        <div className="flex justify-between mb-4">
          <h2 className="text-base font-semibold">Aggiungi Componente Mancante</h2>
          <button onClick={onClose}><X size={16} /></button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1">Codice Materiale *</label>
            <input value={material} onChange={(e) => setMaterial(e.target.value)}
              placeholder="Es. VLV-2200"
              className="w-full border border-border rounded px-2 py-1.5 bg-background" />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">Descrizione</label>
            <input value={desc} onChange={(e) => setDesc(e.target.value)}
              className="w-full border border-border rounded px-2 py-1.5 bg-background" />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">Production Order ID *</label>
            <input value={orderId} onChange={(e) => setOrderId(e.target.value)}
              placeholder="UUID del gruppo/ordine"
              className="w-full border border-border rounded px-2 py-1.5 bg-background font-mono text-xs" />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">Data arrivo prevista</label>
            <input type="date" value={arrival} onChange={(e) => setArrival(e.target.value)}
              className="w-full border border-border rounded px-2 py-1.5 bg-background" />
          </div>
        </div>

        {createMutation.isError && (
          <p className="text-xs text-destructive mt-2">Errore nella creazione.</p>
        )}

        <div className="flex gap-2 mt-4">
          <button onClick={onClose} className="flex-1 py-1.5 border border-border rounded hover:bg-accent">Annulla</button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!material.trim() || !orderId.trim() || createMutation.isPending}
            className="flex-1 py-1.5 bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50"
          >
            Aggiungi
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Timeline view ─────────────────────────────────────────────────────────────

function TimelineView({ components }: { components: MissingComponent[] }) {
  const sorted = [...components]
    .filter((c) => c.expected_arrival_date && !c.is_arrived)
    .sort((a, b) => new Date(a.expected_arrival_date!).getTime() - new Date(b.expected_arrival_date!).getTime());

  if (!sorted.length) return (
    <p className="text-sm text-muted-foreground text-center py-8">
      Nessun componente mancante con data di arrivo.
    </p>
  );

  const minDate = new Date(sorted[0].expected_arrival_date!).getTime();
  const maxDate = new Date(sorted[sorted.length - 1].expected_arrival_date!).getTime();
  const totalMs = Math.max(maxDate - minDate, 1);

  return (
    <div className="relative h-16 bg-muted rounded-lg overflow-hidden">
      <div className="absolute inset-y-0 left-4 right-4">
        {/* Today marker */}
        {(() => {
          const pct = Math.max(0, Math.min(100, ((Date.now() - minDate) / totalMs) * 100));
          return (
            <div className="absolute top-0 bottom-0 w-0.5 bg-red-500 opacity-70" style={{ left: `${pct}%` }}>
              <span className="absolute -top-0.5 text-[9px] text-red-500 whitespace-nowrap -translate-x-1/2">Oggi</span>
            </div>
          );
        })()}

        {sorted.map((c) => {
          const pct = ((new Date(c.expected_arrival_date!).getTime() - minDate) / totalMs) * 100;
          const urg = calcUrgency(c.expected_arrival_date);
          return (
            <div
              key={c.id}
              className={`absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full cursor-pointer ${
                urg === 'CRITICO' ? 'bg-red-500' : urg === 'ALTO' ? 'bg-orange-500' : 'bg-yellow-400'
              }`}
              style={{ left: `${pct}%` }}
              title={`${c.component_material} — ${c.expected_arrival_date}`}
            />
          );
        })}
      </div>

      {/* Dates */}
      <div className="absolute bottom-1 left-4 text-[9px] text-muted-foreground">
        {sorted[0].expected_arrival_date?.slice(0, 10)}
      </div>
      <div className="absolute bottom-1 right-4 text-[9px] text-muted-foreground">
        {sorted[sorted.length - 1].expected_arrival_date?.slice(0, 10)}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MissingComponents() {
  const { selectedMachineOrderId } = useMachineStore();
  const { activeScenarioId }       = useScheduleStore();
  const qc = useQueryClient();

  const [showAdd, setShowAdd]           = useState(false);
  const [viewMode, setViewMode]         = useState<'table' | 'timeline'>('table');
  const [filterUrgency, setFilterUrgency] = useState<string>('');
  const [onlyPending, setOnlyPending]   = useState(false);

  const { data: components = [], isLoading } = useMissingComponents(selectedMachineOrderId ?? undefined);

  const markArrivedMutation = useMutation({
    mutationFn: (id: string) => apiClient.patch(`/api/missing-components/${id}/mark-arrived`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['missing-components', selectedMachineOrderId] });
      triggerToast('Componente arrivato. Rischedulazione avviata per i gruppi bloccati.');
      // Trigger reschedule if scenario active
      if (activeScenarioId) {
        apiClient.post(`/api/schedule/scenario/${activeScenarioId}/reschedule`).catch(() => {});
      }
    },
  });

  const filtered = useMemo(() => {
    return components.filter((c) => {
      if (onlyPending && c.is_arrived) return false;
      if (filterUrgency) {
        const urg = calcUrgency(c.expected_arrival_date);
        if (urg !== filterUrgency) return false;
      }
      return true;
    });
  }, [components, onlyPending, filterUrgency]);

  if (!selectedMachineOrderId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Seleziona un ordine macchina per visualizzare i componenti mancanti.
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Componenti Mancanti</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setViewMode(viewMode === 'table' ? 'timeline' : 'table')}
            className="flex items-center gap-1 text-sm border border-border rounded px-2 py-1 hover:bg-accent"
          >
            <Calendar size={14} />
            {viewMode === 'table' ? 'Timeline' : 'Tabella'}
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm"
          >
            <Plus size={14} /> Aggiungi
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-2 flex-wrap items-center">
        <select
          value={filterUrgency}
          onChange={(e) => setFilterUrgency(e.target.value)}
          className="border border-border rounded px-2 py-1 text-sm bg-background"
        >
          <option value="">Tutte le urgenze</option>
          {(['CRITICO', 'ALTO', 'MEDIO', 'BASSO'] as Urgency[]).map((u) => (
            <option key={u} value={u}>{u}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm cursor-pointer">
          <input type="checkbox" checked={onlyPending} onChange={(e) => setOnlyPending(e.target.checked)} />
          Solo non arrivati
        </label>
      </div>

      {/* Timeline or Table */}
      {viewMode === 'timeline' ? (
        <section>
          <h2 className="text-sm font-semibold mb-2">Timeline arrivi</h2>
          <TimelineView components={filtered} />
        </section>
      ) : isLoading ? (
        <p className="text-muted-foreground text-sm">Caricamento…</p>
      ) : filtered.length === 0 ? (
        <div className="border border-dashed border-border rounded-xl p-12 text-center text-muted-foreground text-sm">
          Nessun componente trovato.
        </div>
      ) : (
        <table className="w-full text-xs border border-border rounded-lg overflow-hidden">
          <thead className="bg-muted">
            <tr>
              {['Materiale', 'Descrizione', 'Arrivo Previsto', 'Urgenza', 'Arrivato'].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.map((c) => {
              const urgency = calcUrgency(c.expected_arrival_date);
              return (
                <tr key={c.id} className={`hover:bg-accent ${c.is_arrived ? 'opacity-50' : ''}`}>
                  <td className="px-3 py-2 font-mono font-semibold">{c.component_material}</td>
                  <td className="px-3 py-2 max-w-[180px] truncate">{c.description ?? '—'}</td>
                  <td className="px-3 py-2">{c.expected_arrival_date ?? '—'}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${URGENCY_COLORS[urgency]}`}>
                      {urgency}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {c.is_arrived ? (
                      <span className="text-green-600 text-[10px]">✓ Arrivato</span>
                    ) : (
                      <button
                        onClick={() => markArrivedMutation.mutate(c.id)}
                        disabled={markArrivedMutation.isPending}
                        className="flex items-center gap-0.5 text-xs text-primary hover:underline disabled:opacity-50"
                        title="Segna come arrivato"
                      >
                        <CheckSquare size={14} />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {showAdd && selectedMachineOrderId && (
        <AddMissingModal machineOrderId={selectedMachineOrderId} onClose={() => setShowAdd(false)} />
      )}
    </div>
  );
}

