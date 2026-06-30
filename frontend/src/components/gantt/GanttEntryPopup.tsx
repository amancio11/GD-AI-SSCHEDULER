import type { GanttEntry } from '../../api/types';
import { X, Bot, Edit2 } from 'lucide-react';
import apiClient from '../../api/client';
import { useQuery } from '@tanstack/react-query';

interface GanttEntryPopupProps {
  entry: GanttEntry;
  onClose: () => void;
  onOverride: () => void;
}

export default function GanttEntryPopup({ entry, onClose, onOverride }: GanttEntryPopupProps) {
  const durationMinutes =
    Math.round(
      (new Date(entry.end).getTime() - new Date(entry.start).getTime()) / 60_000
    );

  const { data: explanation, isFetching: loadingExplanation, refetch: fetchExplanation } =
    useQuery<string>({
      queryKey: ['explain-entry', entry.id],
      queryFn: async () => {
        const { data } = await apiClient.get<string>(`/api/ai/explain-entry/${entry.id}`);
        return data;
      },
      enabled: false,
    });

  const fmt = (iso: string) =>
    new Date(iso).toLocaleString('it-IT', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });

  return (
    <div className="absolute z-50 bg-card border border-border rounded-lg shadow-xl p-4 w-80 text-sm"
      style={{ top: 8, left: 8 }}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="font-semibold">{entry.operation_desc ?? entry.operation_id}</p>
          <p className="text-xs text-muted-foreground">{entry.order_desc}</p>
        </div>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground ml-2 shrink-0">
          <X size={14} />
        </button>
      </div>

      {/* Fields */}
      <dl className="space-y-1">
        <Row label="Risorsa" value={entry.operator_name} />
        <Row label="Start" value={fmt(entry.start)} />
        <Row label="Fine" value={fmt(entry.end)} />
        <Row label="Durata" value={`${durationMinutes} min`} />
        <Row label="Stato" value={entry.status} />
        {entry.is_critical_path && (
          <Row label="Critical path" value="✓ Sì" className="text-yellow-600 font-semibold" />
        )}
        {entry.is_manual_override && (
          <Row label="Override manuale" value="✓ Sì" className="text-yellow-600" />
        )}
      </dl>

      {/* AI explanation */}
      {explanation && (
        <div className="mt-3 p-2 rounded bg-muted text-xs text-muted-foreground whitespace-pre-wrap">
          {explanation}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 mt-3">
        <button
          onClick={() => fetchExplanation()}
          disabled={loadingExplanation}
          className="flex items-center gap-1 text-xs border border-border rounded px-2 py-1 hover:bg-accent disabled:opacity-50"
        >
          <Bot size={12} />
          {loadingExplanation ? 'Analisi…' : 'Perché è schedulata così?'}
        </button>
        <button
          onClick={() => { onClose(); onOverride(); }}
          className="flex items-center gap-1 text-xs border border-border rounded px-2 py-1 hover:bg-accent"
        >
          <Edit2 size={12} />
          Override
        </button>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  className = '',
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={`flex justify-between ${className}`}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
