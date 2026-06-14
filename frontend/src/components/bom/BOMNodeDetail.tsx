import type { BOMTreeNode } from '../../api/types';
import { useOrderOperations } from '../../api/hooks/useOrders';
import { STATUS_COLORS, STATUS_LABELS, LEVEL_ICONS } from './bomConstants';
import { X } from 'lucide-react';

interface BOMNodeDetailProps {
  node: BOMTreeNode;
  onClose: () => void;
}

export default function BOMNodeDetail({ node, onClose }: BOMNodeDetailProps) {
  const { data: operations, isLoading } = useOrderOperations(node.id);

  return (
    <aside className="w-80 shrink-0 border-l border-border bg-card flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-lg">{LEVEL_ICONS[node.level]}</span>
          <div>
            <p className="font-mono text-xs text-muted-foreground">{node.material_code}</p>
            <p className="text-sm font-medium leading-tight truncate max-w-[200px]">
              {node.description ?? '—'}
            </p>
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground"
          aria-label="Chiudi pannello"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-sm">
        {/* Status + level */}
        <div className="flex flex-wrap gap-2">
          <span
            className={`text-xs border rounded px-2 py-0.5 ${STATUS_COLORS[node.status]}`}
          >
            {STATUS_LABELS[node.status]}
          </span>
          <span className="text-xs border rounded px-2 py-0.5 bg-muted text-muted-foreground">
            {node.level}
          </span>
        </div>

        {/* Progress */}
        <div>
          <div className="flex justify-between text-xs text-muted-foreground mb-1">
            <span>Avanzamento</span>
            <span>{node.progress_pct.toFixed(0)}%</span>
          </div>
          <div className="h-1.5 rounded bg-muted overflow-hidden">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${node.progress_pct}%` }}
            />
          </div>
        </div>

        {/* Missing arrival */}
        {node.missing_arrival_date && (
          <div className="rounded border border-orange-300 bg-orange-50 p-2 text-xs text-orange-700">
            <strong>Componente mancante</strong> — arrivo previsto:{' '}
            {node.missing_arrival_date.slice(0, 10)}
          </div>
        )}

        {/* Operations */}
        <div>
          <h3 className="font-semibold mb-2">Operazioni</h3>
          {isLoading ? (
            <p className="text-muted-foreground text-xs">Caricamento…</p>
          ) : !operations?.length ? (
            <p className="text-muted-foreground text-xs">Nessuna operazione.</p>
          ) : (
            <ul className="space-y-2">
              {operations.map((op) => (
                <li
                  key={op.id}
                  className="rounded border border-border p-2 text-xs space-y-0.5"
                >
                  <div className="flex justify-between">
                    <span className="font-medium">
                      {op.sequence_number}. {op.description ?? op.sap_operation_id ?? '—'}
                    </span>
                    <span className={`border rounded px-1.5 ${
                      op.status === 'COMPLETED'
                        ? 'bg-green-100 text-green-700 border-green-300'
                        : op.status === 'IN_PROGRESS'
                        ? 'bg-blue-100 text-blue-700 border-blue-300'
                        : 'bg-gray-100 text-gray-600 border-gray-300'
                    }`}>
                      {op.status}
                    </span>
                  </div>
                  <div className="text-muted-foreground">
                    {op.operation_type} · {op.planned_duration_minutes} min
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </aside>
  );
}
