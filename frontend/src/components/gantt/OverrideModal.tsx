import { useState } from 'react';
import type { GanttEntry, Operator } from '../../api/types';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../../api/client';
import { X, AlertTriangle } from 'lucide-react';

interface OverrideModalProps {
  entry: GanttEntry;
  scenarioId: string;
  operators: Operator[];
  onClose: () => void;
}

export default function OverrideModal({ entry, scenarioId, operators, onClose }: OverrideModalProps) {
  const queryClient = useQueryClient();

  // Pre-fill with current scheduled times (truncate seconds)
  const toLocalInput = (iso: string) => iso.slice(0, 16);

  const [newStart, setNewStart] = useState(toLocalInput(entry.start));
  const [newEnd, setNewEnd]     = useState(toLocalInput(entry.end));
  const [operatorId, setOperatorId] = useState(entry.operator_id);

  const durationWarning =
    newStart && newEnd && new Date(newStart) >= new Date(newEnd)
      ? 'La data di fine deve essere successiva a quella di inizio.'
      : null;

  const overrideMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/api/schedule/scenario/${scenarioId}/override-operation`, {
        operation_id: entry.operation_id,
        new_start: new Date(newStart).toISOString(),
        new_end: new Date(newEnd).toISOString(),
        operator_id: operatorId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['gantt', scenarioId] });
      queryClient.invalidateQueries({ queryKey: ['schedule', scenarioId] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-card border border-border rounded-xl shadow-2xl p-6 w-[420px] text-sm">
        {/* Header */}
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-base font-semibold">Override manuale</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X size={16} />
          </button>
        </div>

        <p className="text-muted-foreground text-xs mb-4">
          Operazione: <strong>{entry.operation_desc ?? entry.operation_id}</strong>
        </p>

        {/* Form */}
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1">Nuovo inizio</label>
            <input
              type="datetime-local"
              value={newStart}
              onChange={(e) => setNewStart(e.target.value)}
              className="w-full border border-border rounded px-2 py-1.5 text-sm bg-background"
            />
          </div>

          <div>
            <label className="block text-xs font-medium mb-1">Nuova fine</label>
            <input
              type="datetime-local"
              value={newEnd}
              onChange={(e) => setNewEnd(e.target.value)}
              className="w-full border border-border rounded px-2 py-1.5 text-sm bg-background"
            />
          </div>

          <div>
            <label className="block text-xs font-medium mb-1">Operatore</label>
            <select
              value={operatorId}
              onChange={(e) => setOperatorId(e.target.value)}
              className="w-full border border-border rounded px-2 py-1.5 text-sm bg-background"
            >
              {operators.map((op) => (
                <option key={op.id} value={op.id}>
                  {op.full_name} ({op.skill})
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Warning */}
        {durationWarning && (
          <div className="mt-3 flex items-center gap-2 text-xs text-destructive">
            <AlertTriangle size={12} />
            {durationWarning}
          </div>
        )}

        {overrideMutation.isError && (
          <div className="mt-3 flex items-center gap-2 text-xs text-destructive">
            <AlertTriangle size={12} />
            Errore nell&apos;override. Verifica i dati e riprova.
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2 mt-5">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-sm border border-border rounded hover:bg-accent"
          >
            Annulla
          </button>
          <button
            onClick={() => overrideMutation.mutate()}
            disabled={!!durationWarning || overrideMutation.isPending}
            className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50"
          >
            {overrideMutation.isPending ? 'Salvataggio…' : 'Applica override'}
          </button>
        </div>
      </div>
    </div>
  );
}
