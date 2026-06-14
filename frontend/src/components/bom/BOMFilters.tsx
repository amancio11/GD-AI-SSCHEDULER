import type { ProductionOrderLevel, ProductionOrderStatus } from '../../api/types';

export interface BOMFiltersState {
  onlyBlocked: boolean;
  onlyDelayed: boolean;
  workcenter: string;
  search: string;
}

interface BOMFiltersProps {
  filters: BOMFiltersState;
  onChange: (f: BOMFiltersState) => void;
  workcenters: string[];
}

export default function BOMFilters({ filters, onChange, workcenters }: BOMFiltersProps) {
  const set = (partial: Partial<BOMFiltersState>) => onChange({ ...filters, ...partial });

  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-border bg-card">
      <input
        type="text"
        placeholder="Cerca codice / descrizione…"
        value={filters.search}
        onChange={(e) => set({ search: e.target.value })}
        className="border border-border rounded px-2 py-1 text-sm w-56 bg-background"
      />

      <label className="flex items-center gap-1.5 text-sm cursor-pointer select-none">
        <input
          type="checkbox"
          checked={filters.onlyBlocked}
          onChange={(e) => set({ onlyBlocked: e.target.checked })}
          className="rounded"
        />
        Solo bloccati
      </label>

      <label className="flex items-center gap-1.5 text-sm cursor-pointer select-none">
        <input
          type="checkbox"
          checked={filters.onlyDelayed}
          onChange={(e) => set({ onlyDelayed: e.target.checked })}
          className="rounded"
        />
        Solo in ritardo
      </label>

      <select
        value={filters.workcenter}
        onChange={(e) => set({ workcenter: e.target.value })}
        className="border border-border rounded px-2 py-1 text-sm bg-background"
      >
        <option value="">Tutti i workcenter</option>
        {workcenters.map((wc) => (
          <option key={wc} value={wc}>
            {wc}
          </option>
        ))}
      </select>

      {(filters.search || filters.onlyBlocked || filters.onlyDelayed || filters.workcenter) && (
        <button
          onClick={() =>
            onChange({ search: '', onlyBlocked: false, onlyDelayed: false, workcenter: '' })
          }
          className="text-xs text-muted-foreground hover:text-foreground underline"
        >
          Azzera filtri
        </button>
      )}
    </div>
  );
}
