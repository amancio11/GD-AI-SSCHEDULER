import { useMemo, useState, useEffect } from 'react';
import { useGanttData, useScenarios } from '../api/hooks/useSchedule';
import { useOperators } from '../api/hooks/useOperators';
import { useMissingComponents } from '../api/hooks/useMissing';
import { useScheduleStore } from '../store/scheduleStore';
import { useMachineStore } from '../store/machineStore';
import GanttByOperator from '../components/gantt/GanttByOperator';
import GanttByOrder from '../components/gantt/GanttByOrder';
import { PX_PER_MINUTE, type ZoomLevel } from '../components/gantt/ganttUtils';

const ZOOM_LABELS: Record<ZoomLevel, string> = {
  day:   'Giorno',
  week:  'Settimana',
  month: 'Mese',
};

/** Visible time window in minutes for each zoom level */
const WINDOW_MINUTES: Record<ZoomLevel, number> = {
  day:   60 * 24 * 7,    // 7 days
  week:  60 * 24 * 30,   // 30 days
  month: 60 * 24 * 120,  // 120 days
};

export default function GanttView() {
  const { activeScenarioId, ganttViewMode, setGanttViewMode, setActiveScenarioId } = useScheduleStore();
  const { selectedMachineOrderId } = useMachineStore();

  const [zoom, setZoom] = useState<ZoomLevel>('week');
  const [showCriticalPath, setShowCriticalPath] = useState(false);

  // Carica la lista scenari e seleziona automaticamente il primo disponibile
  const { data: scenarios } = useScenarios();
  useEffect(() => {
    if (!activeScenarioId && scenarios && scenarios.length > 0) {
      // Preferisce lo scenario attivo (is_active=true), altrimenti il primo
      const active = scenarios.find(s => s.is_active) ?? scenarios[0];
      setActiveScenarioId(active.id);
    }
  }, [scenarios, activeScenarioId, setActiveScenarioId]);

  const { data: entries = [], isLoading, isError } = useGanttData(activeScenarioId ?? undefined);
  const { data: operators = [] } = useOperators();
  const { data: missingComponents = [] } = useMissingComponents(selectedMachineOrderId ?? undefined);

  // Compute origin (min start) and total chart width
  const { originDate, totalWidth } = useMemo(() => {
    if (!entries.length) {
      const origin = new Date();
      origin.setHours(0, 0, 0, 0);
      return {
        originDate: origin,
        totalWidth: WINDOW_MINUTES[zoom] * PX_PER_MINUTE[zoom],
      };
    }
    const minStart = Math.min(...entries.map((e) => new Date(e.start).getTime()));
    const maxEnd   = Math.max(...entries.map((e) => new Date(e.end).getTime()));
    const origin = new Date(minStart);
    origin.setHours(0, 0, 0, 0);
    const totalMins = (maxEnd - origin.getTime()) / 60_000;
    return {
      originDate: origin,
      totalWidth: Math.max(totalMins * PX_PER_MINUTE[zoom], WINDOW_MINUTES[zoom] * PX_PER_MINUTE[zoom]),
    };
  }, [entries, zoom]);

  if (!activeScenarioId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Caricamento scenari…
      </div>
    );
  }

  // Scenario esiste ma non ha ancora schedule entries (solver non ancora eseguito)
  if (!isLoading && !isError && entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-muted-foreground">
        <div className="text-center">
          <p className="text-base font-medium text-foreground">Nessun piano disponibile</p>
          <p className="text-sm mt-1">
            Lo scenario <strong>{scenarios?.find(s => s.id === activeScenarioId)?.name ?? activeScenarioId}</strong> non è ancora stato schedulato.
          </p>
          <p className="text-sm mt-1">
            Vai su <strong>Scenari</strong> → clicca <strong>Schedula</strong> per avviare il solver CP-SAT.
          </p>
        </div>
        <a
          href="/scenarios"
          className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm hover:bg-primary/90"
        >
          Vai agli Scenari →
        </a>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* ── Toolbar ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-border bg-card shrink-0">
        {/* View switch */}
        <div className="flex rounded border border-border overflow-hidden text-sm">
          <button
            onClick={() => setGanttViewMode('BY_OPERATOR')}
            className={`px-3 py-1 ${ganttViewMode === 'BY_OPERATOR' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
          >
            Per Operatore
          </button>
          <button
            onClick={() => setGanttViewMode('BY_ORDER')}
            className={`px-3 py-1 ${ganttViewMode === 'BY_ORDER' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
          >
            Per Ordine
          </button>
        </div>

        {/* Zoom */}
        <div className="flex rounded border border-border overflow-hidden text-sm">
          {(['day', 'week', 'month'] as ZoomLevel[]).map((z) => (
            <button
              key={z}
              onClick={() => setZoom(z)}
              className={`px-3 py-1 ${zoom === z ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
            >
              {ZOOM_LABELS[z]}
            </button>
          ))}
        </div>

        {/* Critical path toggle */}
        <label className="flex items-center gap-1.5 text-sm cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showCriticalPath}
            onChange={(e) => setShowCriticalPath(e.target.checked)}
          />
          Mostra critical path
        </label>

        {/* Legend */}
        <div className="flex items-center gap-2 ml-auto text-xs text-muted-foreground">
          <span className="inline-block w-3 h-3 rounded bg-blue-500" /> ELECTRICAL
          <span className="inline-block w-3 h-3 rounded bg-orange-500 ml-2" /> MECHANICAL
          <span className="inline-block w-3 h-3 rounded bg-gray-400 ml-2" /> GENERAL
        </div>
      </div>

      {/* ── Left label column + Chart ────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Label column */}
        <div className="w-44 shrink-0 border-r border-border bg-card overflow-y-auto">
          <div className="h-7 border-b border-border" /> {/* spacer for date header */}
          {ganttViewMode === 'BY_OPERATOR' ? (
            operators.map((op) => (
              <div
                key={op.id}
                className="flex flex-col justify-center px-2 border-b border-border"
                style={{ height: 36 }}
              >
                <span className="text-xs font-medium truncate">{op.full_name}</span>
                <span className="text-[10px] text-muted-foreground">{op.skill}</span>
              </div>
            ))
          ) : (
            // For order view: unique order labels
            Array.from(new Set(entries.map((e) => e.order_id))).map((orderId) => {
              const e = entries.find((en) => en.order_id === orderId)!;
              return (
                <div
                  key={orderId}
                  className="flex items-center px-2 border-b border-border"
                  style={{ height: 36 }}
                >
                  <span className="text-xs truncate">{e.order_desc ?? orderId}</span>
                </div>
              );
            })
          )}
          {/* Missing components row label */}
          {ganttViewMode === 'BY_OPERATOR' && missingComponents.length > 0 && (
            <div
              className="flex items-center px-2 bg-red-50 dark:bg-red-950/20 text-xs text-red-600 border-b border-border"
              style={{ height: 36 }}
            >
              Componenti mancanti
            </div>
          )}
        </div>

        {/* Chart area */}
        <div className="flex-1 overflow-x-auto overflow-y-hidden">
          {isLoading && (
            <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
              Caricamento Gantt…
            </div>
          )}
          {isError && (
            <div className="flex items-center justify-center h-40 text-destructive text-sm">
              Errore nel caricamento del Gantt.
            </div>
          )}
          {!isLoading && !isError && ganttViewMode === 'BY_OPERATOR' && (
            <GanttByOperator
              entries={entries}
              operators={operators}
              missingComponents={missingComponents}
              zoom={zoom}
              showCriticalPath={showCriticalPath}
              scenarioId={activeScenarioId}
              originDate={originDate}
              totalWidth={totalWidth}
            />
          )}
          {!isLoading && !isError && ganttViewMode === 'BY_ORDER' && (
            <GanttByOrder
              entries={entries}
              operators={operators}
              zoom={zoom}
              showCriticalPath={showCriticalPath}
              scenarioId={activeScenarioId}
              originDate={originDate}
              totalWidth={totalWidth}
            />
          )}
        </div>
      </div>
    </div>
  );
}

