import { useRef, useState, useMemo } from 'react';
import type { GanttEntry, MissingComponent, Operator } from '../../api/types';
import {
  buildDayTicks,
  formatDateLabel,
  tickInterval,
  xOffset,
  barWidth,
  groupByOperator,
  TYPE_COLORS,
  ROW_HEIGHT,
  PX_PER_MINUTE,
  MANUAL_OVERRIDE_BORDER,
  type ZoomLevel,
} from './ganttUtils';
import GanttEntryPopup from './GanttEntryPopup';
import OverrideModal from './OverrideModal';

interface GanttByOperatorProps {
  entries: GanttEntry[];
  operators: Operator[];
  missingComponents: MissingComponent[];
  zoom: ZoomLevel;
  showCriticalPath: boolean;
  scenarioId: string;
  originDate: Date;
  totalWidth: number;
}

export default function GanttByOperator({
  entries,
  operators,
  missingComponents,
  zoom,
  showCriticalPath,
  scenarioId,
  originDate,
  totalWidth,
}: GanttByOperatorProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [selectedEntry, setSelectedEntry] = useState<GanttEntry | null>(null);
  const [overrideEntry, setOverrideEntry]   = useState<GanttEntry | null>(null);

  const byOperator = useMemo(() => groupByOperator(entries), [entries]);

  // Righe = gruppi risorsa presenti nelle entries (capacità di gruppo, non più
  // operatori con nome). Etichetta = operator_name (= label del gruppo risorsa).
  const rows = useMemo(() => {
    const out: { id: string; label: string; entries: GanttEntry[] }[] = [];
    for (const [groupId, ents] of byOperator.entries()) {
      out.push({ id: groupId, label: ents[0]?.operator_name ?? groupId, entries: ents });
    }
    out.sort((a, b) => a.label.localeCompare(b.label));
    return out;
  }, [byOperator]);

  const todayX = xOffset(new Date().toISOString(), originDate, zoom);

  // Build day ticks for the visible range
  const endDate = new Date(originDate.getTime() + totalWidth / PX_PER_MINUTE[zoom] * 60_000);
  const ticks = buildDayTicks(originDate, endDate)
    .filter((_, i) => i % tickInterval(zoom) === 0);

  return (
    <div className="flex flex-col h-full">
      {/* ── Date header ─────────────────────────────────────────────── */}
      <div
        className="relative border-b border-border bg-muted text-xs text-muted-foreground select-none overflow-hidden"
        style={{ height: 28, minWidth: totalWidth }}
        ref={scrollRef}
      >
        {ticks.map((tick, i) => {
          const x = xOffset(tick.toISOString(), originDate, zoom);
          return (
            <span
              key={i}
              className="absolute top-1.5 whitespace-nowrap"
              style={{ left: x + 4 }}
            >
              {formatDateLabel(tick, zoom)}
            </span>
          );
        })}
        {/* Today marker in header */}
        <div
          className="absolute top-0 bottom-0 w-px bg-red-400 opacity-70"
          style={{ left: todayX }}
        />
      </div>

      {/* ── Rows ───────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto relative" style={{ minWidth: totalWidth }}>
        {/* Today vertical line */}
        <div
          className="absolute top-0 bottom-0 w-px bg-red-400 opacity-40 pointer-events-none z-10"
          style={{ left: todayX }}
        />

        {rows.map(({ id, entries: opEntries }) => (
          <div
            key={id}
            className="relative border-b border-border"
            style={{ height: ROW_HEIGHT }}
          >
            {opEntries.map((entry) => {
              const x  = xOffset(entry.start, originDate, zoom);
              const w  = barWidth(entry.start, entry.end, zoom);
              const isCritical = showCriticalPath && entry.is_critical_path;
              const barColor = entry.color || TYPE_COLORS.GENERAL;

              return (
                <div
                  key={entry.id}
                  className={`absolute top-1.5 bottom-1.5 rounded cursor-pointer hover:brightness-110 transition-all
                    ${entry.is_manual_override ? MANUAL_OVERRIDE_BORDER : ''}
                    ${isCritical ? 'ring-2 ring-yellow-400' : ''}
                  `}
                  style={{
                    left:  x,
                    width: w,
                    backgroundColor: barColor,
                    minWidth: 2,
                  }}
                  title={`${entry.operation_desc ?? ''} — ${entry.status}`}
                  onClick={() => setSelectedEntry(entry)}
                />
              );
            })}

            {/* Entry popup */}
            {selectedEntry && opEntries.some((e) => e.id === selectedEntry.id) && (
              <div className="absolute z-40" style={{ left: xOffset(selectedEntry.start, originDate, zoom), top: ROW_HEIGHT }}>
                <GanttEntryPopup
                  entry={selectedEntry}
                  onClose={() => setSelectedEntry(null)}
                  onOverride={() => setOverrideEntry(selectedEntry)}
                />
              </div>
            )}
          </div>
        ))}

        {/* ── Missing components row ──────────────────────────────── */}
        {missingComponents.length > 0 && (
          <div
            className="relative border-b border-border bg-red-50 dark:bg-red-950/20"
            style={{ height: ROW_HEIGHT }}
          >
            {missingComponents
              .filter((mc) => mc.expected_arrival_date)
              .map((mc) => {
                const x = xOffset(
                  new Date(mc.expected_arrival_date!).toISOString(),
                  originDate,
                  zoom
                );
                return (
                  <div
                    key={mc.id}
                    className="absolute top-1 bottom-1 w-2 bg-red-500 rounded-full cursor-pointer"
                    style={{ left: x }}
                    title={`${mc.component_material} — arrivo: ${mc.expected_arrival_date}`}
                  />
                );
              })}
          </div>
        )}
      </div>

      {/* Override modal */}
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
