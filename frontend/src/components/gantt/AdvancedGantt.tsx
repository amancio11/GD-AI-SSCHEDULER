// frontend/src/components/gantt/AdvancedGantt.tsx
//
// Gantt avanzato per Production Scheduling
// - 3 modalità di raggruppamento: BY_ORDER (BOM tree) | BY_OPERATOR | BY_WORKCENTER
// - Frecce dipendenza derivate dal DAG RP
// - Progress overlay, status colors, RP markers, critical path
// - Zoom dinamico (Ora/Giorno/Settimana), virtual scroll, export PNG
//
// Dipendenze: react, axios, lucide-react, html-to-image (npm i html-to-image)
// Stili: Tailwind + shadcn/ui

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Calendar,
  Download,
  Filter,
  Maximize2,
  Users,
  Building2,
  Network,
  AlertTriangle,
} from "lucide-react";
import { toPng } from "html-to-image";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ============================================================================
// TYPES
// ============================================================================

export type GanttStatus =
  | "SCHEDULED"
  | "IN_PROGRESS"
  | "COMPLETED"
  | "BLOCKED"
  | "INTERRUPTED"
  | "DELAYED"
  | "STALE";

export type BomLevel = "MACHINE" | "MACROAGGREGATE" | "AGGREGATE" | "GROUP";

export interface GanttEntry {
  id: string;
  operation_id: string;
  operation_description: string;
  operation_type: "ELECTRICAL" | "MECHANICAL" | "GENERAL";
  operator_id: string;
  operator_name: string;
  operator_skill: "ELECTRICAL" | "MECHANICAL" | "MULTI";
  workcenter_id: string;
  workcenter_code: string;
  workcenter_name: string;
  production_order_id: string;
  production_order_material: string;
  production_order_description: string;
  production_order_level: BomLevel;
  parent_order_id: string | null;
  scheduled_start: string; // ISO
  scheduled_end: string;   // ISO
  actual_start: string | null;
  actual_end: string | null;
  status: GanttStatus;
  progress_pct: number;
  is_critical_path: boolean;
  missing_components: string[];
  reference_point_code: string | null;
}

export interface DependencyEdge {
  from_entry_id: string;
  to_entry_id: string;
  source: "RP_DAG" | "INTRA_ROUTING";
}

export interface RpMarker {
  entry_id: string; // last entry of the predecessor branch
  rp_code: string;
  rp_label: string;
  completion_time: string;
}

export type GroupingMode = "BY_ORDER" | "BY_OPERATOR" | "BY_WORKCENTER";
export type ZoomLevel = "HOUR" | "DAY" | "WEEK";

interface AdvancedGanttProps {
  entries: GanttEntry[];
  dependencies?: DependencyEdge[];
  rpMarkers?: RpMarker[];
  initialMode?: GroupingMode;
  height?: number;
  onEntryClick?: (entry: GanttEntry) => void;
}

// ============================================================================
// STATUS COLORS — match the mockup
// ============================================================================

const STATUS_STYLE: Record<GanttStatus, { fill: string; stroke: string; pattern?: string }> = {
  SCHEDULED:   { fill: "#88878066", stroke: "#5F5E5A" },
  IN_PROGRESS: { fill: "#378ADDcc", stroke: "#185FA5" },
  COMPLETED:   { fill: "#1D9E7599", stroke: "#0F6E56" },
  BLOCKED:     { fill: "#E24B4A4D", stroke: "#A32D2D", pattern: "3 2" },
  INTERRUPTED: { fill: "#EF9F27a6", stroke: "#BA7517" },
  DELAYED:     { fill: "#D85A30a6", stroke: "#993C1D" },
  STALE:       { fill: "#B4B2A966", stroke: "#888780", pattern: "1 3" },
};

const LEVEL_INDENT: Record<BomLevel, number> = {
  MACHINE: 0,
  MACROAGGREGATE: 16,
  AGGREGATE: 32,
  GROUP: 48,
};

// ============================================================================
// HELPERS
// ============================================================================

function parseISO(s: string): Date {
  return new Date(s);
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function formatHM(d: Date): string {
  return d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
}

function formatDay(d: Date): string {
  return d.toLocaleDateString("it-IT", { weekday: "short", day: "2-digit", month: "short" });
}

interface TimeAxis {
  start: Date;
  end: Date;
  pxPerMs: number;
  ticks: { date: Date; x: number; label: string }[];
}

function buildAxis(entries: GanttEntry[], zoom: ZoomLevel, widthPx: number): TimeAxis {
  if (entries.length === 0) {
    const now = new Date();
    return { start: now, end: now, pxPerMs: 0, ticks: [] };
  }

  const starts = entries.map((e) => parseISO(e.scheduled_start).getTime());
  const ends = entries.map((e) => parseISO(e.scheduled_end).getTime());
  const min = Math.min(...starts);
  const max = Math.max(...ends);

  // Pad 5% on each side
  const span = max - min || 60 * 60 * 1000;
  const padded_min = min - span * 0.02;
  const padded_max = max + span * 0.02;
  const total = padded_max - padded_min;
  const pxPerMs = widthPx / total;

  const ticks: TimeAxis["ticks"] = [];
  const step =
    zoom === "HOUR" ? 60 * 60 * 1000 :
    zoom === "DAY" ? 24 * 60 * 60 * 1000 :
    7 * 24 * 60 * 60 * 1000;

  // Snap first tick to next boundary
  const first = new Date(padded_min);
  if (zoom === "HOUR") {
    first.setMinutes(0, 0, 0);
    first.setHours(first.getHours() + 1);
  } else if (zoom === "DAY") {
    first.setHours(0, 0, 0, 0);
    first.setDate(first.getDate() + 1);
  } else {
    first.setHours(0, 0, 0, 0);
    first.setDate(first.getDate() + ((8 - first.getDay()) % 7));
  }

  for (let t = first.getTime(); t < padded_max; t += step) {
    const d = new Date(t);
    const x = (t - padded_min) * pxPerMs;
    ticks.push({
      date: d,
      x,
      label: zoom === "HOUR" ? formatHM(d) : formatDay(d),
    });
  }

  return { start: new Date(padded_min), end: new Date(padded_max), pxPerMs, ticks };
}

// ============================================================================
// GROUPING
// ============================================================================

interface GroupRow {
  key: string;
  label: string;
  sublabel?: string;
  level: BomLevel | "OPERATOR" | "WORKCENTER";
  indent: number;
  entries: GanttEntry[];
  isParent: boolean;
}

function groupByOrder(entries: GanttEntry[]): GroupRow[] {
  // Build map order_id -> entries
  const byOrder = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = byOrder.get(e.production_order_id) ?? [];
    arr.push(e);
    byOrder.set(e.production_order_id, arr);
  }

  // Build parent-child tree
  const orders = new Map<string, { id: string; material: string; desc: string; level: BomLevel; parent: string | null; entries: GanttEntry[] }>();
  for (const [oid, ents] of byOrder.entries()) {
    const first = ents[0];
    orders.set(oid, {
      id: oid,
      material: first.production_order_material,
      desc: first.production_order_description,
      level: first.production_order_level,
      parent: first.parent_order_id,
      entries: ents,
    });
  }

  // Roots (level === MACHINE or parent not in set)
  const roots = Array.from(orders.values()).filter(
    (o) => o.parent === null || !orders.has(o.parent)
  );

  const rows: GroupRow[] = [];
  function dfs(node: { id: string; material: string; desc: string; level: BomLevel; entries: GanttEntry[] }, depth: number): void {
    rows.push({
      key: node.id,
      label: `${node.material} — ${node.desc}`,
      level: node.level,
      indent: LEVEL_INDENT[node.level],
      entries: node.entries,
      isParent: true,
    });
    const children = Array.from(orders.values())
      .filter((o) => o.parent === node.id)
      .sort((a, b) => a.material.localeCompare(b.material));
    for (const c of children) dfs(c, depth + 1);
  }
  for (const r of roots.sort((a, b) => a.material.localeCompare(b.material))) dfs(r, 0);

  return rows;
}

function groupByOperator(entries: GanttEntry[]): GroupRow[] {
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.operator_id) ?? [];
    arr.push(e);
    map.set(e.operator_id, arr);
  }
  return Array.from(map.entries())
    .map(([opId, ents]) => {
      const first = ents[0];
      return {
        key: opId,
        label: first.operator_name,
        sublabel: `${first.operator_skill} • ${first.workcenter_code}`,
        level: "OPERATOR" as const,
        indent: 0,
        entries: ents,
        isParent: false,
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

function groupByWorkcenter(entries: GanttEntry[]): GroupRow[] {
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.workcenter_id) ?? [];
    arr.push(e);
    map.set(e.workcenter_id, arr);
  }
  return Array.from(map.entries())
    .map(([wcId, ents]) => {
      const first = ents[0];
      return {
        key: wcId,
        label: `${first.workcenter_code} — ${first.workcenter_name}`,
        level: "WORKCENTER" as const,
        indent: 0,
        entries: ents,
        isParent: false,
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

// ============================================================================
// COMPONENT
// ============================================================================

const ROW_HEIGHT = 28;
const HEADER_HEIGHT = 40;
const LABEL_COL_WIDTH = 280;
const RIGHT_PADDING = 20;

export default function AdvancedGantt({
  entries,
  dependencies = [],
  rpMarkers = [],
  initialMode = "BY_ORDER",
  height = 600,
  onEntryClick,
}: AdvancedGanttProps): JSX.Element {
  const [mode, setMode] = useState<GroupingMode>(initialMode);
  const [zoom, setZoom] = useState<ZoomLevel>("DAY");
  const [statusFilter, setStatusFilter] = useState<GanttStatus | "ALL">("ALL");
  const [showCriticalOnly, setShowCriticalOnly] = useState<boolean>(false);
  const [showDeps, setShowDeps] = useState<boolean>(true);
  const [showRpMarkers, setShowRpMarkers] = useState<boolean>(true);
  const [containerWidth, setContainerWidth] = useState<number>(900);
  const containerRef = useRef<HTMLDivElement>(null);
  const exportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((es) => {
      for (const e of es) setContainerWidth(e.contentRect.width);
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const filteredEntries = useMemo(() => {
    return entries.filter((e) => {
      if (statusFilter !== "ALL" && e.status !== statusFilter) return false;
      if (showCriticalOnly && !e.is_critical_path) return false;
      return true;
    });
  }, [entries, statusFilter, showCriticalOnly]);

  const rows = useMemo(() => {
    if (mode === "BY_ORDER") return groupByOrder(filteredEntries);
    if (mode === "BY_OPERATOR") return groupByOperator(filteredEntries);
    return groupByWorkcenter(filteredEntries);
  }, [filteredEntries, mode]);

  const chartWidth = containerWidth - LABEL_COL_WIDTH - RIGHT_PADDING;
  const axis = useMemo(
    () => buildAxis(filteredEntries, zoom, chartWidth),
    [filteredEntries, zoom, chartWidth]
  );

  // Lookup entry -> row index for dependency drawing
  const entryRowIdx = useMemo(() => {
    const m = new Map<string, number>();
    rows.forEach((r, i) => {
      for (const e of r.entries) m.set(e.id, i);
    });
    return m;
  }, [rows]);

  const totalHeight = rows.length * ROW_HEIGHT + HEADER_HEIGHT + 20;

  const xForDate = (iso: string): number =>
    (parseISO(iso).getTime() - axis.start.getTime()) * axis.pxPerMs;

  const nowX = useMemo(() => {
    const nowMs = Date.now();
    const startMs = axis.start.getTime();
    const endMs = axis.end.getTime();
    if (nowMs < startMs || nowMs > endMs) return null;
    return (nowMs - startMs) * axis.pxPerMs;
  }, [axis]);

  const exportPng = async (): Promise<void> => {
    if (!exportRef.current) return;
    const dataUrl = await toPng(exportRef.current, { backgroundColor: "#ffffff" });
    const link = document.createElement("a");
    link.download = `gantt-${new Date().toISOString()}.png`;
    link.href = dataUrl;
    link.click();
  };

  return (
    <TooltipProvider delay={150}>
      <Card className="w-full">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <CardTitle className="flex items-center gap-2">
              <Calendar className="h-5 w-5" />
              Schedulazione — Gantt
            </CardTitle>
            <div className="flex items-center gap-2 flex-wrap">
              {/* Grouping mode */}
              <div className="flex items-center gap-1 rounded-md border p-0.5">
                <Button
                  size="sm"
                  variant={mode === "BY_ORDER" ? "default" : "ghost"}
                  onClick={() => setMode("BY_ORDER")}
                  className="h-7"
                >
                  <Network className="h-3.5 w-3.5 mr-1" />
                  Ordine
                </Button>
                <Button
                  size="sm"
                  variant={mode === "BY_OPERATOR" ? "default" : "ghost"}
                  onClick={() => setMode("BY_OPERATOR")}
                  className="h-7"
                >
                  <Users className="h-3.5 w-3.5 mr-1" />
                  Operatore
                </Button>
                <Button
                  size="sm"
                  variant={mode === "BY_WORKCENTER" ? "default" : "ghost"}
                  onClick={() => setMode("BY_WORKCENTER")}
                  className="h-7"
                >
                  <Building2 className="h-3.5 w-3.5 mr-1" />
                  Workcenter
                </Button>
              </div>

              {/* Zoom */}
              <Select value={zoom} onValueChange={(v) => setZoom(v as ZoomLevel)}>
                <SelectTrigger className="w-[110px] h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="HOUR">Ora</SelectItem>
                  <SelectItem value="DAY">Giorno</SelectItem>
                  <SelectItem value="WEEK">Settimana</SelectItem>
                </SelectContent>
              </Select>

              {/* Status filter */}
              <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as GanttStatus | "ALL")}>
                <SelectTrigger className="w-[140px] h-8">
                  <Filter className="h-3.5 w-3.5 mr-1" />
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">Tutti gli stati</SelectItem>
                  <SelectItem value="SCHEDULED">Pianificate</SelectItem>
                  <SelectItem value="IN_PROGRESS">In corso</SelectItem>
                  <SelectItem value="COMPLETED">Completate</SelectItem>
                  <SelectItem value="BLOCKED">Bloccate</SelectItem>
                  <SelectItem value="INTERRUPTED">Interrotte</SelectItem>
                  <SelectItem value="DELAYED">In ritardo</SelectItem>
                </SelectContent>
              </Select>

              <Button
                size="sm"
                variant={showCriticalOnly ? "default" : "outline"}
                onClick={() => setShowCriticalOnly((v) => !v)}
                className="h-8"
                title="Mostra solo il percorso critico"
              >
                <Maximize2 className="h-3.5 w-3.5 mr-1" />
                Critical
              </Button>

              <Button
                size="sm"
                variant={showDeps ? "default" : "outline"}
                onClick={() => setShowDeps((v) => !v)}
                className="h-8"
              >
                Dipendenze
              </Button>

              <Button
                size="sm"
                variant={showRpMarkers ? "default" : "outline"}
                onClick={() => setShowRpMarkers((v) => !v)}
                className="h-8"
              >
                RP
              </Button>

              <Button size="sm" variant="outline" onClick={exportPng} className="h-8">
                <Download className="h-3.5 w-3.5 mr-1" />
                PNG
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent className="p-0">
          <div
            ref={containerRef}
            style={{ height, overflow: "auto" }}
            className="border-t"
          >
            <div ref={exportRef} style={{ width: containerWidth, position: "relative" }}>
              <svg
                width={containerWidth}
                height={totalHeight}
                style={{ display: "block", background: "white" }}
              >
                {/* Header background */}
                <rect x={0} y={0} width={containerWidth} height={HEADER_HEIGHT} fill="#F1EFE8" />
                <line x1={LABEL_COL_WIDTH} y1={0} x2={LABEL_COL_WIDTH} y2={totalHeight} stroke="#D3D1C7" strokeWidth={0.5} />

                {/* Timeline ticks */}
                {axis.ticks.map((t, i) => (
                  <g key={`tick-${i}`}>
                    <line
                      x1={LABEL_COL_WIDTH + t.x}
                      y1={0}
                      x2={LABEL_COL_WIDTH + t.x}
                      y2={totalHeight}
                      stroke="#E5E5E5"
                      strokeWidth={0.5}
                    />
                    <text
                      x={LABEL_COL_WIDTH + t.x}
                      y={24}
                      textAnchor="middle"
                      fontSize={11}
                      fill="#5F5E5A"
                    >
                      {t.label}
                    </text>
                  </g>
                ))}

                {/* Row backgrounds and labels */}
                {rows.map((row, idx) => {
                  const y = HEADER_HEIGHT + idx * ROW_HEIGHT;
                  return (
                    <g key={row.key}>
                      <rect
                        x={0}
                        y={y}
                        width={containerWidth}
                        height={ROW_HEIGHT}
                        fill={idx % 2 === 0 ? "#FAFAF8" : "white"}
                      />
                      <text
                        x={8 + row.indent}
                        y={y + ROW_HEIGHT / 2 + 4}
                        fontSize={12}
                        fontWeight={row.isParent ? 500 : 400}
                        fill="#2C2C2A"
                      >
                        {row.label.length > 36 ? row.label.slice(0, 34) + "…" : row.label}
                      </text>
                      {row.sublabel && (
                        <text
                          x={8 + row.indent}
                          y={y + ROW_HEIGHT / 2 + 16}
                          fontSize={10}
                          fill="#5F5E5A"
                        >
                          {row.sublabel}
                        </text>
                      )}
                    </g>
                  );
                })}

                {/* Bars */}
                {rows.map((row, idx) =>
                  row.entries.map((e) => {
                    const y = HEADER_HEIGHT + idx * ROW_HEIGHT + 6;
                    const x = LABEL_COL_WIDTH + xForDate(e.scheduled_start);
                    const w = Math.max(
                      4,
                      xForDate(e.scheduled_end) - xForDate(e.scheduled_start)
                    );
                    const barH = 16;
                    const style = STATUS_STYLE[e.status];
                    const progressW = (w * clamp(e.progress_pct, 0, 100)) / 100;

                    return (
                      <Tooltip key={e.id}>
                        <TooltipTrigger>
                          <g
                            style={{ cursor: "pointer" }}
                            onClick={() => onEntryClick?.(e)}
                          >
                            {/* Background bar */}
                            <rect
                              x={x}
                              y={y}
                              width={w}
                              height={barH}
                              rx={3}
                              fill={style.fill}
                              stroke={style.stroke}
                              strokeWidth={0.5}
                              strokeDasharray={style.pattern}
                            />
                            {/* Progress overlay */}
                            {progressW > 1 && e.status !== "COMPLETED" && (
                              <rect
                                x={x}
                                y={y}
                                width={progressW}
                                height={barH}
                                rx={3}
                                fill={style.stroke}
                                opacity={0.55}
                              />
                            )}
                            {/* Critical path edge */}
                            {e.is_critical_path && (
                              <rect
                                x={x}
                                y={y + barH + 2}
                                width={w}
                                height={2}
                                fill="#E24B4A"
                                opacity={0.7}
                              />
                            )}
                            {/* Missing components indicator */}
                            {e.missing_components.length > 0 && w > 30 && (
                              <text
                                x={x + w / 2}
                                y={y + barH / 2 + 4}
                                textAnchor="middle"
                                fontSize={10}
                                fill="#791F1F"
                                fontWeight={500}
                              >
                                ⚠ {e.missing_components[0]}
                              </text>
                            )}
                            {/* Operation label (only if wide enough) */}
                            {w > 60 && e.missing_components.length === 0 && (
                              <text
                                x={x + 6}
                                y={y + barH / 2 + 4}
                                fontSize={10}
                                fill="#2C2C2A"
                                fontWeight={400}
                              >
                                {e.operation_description.length > Math.floor(w / 6)
                                  ? e.operation_description.slice(0, Math.floor(w / 6)) + "…"
                                  : e.operation_description}
                              </text>
                            )}
                          </g>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          <div className="space-y-1 text-xs">
                            <div className="font-semibold">{e.operation_description}</div>
                            <div>
                              Ordine: <span className="font-mono">{e.production_order_material}</span> ({e.production_order_level})
                            </div>
                            <div>Operatore: {e.operator_name} ({e.operator_skill})</div>
                            <div>Workcenter: {e.workcenter_code}</div>
                            <div>
                              {parseISO(e.scheduled_start).toLocaleString("it-IT")} →{" "}
                              {parseISO(e.scheduled_end).toLocaleString("it-IT")}
                            </div>
                            <div>
                              Stato: <Badge variant="secondary">{e.status}</Badge>{" "}
                              {e.progress_pct > 0 && `${e.progress_pct.toFixed(0)}%`}
                            </div>
                            {e.reference_point_code && (
                              <div>RP precedente: <span className="font-mono">{e.reference_point_code}</span></div>
                            )}
                            {e.missing_components.length > 0 && (
                              <div className="text-red-700 flex items-center gap-1">
                                <AlertTriangle className="h-3 w-3" />
                                Mancanti: {e.missing_components.join(", ")}
                              </div>
                            )}
                            {e.is_critical_path && (
                              <div className="text-red-700">⎯ Critical path</div>
                            )}
                          </div>
                        </TooltipContent>
                      </Tooltip>
                    );
                  })
                )}

                {/* Dependency arrows */}
                {showDeps &&
                  dependencies.map((d, i) => {
                    const fromEntry = entries.find((e) => e.id === d.from_entry_id);
                    const toEntry = entries.find((e) => e.id === d.to_entry_id);
                    if (!fromEntry || !toEntry) return null;
                    const fromRow = entryRowIdx.get(d.from_entry_id);
                    const toRow = entryRowIdx.get(d.to_entry_id);
                    if (fromRow === undefined || toRow === undefined) return null;
                    const x1 = LABEL_COL_WIDTH + xForDate(fromEntry.scheduled_end);
                    const y1 = HEADER_HEIGHT + fromRow * ROW_HEIGHT + 14;
                    const x2 = LABEL_COL_WIDTH + xForDate(toEntry.scheduled_start);
                    const y2 = HEADER_HEIGHT + toRow * ROW_HEIGHT + 14;
                    const midX = (x1 + x2) / 2;
                    const color = d.source === "RP_DAG" ? "#534AB7" : "#888780";
                    return (
                      <path
                        key={`dep-${i}`}
                        d={`M${x1} ${y1} L${midX} ${y1} L${midX} ${y2} L${x2} ${y2}`}
                        fill="none"
                        stroke={color}
                        strokeWidth={1}
                        markerEnd="url(#gantt-arrow)"
                        opacity={0.55}
                      />
                    );
                  })}

                {/* RP markers (vertical dashed lines + label) */}
                {showRpMarkers &&
                  rpMarkers.map((m, i) => {
                    const x = LABEL_COL_WIDTH + xForDate(m.completion_time);
                    return (
                      <g key={`rp-${i}`}>
                        <line
                          x1={x}
                          y1={HEADER_HEIGHT}
                          x2={x}
                          y2={totalHeight}
                          stroke="#534AB7"
                          strokeWidth={0.8}
                          strokeDasharray="2 3"
                          opacity={0.5}
                        />
                        <text
                          x={x + 3}
                          y={HEADER_HEIGHT + 12}
                          fontSize={9}
                          fill="#3C3489"
                        >
                          {m.rp_code}
                        </text>
                      </g>
                    );
                  })}

                {/* Now line */}
                {nowX !== null && (
                  <g>
                    <line
                      x1={LABEL_COL_WIDTH + nowX}
                      y1={HEADER_HEIGHT}
                      x2={LABEL_COL_WIDTH + nowX}
                      y2={totalHeight}
                      stroke="#E24B4A"
                      strokeWidth={1.5}
                      strokeDasharray="4 3"
                    />
                    <rect
                      x={LABEL_COL_WIDTH + nowX - 18}
                      y={HEADER_HEIGHT - 14}
                      width={36}
                      height={14}
                      rx={3}
                      fill="#E24B4A"
                    />
                    <text
                      x={LABEL_COL_WIDTH + nowX}
                      y={HEADER_HEIGHT - 4}
                      textAnchor="middle"
                      fontSize={10}
                      fill="white"
                      fontWeight={500}
                    >
                      ORA
                    </text>
                  </g>
                )}

                {/* Arrow marker def */}
                <defs>
                  <marker
                    id="gantt-arrow"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="5"
                    markerHeight="5"
                    orient="auto-start-reverse"
                  >
                    <path
                      d="M2 1L8 5L2 9"
                      fill="none"
                      stroke="context-stroke"
                      strokeWidth={1.5}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </marker>
                </defs>
              </svg>
            </div>
          </div>

          {/* Legend */}
          <div className="flex items-center flex-wrap gap-4 px-4 py-3 border-t bg-stone-50 text-xs">
            {(["COMPLETED", "IN_PROGRESS", "SCHEDULED", "BLOCKED", "INTERRUPTED", "DELAYED"] as GanttStatus[]).map((s) => (
              <div key={s} className="flex items-center gap-1.5">
                <div
                  className="w-4 h-3 rounded-sm"
                  style={{
                    background: STATUS_STYLE[s].fill,
                    border: `0.5px solid ${STATUS_STYLE[s].stroke}`,
                  }}
                />
                <span className="text-stone-700">{s}</span>
              </div>
            ))}
            <div className="flex items-center gap-1.5">
              <div className="w-6 h-0.5 bg-red-500" />
              <span className="text-stone-700">Critical path</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-6 h-3 border-l border-dashed border-purple-600" />
              <span className="text-stone-700">RP boundary</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </TooltipProvider>
  );
}