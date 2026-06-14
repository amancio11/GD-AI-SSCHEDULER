import { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useScheduleStore } from '../store/scheduleStore';
import { useMachineStore } from '../store/machineStore';
import { useAiStore } from '../store/aiStore';
import { useScenario, useGanttData } from '../api/hooks/useSchedule';
import { useMissingComponents } from '../api/hooks/useMissing';
import apiClient from '../api/client';
import type { DelayEvent, ScheduleScenario } from '../api/types';
import { NavLink } from 'react-router-dom';
import { AlertTriangle, PackageX, Bot, ChevronRight, BarChart2 } from 'lucide-react';

// ── KPI card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: 'green' | 'red' }) {
  return (
    <div className="border border-border rounded-xl p-4 bg-card">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className={`text-2xl font-bold ${accent === 'green' ? 'text-green-600' : accent === 'red' ? 'text-destructive' : ''}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Progress ring ─────────────────────────────────────────────────────────────

function ProgressRing({ pct }: { pct: number }) {
  const r = 28;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - pct / 100);
  return (
    <svg width={70} height={70}>
      <circle cx={35} cy={35} r={r} fill="none" stroke="hsl(var(--muted))" strokeWidth={6} />
      <circle
        cx={35} cy={35} r={r} fill="none"
        stroke="hsl(var(--primary))" strokeWidth={6}
        strokeDasharray={circ} strokeDashoffset={offset}
        strokeLinecap="round"
        transform="rotate(-90 35 35)"
      />
      <text x={35} y={40} textAnchor="middle" fontSize={13} fontWeight="bold" fill="currentColor">
        {pct.toFixed(0)}%
      </text>
    </svg>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate();
  const { activeScenarioId } = useScheduleStore();
  const { selectedMachineOrderId } = useMachineStore();
  const { unreadCount } = useAiStore();

  const { data: scenario }  = useScenario(activeScenarioId ?? undefined);
  const { data: ganttEntries = [] } = useGanttData(activeScenarioId ?? undefined);
  const { data: missingComponents = [] } = useMissingComponents(selectedMachineOrderId ?? undefined);

  // Active delays
  const { data: delays = [] } = useQuery<DelayEvent[]>({
    queryKey: ['delays', selectedMachineOrderId],
    queryFn: async () => {
      const { data } = await apiClient.get<DelayEvent[]>(`/api/delays/machine/${selectedMachineOrderId}`);
      return data;
    },
    enabled: !!selectedMachineOrderId,
    refetchInterval: 30_000,
  });

  // KPI computations
  const total     = ganttEntries.length;
  const completed = ganttEntries.filter((e) => e.status === 'COMPLETED').length;
  const pct       = total > 0 ? Math.round((completed / total) * 100) : 0;

  const today = new Date().toISOString().slice(0, 10);
  const criticalMissing = missingComponents.filter((mc) => {
    if (mc.is_arrived || !mc.expected_arrival_date) return false;
    const days = (new Date(mc.expected_arrival_date).getTime() - Date.now()) / 86400_000;
    return days <= 3;
  });

  // Estimated completion from max scheduled_end
  const estimatedEnd = useMemo(() => {
    if (!ganttEntries.length) return null;
    const maxEnd = Math.max(...ganttEntries.map((e) => new Date(e.end).getTime()));
    return new Date(maxEnd).toLocaleDateString('it-IT');
  }, [ganttEntries]);

  const targetDate = scenario?.target_finish_date;
  const endAccent: 'green' | 'red' | undefined = targetDate && estimatedEnd
    ? (new Date(estimatedEnd) <= new Date(targetDate) ? 'green' : 'red')
    : undefined;

  // Alerts
  const alerts = [
    ...delays.slice(0, 5).map((d) => ({
      icon: <AlertTriangle size={14} />,
      text: `Ritardo: ${d.event_type} — ${d.description ?? ''}`.slice(0, 60),
      action: () => navigate('/delays'),
      actionLabel: 'Vedi',
      urgency: 'HIGH',
    })),
    ...criticalMissing.slice(0, 3).map((mc) => ({
      icon: <PackageX size={14} />,
      text: `Mancante critico: ${mc.component_material} (arrivo: ${mc.expected_arrival_date})`,
      action: () => navigate('/missing'),
      actionLabel: 'Vedi',
      urgency: 'CRITICAL',
    })),
  ];

  return (
    <div className="h-full overflow-auto p-6 space-y-6">
      <h1 className="text-lg font-bold">Dashboard</h1>

      {/* ── KPI Cards ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Data fine stimata"
          value={estimatedEnd ?? '—'}
          sub={targetDate ? `Target: ${targetDate}` : undefined}
          accent={endAccent}
        />
        <div className="border border-border rounded-xl p-4 bg-card flex flex-col items-center justify-center">
          <p className="text-xs text-muted-foreground mb-2">% Completamento</p>
          <ProgressRing pct={pct} />
          <p className="text-xs text-muted-foreground mt-1">{completed}/{total} op.</p>
        </div>
        <KpiCard
          label="Operatori attivi oggi"
          value={String(new Set(ganttEntries.map((e) => e.operator_id)).size)}
        />
        <KpiCard
          label="Mancanti critici (≤ 3gg)"
          value={String(criticalMissing.length)}
          accent={criticalMissing.length > 0 ? 'red' : 'green'}
        />
      </div>

      {/* ── AI Suggestions banner ──────────────────────────────── */}
      {unreadCount > 0 && (
        <div className="flex items-center justify-between bg-purple-50 dark:bg-purple-950/30 border border-purple-200 rounded-xl px-4 py-3 text-sm">
          <div className="flex items-center gap-2 text-purple-700 dark:text-purple-300">
            <Bot size={16} />
            Hai <strong>{unreadCount}</strong> suggeriment{unreadCount === 1 ? 'o' : 'i'} AI non letti.
          </div>
          <NavLink to="/ai" className="flex items-center gap-1 text-xs text-purple-700 dark:text-purple-300 hover:underline">
            Visualizza <ChevronRight size={12} />
          </NavLink>
        </div>
      )}

      {/* ── Alerts panel ───────────────────────────────────────── */}
      {alerts.length > 0 && (
        <section className="border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-2 bg-muted border-b border-border text-sm font-semibold">
            Alert ({alerts.length})
          </div>
          <ul className="divide-y divide-border">
            {alerts.map((a, i) => (
              <li key={i} className="flex items-center justify-between px-4 py-2 text-sm hover:bg-accent">
                <div className="flex items-center gap-2">
                  <span className={a.urgency === 'CRITICAL' ? 'text-destructive' : 'text-orange-500'}>
                    {a.icon}
                  </span>
                  <span className="truncate max-w-[300px]">{a.text}</span>
                </div>
                <button
                  onClick={a.action}
                  className="text-xs text-primary hover:underline shrink-0 ml-2"
                >
                  {a.actionLabel}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── Timeline prossimi eventi ────────────────────────────── */}
      <section className="border border-border rounded-xl overflow-hidden">
        <div className="px-4 py-2 bg-muted border-b border-border text-sm font-semibold">
          Prossimi eventi
        </div>
        <ul className="divide-y divide-border text-xs">
          {missingComponents
            .filter((mc) => !mc.is_arrived && mc.expected_arrival_date)
            .sort((a, b) => a.expected_arrival_date!.localeCompare(b.expected_arrival_date!))
            .slice(0, 5)
            .map((mc) => (
              <li key={mc.id} className="flex justify-between px-4 py-2 hover:bg-accent">
                <span className="text-muted-foreground">{mc.expected_arrival_date}</span>
                <span>📦 Arrivo: {mc.component_material}</span>
              </li>
            ))}
          {delays.slice(0, 3).map((d) => (
            <li key={d.id} className="flex justify-between px-4 py-2 hover:bg-accent">
              <span className="text-muted-foreground">{new Date(d.delay_from).toLocaleDateString('it-IT')}</span>
              <span>⚠️ Ritardo: {d.event_type}</span>
            </li>
          ))}
          {!missingComponents.length && !delays.length && (
            <li className="px-4 py-4 text-muted-foreground text-center">Nessun evento imminente.</li>
          )}
        </ul>
      </section>

      {/* Gantt preview link */}
      <NavLink
        to="/gantt"
        className="flex items-center justify-between border border-border rounded-xl p-4 bg-card hover:bg-accent text-sm"
      >
        <div className="flex items-center gap-2 font-medium">
          <BarChart2 size={16} className="text-primary" />
          Vedi Gantt completo
        </div>
        <ChevronRight size={16} />
      </NavLink>
    </div>
  );
}

