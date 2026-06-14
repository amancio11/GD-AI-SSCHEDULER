import { useState, useMemo } from 'react';
import type { GanttEntry, Operator } from '../../api/types';
import {
  buildDayTicks,
  formatDateLabel,
  tickInterval,
  xOffset,
  barWidth,
  groupByOrder,
  ROW_HEIGHT,
  PX_PER_MINUTE,
  MANUAL_OVERRIDE_BORDER,
  type ZoomLevel,
} from './ganttUtils';
import GanttEntryPopup from './GanttEntryPopup';
import OverrideModal from './OverrideModal';

interface GanttByOrderProps {
  entries: GanttEntry[];
  operators: Operator[];
  zoom: ZoomLevel;
  showCriticalPath: boolean;
  scenarioId: string;
  originDate: Date;
  totalWidth: number;
}

export default function GanttByOrder({
  entries,
  operators,
  zoom,
  showCriticalPath,
  scenarioId,
  originDate,
  totalWidth,
}: GanttByOrderProps) {
  const [selectedEntry, setSelectedEntry] = useState<GanttEntry | null>(null);
  const [overrideEntry, setOverrideEntry]   = useState<GanttEntry | null>(null);

  const byOrder = useMemo(() => groupByOrder(entries), [entries]);

  // Build order rows: one per order_id, bar = min(start) → max(end)
  const rows = useMemo(() => {
    return Array.from(byOrder.entries()).map(([orderId, oes]) => {
      const starts = oes.map((e) => new Date(e.start).getTime());
      const ends   = oes.map((e) => new Date(e.end).getTime());
      const minStart = new Date(Math.min(...starts)).toISOString();
      const maxEnd   = new Date(Math.max(...ends)).toISOString();
      const progress = oes.filter((e) => e.status === 'COMPLETED').length / oes.length * 100;
      const label = oes[0].order_desc ?? orderId;
      const hasCritical = showCriticalPath && oes.some((e) => e.is_critical_path);
      return { orderId, oes, minStart, maxEnd, progress, label, hasCritical };
    });
  }, [byOrder, showCriticalPath]);

  const todayX = xOffset(new Date().toISOString(), originDate, zoom);
  const endDate = new Date(originDate.getTime() + totalWidth / PX_PER_MINUTE[zoom] * 60_000);
  const ticks = buildDayTicks(originDate, endDate)
    .filter((_, i) => i % tickInterval(zoom) === 0);

  return (
    <div className="flex flex-col h-full">
      {/* Date header */}
      <div
        className="relative border-b border-border bg-muted text-xs text-muted-foreground select-none overflow-hidden"
        style={{ height: 28, minWidth: totalWidth }}
      >
        {ticks.map((tick, i) => {
          const x = xOffset(tick.toISOString(), originDate, zoom);
          return (
            <span key={i} className="absolute top-1.5 whitespace-nowrap" style={{ left: x + 4 }}>
              {formatDateLabel(tick, zoom)}
            </span>
          );
        })}
        <div
          className="absolute top-0 bottom-0 w-px bg-red-400 opacity-70"
          style={{ left: todayX }}
        />
      </div>

      {/* Rows */}
      <div className="flex-1 overflow-auto relative" style={{ minWidth: totalWidth }}>
        <div
          className="absolute top-0 bottom-0 w-px bg-red-400 opacity-40 pointer-events-none z-10"
          style={{ left: todayX }}
        />

        {rows.map(({ orderId, oes, minStart, maxEnd, progress, label, hasCritical }) => {
          const x = xOffset(minStart, originDate, zoom);
          const w = barWidth(minStart, maxEnd, zoom);

          return (
            <div
              key={orderId}
              className="relative border-b border-border"
              style={{ height: ROW_HEIGHT }}
            >
              {/* Span bar */}
              <div
                className={`absolute top-1.5 bottom-1.5 rounded cursor-pointer hover:brightness-110
                  bg-primary opacity-80
                  ${hasCritical ? 'ring-2 ring-yellow-400' : ''}
                `}
                style={{ left: x, width: w, minWidth: 2 }}
                title={label}
                onClick={() => setSelectedEntry(oes[0])}
              >
                {/* Progress fill */}
                <div
                  className="h-full rounded bg-green-500 opacity-40"
                  style={{ width: `${progress}%` }}
                />
                {/* Label inside bar */}
                <span className="absolute inset-0 flex items-center px-1 text-xs text-white truncate pointer-events-none">
                  {label}
                </span>
                {/* Progress badge */}
                <span className="absolute right-1 top-0.5 text-[10px] text-white opacity-80">
                  {Math.round(progress)}%
                </span>
              </div>

              {/* Entry popup */}
              {selectedEntry && oes.some((e) => e.id === selectedEntry.id) && (
                <div className="absolute z-40" style={{ left: x, top: ROW_HEIGHT }}>
                  <GanttEntryPopup
                    entry={selectedEntry}
                    onClose={() => setSelectedEntry(null)}
                    onOverride={() => setOverrideEntry(selectedEntry)}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {overrideEntry && (
        <OverrideModal
          entry={overrideEntry}
          scenarioId={scenarioId}
          operators={operators}
          onClose={() => setOverrideEntry(null)}
        />
      )}
    </div>
  );
}
