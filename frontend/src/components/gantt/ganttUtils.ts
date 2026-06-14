import type { GanttEntry, ScheduleEntryStatus } from '../../api/types';

export type ZoomLevel = 'day' | 'week' | 'month';

/** Pixels per minute at each zoom level */
export const PX_PER_MINUTE: Record<ZoomLevel, number> = {
  day: 0.15,
  week: 0.035,
  month: 0.008,
};

/** Row height in pixels */
export const ROW_HEIGHT = 36;

/** Colour for the bar fill by operation_type (extracted from GanttEntry.color when available) */
export const TYPE_COLORS: Record<string, string> = {
  ELECTRICAL: '#3b82f6',  // blue
  MECHANICAL: '#f97316',  // orange
  GENERAL:    '#6b7280',  // gray
};

/** Border style for bar by status */
export const STATUS_BORDER: Record<ScheduleEntryStatus | string, string> = {
  SCHEDULED:   'border border-transparent',
  IN_PROGRESS: 'border-2 border-pulse animate-pulse',
  COMPLETED:   'opacity-60 border border-transparent',
  INTERRUPTED: 'border-2 border-dashed border-red-400',
  DELAYED:     'border-2 border-red-500',
  STALE:       'opacity-30 border border-gray-400',
};

/** For manual override entries add a yellow border */
export const MANUAL_OVERRIDE_BORDER = 'ring-2 ring-yellow-400';

export function toDate(iso: string): Date {
  return new Date(iso);
}

export function minutesBetween(a: Date, b: Date): number {
  return (b.getTime() - a.getTime()) / 60_000;
}

/** Build a list of day ticks between start and end dates */
export function buildDayTicks(start: Date, end: Date): Date[] {
  const ticks: Date[] = [];
  const cur = new Date(start);
  cur.setHours(0, 0, 0, 0);
  while (cur <= end) {
    ticks.push(new Date(cur));
    cur.setDate(cur.getDate() + 1);
  }
  return ticks;
}

export function formatDateLabel(date: Date, zoom: ZoomLevel): string {
  if (zoom === 'day') {
    return date.toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: '2-digit' });
  }
  if (zoom === 'week') {
    return date.toLocaleDateString('it-IT', { day: '2-digit', month: 'short' });
  }
  return date.toLocaleDateString('it-IT', { month: 'short', year: '2-digit' });
}

export function tickInterval(zoom: ZoomLevel): number {
  if (zoom === 'day')   return 1;    // every day
  if (zoom === 'week')  return 7;    // every week
  return 30;                          // every ~month
}

/** Given an ISO datetime and the chart origin, return x offset in pixels */
export function xOffset(isoDate: string, originDate: Date, zoom: ZoomLevel): number {
  const ms = new Date(isoDate).getTime() - originDate.getTime();
  const minutes = ms / 60_000;
  return Math.round(minutes * PX_PER_MINUTE[zoom]);
}

export function barWidth(startIso: string, endIso: string, zoom: ZoomLevel): number {
  const mins = minutesBetween(toDate(startIso), toDate(endIso));
  return Math.max(2, Math.round(mins * PX_PER_MINUTE[zoom]));
}

/** Group GanttEntries by operator_id */
export function groupByOperator(entries: GanttEntry[]): Map<string, GanttEntry[]> {
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.operator_id) ?? [];
    arr.push(e);
    map.set(e.operator_id, arr);
  }
  return map;
}

/** Group GanttEntries by order_id */
export function groupByOrder(entries: GanttEntry[]): Map<string, GanttEntry[]> {
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.order_id) ?? [];
    arr.push(e);
    map.set(e.order_id, arr);
  }
  return map;
}
