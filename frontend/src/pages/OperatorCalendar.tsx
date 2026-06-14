import { useState, useMemo } from 'react';
import { useOperators, useOperatorCalendar } from '../api/hooks/useOperators';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import type { Operator, OperatorCalendarEntry, SkillType } from '../api/types';
import { ChevronLeft, ChevronRight, X, Check } from 'lucide-react';

// ── Constants ─────────────────────────────────────────────────────────────────

const SKILL_COLORS: Record<SkillType, string> = {
  ELECTRICAL: 'bg-blue-100 text-blue-700',
  MECHANICAL: 'bg-orange-100 text-orange-700',
  MULTI: 'bg-purple-100 text-purple-700',
};

const SHIFT_COLORS: Record<string, string> = {
  Mattina:    'bg-green-100 text-green-800',
  Pomeriggio: 'bg-yellow-100 text-yellow-800',
  Notte:      'bg-blue-900 text-white',
};

const DAYS_IT = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom'];

function isoDate(d: Date) {
  return d.toISOString().slice(0, 10);
}

function todayIso() {
  return isoDate(new Date());
}

// Build the calendar grid: array of weeks (each week = 7 Date|null entries, Mon-Sun)
function buildCalendarGrid(year: number, month: number): (Date | null)[][] {
  const firstDay = new Date(year, month, 1);
  const lastDay  = new Date(year, month + 1, 0);

  // day-of-week for the 1st (0=Sun → shift to Mon-first)
  const startDow = (firstDay.getDay() + 6) % 7; // 0=Mon

  const days: (Date | null)[] = [];
  for (let i = 0; i < startDow; i++) days.push(null);
  for (let d = 1; d <= lastDay.getDate(); d++) days.push(new Date(year, month, d));
  while (days.length % 7 !== 0) days.push(null);

  const weeks: (Date | null)[][] = [];
  for (let i = 0; i < days.length; i += 7) weeks.push(days.slice(i, i + 7));
  return weeks;
}

// ── Sub-components ────────────────────────────────────────────────────────────

interface DayEditModalProps {
  date: Date;
  operatorId: string;
  existingEntry: OperatorCalendarEntry | null;
  onClose: () => void;
}

function DayEditModal({ date, operatorId, existingEntry, onClose }: DayEditModalProps) {
  const qc = useQueryClient();
  const dateStr = isoDate(date);

  const [shiftName, setShiftName] = useState<string>(
    existingEntry?.is_available ? (existingEntry.override_reason ?? 'Mattina') : ''
  );
  const [notes, setNotes]     = useState(existingEntry?.notes ?? '');
  const isAbsent = shiftName === '';

  const saveMutation = useMutation({
    mutationFn: () =>
      apiClient.put(`/api/operators/${operatorId}/calendar/${dateStr}`, {
        shift_id: null,       // backend resolves by name; simplified here
        is_available: !isAbsent,
        notes: isAbsent ? notes : null,
        override_reason: shiftName || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-calendar', operatorId] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-card border border-border rounded-xl shadow-xl p-6 w-80 text-sm">
        <div className="flex justify-between mb-4">
          <h3 className="font-semibold">
            {date.toLocaleDateString('it-IT', { weekday: 'long', day: '2-digit', month: 'long' })}
          </h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X size={14} />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1">Turno</label>
            <select
              value={shiftName}
              onChange={(e) => setShiftName(e.target.value)}
              className="w-full border border-border rounded px-2 py-1.5 bg-background"
            >
              <option value="Mattina">Mattina (06:00–14:00)</option>
              <option value="Pomeriggio">Pomeriggio (14:00–22:00)</option>
              <option value="Notte">Notte (22:00–06:00)</option>
              <option value="">Assente</option>
            </select>
          </div>

          {isAbsent && (
            <div>
              <label className="block text-xs font-medium mb-1">Note (obbligatorio)</label>
              <input
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Motivo assenza…"
                className="w-full border border-border rounded px-2 py-1.5 bg-background"
              />
            </div>
          )}
        </div>

        {saveMutation.isError && (
          <p className="text-xs text-destructive mt-2">Errore nel salvataggio.</p>
        )}

        <div className="flex gap-2 mt-4">
          <button
            onClick={onClose}
            className="flex-1 py-1.5 border border-border rounded hover:bg-accent text-sm"
          >
            Annulla
          </button>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={isAbsent && !notes.trim() || saveMutation.isPending}
            className="flex-1 py-1.5 bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50 text-sm"
          >
            {saveMutation.isPending ? 'Salvo…' : 'Salva'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function OperatorCalendar() {
  const qc = useQueryClient();
  const today = todayIso();

  // Month state
  const [viewDate, setViewDate] = useState(() => {
    const d = new Date();
    return { year: d.getFullYear(), month: d.getMonth() };
  });

  // Operator filters
  const [skillFilter, setSkillFilter] = useState<SkillType | ''>('');
  const [wcFilter, setWcFilter]       = useState('');
  const [search, setSearch]           = useState('');

  // Selected operator
  const [selectedOperatorId, setSelectedOperatorId] = useState<string | null>(null);

  // Day edit modal
  const [editDay, setEditDay] = useState<Date | null>(null);

  // Bulk edit state
  const [bulkFrom, setBulkFrom]   = useState('');
  const [bulkTo, setBulkTo]       = useState('');
  const [bulkShift, setBulkShift] = useState('Mattina');

  const { data: operators = [] } = useOperators();

  // Filter sidebar operators
  const filteredOperators = useMemo(
    () =>
      operators.filter(
        (op) =>
          (!skillFilter || op.skill === skillFilter) &&
          (!wcFilter || op.workcenter_id === wcFilter) &&
          (!search || op.full_name.toLowerCase().includes(search.toLowerCase()))
      ),
    [operators, skillFilter, wcFilter, search]
  );

  const selectedOperator = operators.find((o) => o.id === selectedOperatorId) ?? null;

  // Calendar data for current month
  const firstOfMonth = `${viewDate.year}-${String(viewDate.month + 1).padStart(2, '0')}-01`;
  const lastDay = new Date(viewDate.year, viewDate.month + 1, 0);
  const lastOfMonth = isoDate(lastDay);

  const { data: calendarEntries = [] } = useOperatorCalendar(
    selectedOperatorId ?? undefined,
    firstOfMonth,
    lastOfMonth
  );

  // Build a map date-string → entry for O(1) lookup
  const entryByDate = useMemo(() => {
    const map: Record<string, OperatorCalendarEntry> = {};
    for (const e of calendarEntries) map[e.date] = e;
    return map;
  }, [calendarEntries]);

  // Calendar grid
  const weeks = useMemo(
    () => buildCalendarGrid(viewDate.year, viewDate.month),
    [viewDate.year, viewDate.month]
  );

  // Bulk update mutation
  const bulkMutation = useMutation({
    mutationFn: () =>
      apiClient.post('/api/operators/calendar/bulk-update', {
        operator_ids: [selectedOperatorId],
        date_from: bulkFrom,
        date_to: bulkTo,
        shift_id: null,
        is_available: true,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['operator-calendar', selectedOperatorId] });
    },
  });

  // Stats
  const absences  = calendarEntries.filter((e) => !e.is_available).length;
  const workDays  = calendarEntries.filter((e) => e.is_available).length;
  const totalHoursApprox = workDays * 7.5; // ~7.5h per shift (8h - 30min break)

  const monthName = new Date(viewDate.year, viewDate.month, 1).toLocaleDateString('it-IT', {
    month: 'long',
    year: 'numeric',
  });

  function navMonth(delta: number) {
    setViewDate((v) => {
      const d = new Date(v.year, v.month + delta, 1);
      return { year: d.getFullYear(), month: d.getMonth() };
    });
  }

  function initials(name: string) {
    return name
      .split(' ')
      .map((w) => w[0])
      .slice(0, 2)
      .join('')
      .toUpperCase();
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Sidebar ──────────────────────────────────────────────── */}
      <aside className="w-56 shrink-0 border-r border-border bg-card flex flex-col overflow-hidden">
        <div className="p-2 border-b border-border space-y-1.5">
          <input
            placeholder="Cerca…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full text-xs border border-border rounded px-2 py-1 bg-background"
          />
          <select
            value={skillFilter}
            onChange={(e) => setSkillFilter(e.target.value as SkillType | '')}
            className="w-full text-xs border border-border rounded px-2 py-1 bg-background"
          >
            <option value="">Tutte le skill</option>
            <option value="ELECTRICAL">ELECTRICAL</option>
            <option value="MECHANICAL">MECHANICAL</option>
            <option value="MULTI">MULTI</option>
          </select>
        </div>

        <ul className="flex-1 overflow-y-auto divide-y divide-border">
          {filteredOperators.map((op) => (
            <li
              key={op.id}
              onClick={() => setSelectedOperatorId(op.id)}
              className={`flex items-center gap-2 px-2 py-2 cursor-pointer hover:bg-accent text-sm
                ${selectedOperatorId === op.id ? 'bg-accent' : ''}
              `}
            >
              <div className="w-8 h-8 rounded-full bg-primary text-primary-foreground flex items-center justify-center text-xs font-bold shrink-0">
                {initials(op.full_name)}
              </div>
              <div className="min-w-0">
                <p className="font-medium truncate text-xs">{op.full_name}</p>
                <span className={`text-[10px] rounded px-1 ${SKILL_COLORS[op.skill]}`}>
                  {op.skill}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </aside>

      {/* ── Main area ────────────────────────────────────────────── */}
      {!selectedOperator ? (
        <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
          Seleziona un operatore per visualizzarne il calendario.
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-card shrink-0">
            <div>
              <h1 className="text-base font-semibold">{selectedOperator.full_name}</h1>
              <span className={`text-xs rounded px-1.5 py-0.5 ${SKILL_COLORS[selectedOperator.skill]}`}>
                {selectedOperator.skill}
              </span>
            </div>

            {/* Month navigation */}
            <div className="flex items-center gap-2">
              <button onClick={() => navMonth(-1)} className="p-1 hover:bg-accent rounded">
                <ChevronLeft size={16} />
              </button>
              <span className="text-sm font-medium w-36 text-center capitalize">{monthName}</span>
              <button onClick={() => navMonth(1)} className="p-1 hover:bg-accent rounded">
                <ChevronRight size={16} />
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-auto p-4 space-y-4">
            {/* ── Calendar grid ──────────────────────────────────── */}
            <div className="border border-border rounded-lg overflow-hidden">
              {/* Day headers */}
              <div className="grid grid-cols-7 bg-muted">
                {DAYS_IT.map((d) => (
                  <div key={d} className="text-center text-xs text-muted-foreground py-1.5 font-medium">
                    {d}
                  </div>
                ))}
              </div>

              {/* Weeks */}
              {weeks.map((week, wi) => (
                <div key={wi} className="grid grid-cols-7 border-t border-border">
                  {week.map((day, di) => {
                    if (!day) {
                      return <div key={di} className="h-14 bg-muted/30" />;
                    }
                    const ds   = isoDate(day);
                    const entry = entryByDate[ds];
                    const isToday = ds === today;
                    const isPast  = ds < today;

                    let cellBg = '';
                    let icon: React.ReactNode = null;
                    let tooltip = '';

                    if (entry) {
                      if (!entry.is_available) {
                        cellBg  = 'bg-red-50 dark:bg-red-950/20';
                        icon    = <X size={12} className="text-red-500" />;
                        tooltip = entry.notes ?? 'Assente';
                      } else {
                        const sn = entry.override_reason ?? '';
                        cellBg  = SHIFT_COLORS[sn] ?? 'bg-green-50';
                        tooltip = sn;
                      }
                    }

                    return (
                      <div
                        key={di}
                        onClick={() => !isPast && setEditDay(day)}
                        title={tooltip}
                        className={`h-14 border-l border-border first:border-l-0 flex flex-col items-start p-1 text-xs
                          ${cellBg}
                          ${isToday ? 'ring-2 ring-inset ring-primary' : ''}
                          ${!isPast ? 'cursor-pointer hover:brightness-95' : 'opacity-60'}
                        `}
                      >
                        <span className={`font-medium ${isToday ? 'text-primary' : ''}`}>
                          {day.getDate()}
                        </span>
                        {icon && <span className="mt-auto">{icon}</span>}
                        {entry?.is_available && entry.override_reason && (
                          <span className="text-[9px] mt-auto truncate w-full">
                            {entry.override_reason}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>

            {/* ── Bulk edit ─────────────────────────────────────── */}
            <section className="border border-border rounded-lg p-3">
              <h2 className="text-sm font-semibold mb-2">Modifica in blocco</h2>
              <div className="flex flex-wrap gap-2 items-end">
                <div>
                  <label className="block text-xs mb-0.5">Dal</label>
                  <input
                    type="date"
                    value={bulkFrom}
                    onChange={(e) => setBulkFrom(e.target.value)}
                    className="border border-border rounded px-2 py-1 text-sm bg-background"
                  />
                </div>
                <div>
                  <label className="block text-xs mb-0.5">Al</label>
                  <input
                    type="date"
                    value={bulkTo}
                    onChange={(e) => setBulkTo(e.target.value)}
                    className="border border-border rounded px-2 py-1 text-sm bg-background"
                  />
                </div>
                <div>
                  <label className="block text-xs mb-0.5">Turno</label>
                  <select
                    value={bulkShift}
                    onChange={(e) => setBulkShift(e.target.value)}
                    className="border border-border rounded px-2 py-1 text-sm bg-background"
                  >
                    <option>Mattina</option>
                    <option>Pomeriggio</option>
                    <option>Notte</option>
                  </select>
                </div>
                <button
                  onClick={() => bulkMutation.mutate()}
                  disabled={!bulkFrom || !bulkTo || bulkMutation.isPending}
                  className="px-3 py-1 text-sm bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50"
                >
                  {bulkMutation.isPending ? 'Applicando…' : 'Applica'}
                </button>
                {bulkMutation.isSuccess && (
                  <span className="text-xs text-green-600 flex items-center gap-1">
                    <Check size={12} /> Aggiornato
                  </span>
                )}
              </div>
            </section>

            {/* ── Statistics ────────────────────────────────────── */}
            <section className="grid grid-cols-4 gap-3">
              {[
                { label: 'Ore nel mese',       value: `${totalHoursApprox.toFixed(0)}h` },
                { label: 'Giorni lavorativi',   value: workDays },
                { label: 'Assenze programmate', value: absences },
                { label: 'Utilizzo stimato',    value: workDays > 0 ? `${Math.min(100, Math.round((workDays / (lastDay.getDate())) * 100))}%` : '—' },
              ].map(({ label, value }) => (
                <div key={label} className="border border-border rounded-lg p-3 text-center">
                  <p className="text-xl font-bold">{value}</p>
                  <p className="text-xs text-muted-foreground">{label}</p>
                </div>
              ))}
            </section>
          </div>
        </div>
      )}

      {/* Day edit modal */}
      {editDay && selectedOperator && (
        <DayEditModal
          date={editDay}
          operatorId={selectedOperator.id}
          existingEntry={entryByDate[isoDate(editDay)] ?? null}
          onClose={() => setEditDay(null)}
        />
      )}
    </div>
  );
}

