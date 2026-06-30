// frontend/src/pages/GanttView.tsx
//
// Fix v2:
//  - Modalità ADVANCED sostituisce l'intera area (non affiancata)
//  - Zoom della toolbar (day/week/month) convertito in ZoomLevel HOUR/DAY/WEEK
//    e passato all'AdvancedGantt tramite prop externalZoom
//  - AdvancedGantt riceve enrichedGantt entries/dependencies/rpMarkers
//  - scheduleStore aggiornato per supportare 'ADVANCED' come GanttViewMode

import { useMemo, useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useGanttData, useScenarios } from '../api/hooks/useSchedule';
import { useOperators } from '../api/hooks/useOperators';
import { useMissingComponents } from '../api/hooks/useMissing';
import { useScheduleStore } from '../store/scheduleStore';
import { useMachineStore } from '../store/machineStore';
import GanttByOperator from '../components/gantt/GanttByOperator';
import GanttByOrder from '../components/gantt/GanttByOrder';
import AdvancedGantt, { type ZoomLevel as AdvZoomLevel } from '../components/gantt/AdvancedGantt';
import apiClient from '../api/client';
import { PX_PER_MINUTE, type ZoomLevel } from '../components/gantt/ganttUtils';

const ZOOM_LABELS: Record<ZoomLevel, string> = {
  day:   'Giorno',
  week:  'Settimana',
  month: 'Mese',
};

/** Mappa zoom toolbar → ZoomLevel di AdvancedGantt */
const ZOOM_MAP: Record<ZoomLevel, AdvZoomLevel> = {
  day:   'DAY',
  week:  'WEEK',
  month: 'WEEK',  // mese non esiste in AdvancedGantt, usa WEEK (vista più larga)
};

const WINDOW_MINUTES: Record<ZoomLevel, number> = {
  day:   60 * 24 * 7,
  week:  60 * 24 * 30,
  month: 60 * 24 * 120,
};

// GanttViewMode include ora ADVANCED
type GanttViewMode = 'BY_OPERATOR' | 'BY_ORDER' | 'ADVANCED';

export default function GanttView() {
  const { activeScenarioId, setActiveScenarioId } = useScheduleStore();
  const { selectedMachineOrderId } = useMachineStore();

  // Stato locale per la modalità vista (non nello store per evitare conflitti con tipo)
  const [ganttViewMode, setGanttViewMode] = useState<GanttViewMode>('BY_OPERATOR');
  const [zoom, setZoom] = useState<ZoomLevel>('week');
  const [showCriticalPath, setShowCriticalPath] = useState(false);

  const { data: scenarios } = useScenarios();
  useEffect(() => {
    if (!activeScenarioId && scenarios && scenarios.length > 0) {
      const active = scenarios.find(s => s.is_active) ?? scenarios[0];
      setActiveScenarioId(active.id);
    }
  }, [scenarios, activeScenarioId, setActiveScenarioId]);

  const { data: entries = [], isLoading, isError } = useGanttData(activeScenarioId ?? undefined);
  const { data: operators = [] } = useOperators();
  const { data: missingComponents = [] } = useMissingComponents(selectedMachineOrderId ?? undefined);

  // Dati arricchiti per la modalità ADVANCED (endpoint /api/gantt/{scenario_id})
  const { data: enrichedGantt } = useQuery({
    queryKey: ['gantt-enriched', activeScenarioId],
    queryFn: () => apiClient.get(`/api/gantt/${activeScenarioId}`).then(r => r.data),
    enabled: !!activeScenarioId && ganttViewMode === 'ADVANCED',
  });

  const { originDate, totalWidth } = useMemo(() => {
    if (!entries.length) {
      const origin = new Date();
      origin.setHours(0, 0, 0, 0);
      return { originDate: origin, totalWidth: WINDOW_MINUTES[zoom] * PX_PER_MINUTE[zoom] };
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

  if (!isLoading && !isError && entries.length === 0 && ganttViewMode !== 'ADVANCED') {
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
        <a href="/scenarios" className="px-4 py-2 bg-primary text-primary-foreground rounded text-sm hover:bg-primary/90">
          Vai agli Scenari →
        </a>
      </div>
    );
  }

  // ── Toolbar condivisa ──────────────────────────────────────────────────────
  const toolbar = (
    <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-border bg-card shrink-0">
      {/* View switch */}
      <div className="flex rounded border border-border overflow-hidden text-sm">
        <button
          onClick={() => setGanttViewMode('BY_OPERATOR')}
          className={`px-3 py-1 ${ganttViewMode === 'BY_OPERATOR' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
        >
          Risorsa
        </button>
        <button
          onClick={() => setGanttViewMode('BY_ORDER')}
          className={`px-3 py-1 ${ganttViewMode === 'BY_ORDER' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
        >
          Ordine
        </button>
        <button
          onClick={() => setGanttViewMode('ADVANCED')}
          className={`px-3 py-1 ${ganttViewMode === 'ADVANCED' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
        >
          ✦ Avanzato
        </button>
      </div>

      {/* Zoom — mostrato sempre, usato da tutte le viste */}
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

      {/* Critical path — solo per viste BY_* */}
      {ganttViewMode !== 'ADVANCED' && (
        <label className="flex items-center gap-1.5 text-sm cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showCriticalPath}
            onChange={(e) => setShowCriticalPath(e.target.checked)}
          />
          Critical path
        </label>
      )}

      {/* Legenda colori — solo per viste BY_* */}
      {ganttViewMode !== 'ADVANCED' && (
        <div className="flex items-center gap-2 ml-auto text-xs text-muted-foreground">
          <span className="inline-block w-3 h-3 rounded bg-blue-500" /> ELECTRICAL
          <span className="inline-block w-3 h-3 rounded bg-orange-500 ml-2" /> MECHANICAL
          <span className="inline-block w-3 h-3 rounded bg-gray-400 ml-2" /> GENERAL
        </div>
      )}
    </div>
  );

  // ── Vista ADVANCED — occupa tutta l'area sotto la toolbar ─────────────────
  if (ganttViewMode === 'ADVANCED') {
    return (
      <div className="flex flex-col h-full">
        {toolbar}
        <div className="flex-1 overflow-hidden p-3">
          {enrichedGantt ? (
            <AdvancedGantt
              entries={enrichedGantt.entries ?? []}
              dependencies={enrichedGantt.dependencies ?? []}
              rpMarkers={enrichedGantt.rp_markers ?? []}
              initialMode="BY_OPERATOR"
              externalZoom={ZOOM_MAP[zoom]}
              height={undefined}         // occupa tutto l'altezza disponibile
            />
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
              {activeScenarioId ? 'Caricamento dati avanzati…' : 'Seleziona uno scenario.'}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Vista BY_OPERATOR / BY_ORDER ──────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {toolbar}

      <div className="flex flex-1 overflow-hidden">
        {/* Colonna etichette */}
        <div className="w-44 shrink-0 border-r border-border bg-card overflow-y-auto">
          <div className="h-7 border-b border-border" />
          {ganttViewMode === 'BY_OPERATOR' ? (
            operators.map((op) => (
              <div key={op.id} className="flex flex-col justify-center px-2 border-b border-border" style={{ height: 36 }}>
                <span className="text-xs font-medium truncate">{op.full_name}</span>
                <span className="text-[10px] text-muted-foreground">{op.skill}</span>
              </div>
            ))
          ) : (
            Array.from(new Set(entries.map((e) => e.order_id))).map((orderId) => {
              const e = entries.find((en) => en.order_id === orderId)!;
              return (
                <div key={orderId} className="flex items-center px-2 border-b border-border" style={{ height: 36 }}>
                  <span className="text-xs truncate">{e.order_desc ?? orderId}</span>
                </div>
              );
            })
          )}
          {ganttViewMode === 'BY_OPERATOR' && missingComponents.length > 0 && (
            <div className="flex items-center px-2 bg-red-50 dark:bg-red-950/20 text-xs text-red-600 border-b border-border" style={{ height: 36 }}>
              Componenti mancanti
            </div>
          )}
        </div>

        {/* Area chart */}
        <div className="flex-1 overflow-x-auto overflow-y-hidden">
          {isLoading && (
            <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">Caricamento Gantt…</div>
          )}
          {isError && (
            <div className="flex items-center justify-center h-40 text-destructive text-sm">Errore nel caricamento del Gantt.</div>
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