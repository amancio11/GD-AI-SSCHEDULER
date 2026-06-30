// frontend/src/components/gantt/AdvancedGantt.tsx
//
// v5 — Architettura "single scroll container"
//
// ROOT CAUSE definitiva:
//   Tutte le versioni precedenti usavano DUE contenitori di scroll separati
//   (sidebar + chart) che venivano sincronizzati via JS. Questo approccio ha
//   due problemi fondamentali:
//   1. Il browser non ammette scrollTop su un elemento con overflowY:hidden
//      (spec CSS: scrollTop è ignorato se overflow non è scroll/auto).
//   2. Il sub-pixel rounding del flex layout vs position:absolute diverge
//      anche di 1-2px dopo molte righe.
//
// SOLUZIONE v5 — UN SOLO scroll container:
//   La struttura è una singola tabella dove ogni riga ha due celle:
//   - Cella sinistra: etichetta (LABEL_COL px, sticky left)
//   - Cella destra: barre gantt (larghezza = totalPx)
//
//   L'intero div scrolla sia orizzontalmente che verticalmente.
//   La colonna label è "sticky left" quindi rimane visibile durante lo scroll X.
//   L'header data è "sticky top" quindi rimane visibile durante lo scroll Y.
//   L'angolo in alto a sinistra (header + label column intersection) è sticky
//   su entrambi gli assi.
//
//   Allineamento: la cella label e la cella chart sono NELLA STESSA RIGA DOM,
//   quindi non possono mai disallinearsi — il browser garantisce altezze uguali.

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { X } from "lucide-react";

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
  scheduled_start: string;
  scheduled_end: string;
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
  entry_id: string;
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
  height?: number | string;
  externalZoom?: ZoomLevel;
  onEntryClick?: (entry: GanttEntry) => void;
}

// ============================================================================
// CONSTANTS
// ============================================================================

const LABEL_COL = 240; // px colonna sinistra
const ROW_H = 36;      // px altezza riga
const HEADER_H = 40;   // px header date
const BAR_H = 22;      // px altezza barra
const BAR_TOP = (ROW_H - BAR_H) / 2;
const MIN_BAR_W = 4;   // px larghezza minima barra

const PX_PER_HOUR = 80;
const PX_PER_DAY = PX_PER_HOUR * 24;
const PX_PER_WEEK = PX_PER_DAY * 7;

const PX_PER_MS: Record<ZoomLevel, number> = {
  HOUR: PX_PER_HOUR / 3_600_000,
  DAY: PX_PER_DAY / 86_400_000,
  WEEK: PX_PER_WEEK / 604_800_000,
};

const TICK_MS: Record<ZoomLevel, number> = {
  HOUR: 3_600_000,
  DAY: 86_400_000,
  WEEK: 604_800_000,
};

// ============================================================================
// COLORS
// ============================================================================

const OP_COLOR: Record<string, { fill: string; text: string }> = {
  ELECTRICAL: { fill: "#3b82f6", text: "#fff" },
  MECHANICAL: { fill: "#f97316", text: "#fff" },
  GENERAL: { fill: "#22c55e", text: "#fff" },
};

const STATUS_STYLE: Record<GanttStatus, { opacity: number; outline: string }> = {
  SCHEDULED: { opacity: 0.85, outline: "none" },
  IN_PROGRESS: { opacity: 1.0, outline: "2px solid #1d4ed8" },
  COMPLETED: { opacity: 0.4, outline: "none" },
  BLOCKED: { opacity: 0.9, outline: "2px solid #dc2626" },
  INTERRUPTED: { opacity: 0.8, outline: "2px dashed #ea580c" },
  DELAYED: { opacity: 0.9, outline: "2px solid #dc2626" },
  STALE: { opacity: 0.25, outline: "none" },
};

const LEVEL_INDENT: Record<BomLevel, number> = {
  MACHINE: 0, MACROAGGREGATE: 10, AGGREGATE: 22, GROUP: 34,
};

const LEVEL_COLOR: Record<BomLevel, string> = {
  MACHINE: "#1e40af", MACROAGGREGATE: "#7c3aed", AGGREGATE: "#0f766e", GROUP: "#15803d",
};

// ============================================================================
// GROUPING
// ============================================================================

interface GanttRow {
  key: string;
  label: string;
  sublabel?: string;
  indent: number;
  labelColor?: string;
  entries: GanttEntry[];
}

function groupByOperator(entries: GanttEntry[]): GanttRow[] {
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.operator_id) ?? [];
    arr.push(e);
    map.set(e.operator_id, arr);
  }
  return [...map.values()]
    .map((ents) => ({
      key: ents[0].operator_id,
      label: ents[0].operator_name,
      sublabel: `${ents[0].operator_skill} · ${ents[0].workcenter_code}`,
      indent: 0,
      entries: ents,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function groupByWorkcenter(entries: GanttEntry[]): GanttRow[] {
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.workcenter_id) ?? [];
    arr.push(e);
    map.set(e.workcenter_id, arr);
  }
  return [...map.values()]
    .map((ents) => ({
      key: ents[0].workcenter_id,
      label: ents[0].workcenter_code,
      sublabel: ents[0].workcenter_name,
      indent: 0,
      entries: ents,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function groupByOrder(entries: GanttEntry[]): GanttRow[] {
  const byOrder = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = byOrder.get(e.production_order_id) ?? [];
    arr.push(e);
    byOrder.set(e.production_order_id, arr);
  }

  type ONode = { id: string; mat: string; desc: string; level: BomLevel; parent: string | null; entries: GanttEntry[] };
  const nodes = new Map<string, ONode>();
  for (const [id, ents] of byOrder) {
    const f = ents[0];
    nodes.set(id, { id, mat: f.production_order_material, desc: f.production_order_description, level: f.production_order_level, parent: f.parent_order_id, entries: ents });
  }

  const rows: GanttRow[] = [];
  function dfs(n: ONode) {
    rows.push({ key: n.id, label: n.mat, sublabel: n.desc, indent: LEVEL_INDENT[n.level] ?? 0, labelColor: LEVEL_COLOR[n.level], entries: n.entries });
    [...nodes.values()].filter((o) => o.parent === n.id).sort((a, b) => a.mat.localeCompare(b.mat)).forEach(dfs);
  }
  [...nodes.values()].filter((o) => !o.parent || !nodes.has(o.parent)).sort((a, b) => a.mat.localeCompare(b.mat)).forEach(dfs);
  return rows;
}

// ============================================================================
// AXIS
// ============================================================================

interface Tick { ms: number; label: string; major: boolean }

function buildAxis(minMs: number, maxMs: number, zoom: ZoomLevel) {
  const pxPerMs = PX_PER_MS[zoom];
  const tickMs = TICK_MS[zoom];
  const totalPx = Math.ceil((maxMs - minMs) * pxPerMs);
  const ticks: Tick[] = [];
  let t = Math.floor(minMs / tickMs) * tickMs;
  while (t <= maxMs + tickMs) {
    const d = new Date(t);
    let label = "";
    let major = false;
    if (zoom === "HOUR") {
      label = d.toLocaleString("it-IT", { hour: "2-digit", minute: "2-digit" });
    } else if (zoom === "DAY") {
      label = d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" });
      major = true;
    } else {
      const wn = Math.ceil(((d.getTime() - new Date(d.getFullYear(), 0, 1).getTime()) / 86400000 + new Date(d.getFullYear(), 0, 1).getDay() + 1) / 7);
      label = `W${wn} · ${d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}`;
      major = true;
    }
    ticks.push({ ms: t, label, major });
    t += tickMs;
  }
  return { pxPerMs, ticks, totalPx };
}

// ============================================================================
// POPUP
// ============================================================================

function EntryPopup({ entry, x, y, onClose }: { entry: GanttEntry; x: number; y: number; onClose: () => void }) {
  const c = OP_COLOR[entry.operation_type] ?? OP_COLOR.GENERAL;
  const fmt = (iso: string | null) => iso ? new Date(iso).toLocaleString("it-IT", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
  const left = Math.min(x + 12, window.innerWidth - 320);
  const top = Math.min(y - 8, window.innerHeight - 300);

  return (
    <div
      style={{ position: "fixed", left, top, width: 300, background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, boxShadow: "0 8px 24px rgba(0,0,0,0.15)", zIndex: 9999, padding: 14, fontSize: 12 }}
      onClick={(e) => e.stopPropagation()}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div>
          <span style={{ background: c.fill, color: c.text, fontSize: 10, fontWeight: 700, padding: "2px 6px", borderRadius: 4, display: "inline-block", marginBottom: 4 }}>{entry.operation_type}</span>
          <div style={{ fontWeight: 700, color: "#1e293b" }}>{entry.operation_description}</div>
          {entry.reference_point_code && <div style={{ fontSize: 10, color: "#7c3aed", fontWeight: 600 }}>[{entry.reference_point_code}]</div>}
        </div>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "#94a3b8", padding: 2 }}><X size={14} /></button>
      </div>
      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 10px", color: "#475569", margin: 0 }}>
        {[["Risorsa", entry.operator_name], ["Workcenter", `${entry.workcenter_code} — ${entry.workcenter_name}`], ["Start", fmt(entry.scheduled_start)], ["End", fmt(entry.scheduled_end)], ["Progresso", `${entry.progress_pct}%`], ["Stato", entry.status]].map(([k, v]) => (
          <>
            <dt key={`k${k}`} style={{ fontWeight: 600, whiteSpace: "nowrap" }}>{k}</dt>
            <dd key={`v${k}`} style={{ margin: 0 }}>{v}</dd>
          </>
        ))}
        {entry.is_critical_path && <><dt style={{ fontWeight: 600 }}>⚡</dt><dd style={{ margin: 0, color: "#ca8a04", fontWeight: 600 }}>Critical Path</dd></>}
        {entry.missing_components.length > 0 && <><dt style={{ fontWeight: 600 }}>⛔</dt><dd style={{ margin: 0, color: "#dc2626" }}>{entry.missing_components.join(", ")}</dd></>}
      </dl>
    </div>
  );
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export default function AdvancedGantt({
  entries = [],
  dependencies = [],
  rpMarkers = [],
  initialMode = "BY_OPERATOR",
  height = 600,
  externalZoom,
  onEntryClick,
}: AdvancedGanttProps) {
  const [mode, setMode] = useState<GroupingMode>(initialMode);
  const [zoom, setZoom] = useState<ZoomLevel>(externalZoom ?? "DAY");
  const [showDeps, setShowDeps] = useState(true);
  const [showRp, setShowRp] = useState(true);
  const [critOnly, setCritOnly] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  const [popup, setPopup] = useState<{ entry: GanttEntry; x: number; y: number } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (externalZoom) setZoom(externalZoom); }, [externalZoom]);

  useEffect(() => {
    if (!popup) return;
    const h = () => setPopup(null);
    window.addEventListener("click", h);
    return () => window.removeEventListener("click", h);
  }, [popup]);

  const filtered = useMemo(() => {
    const safe = Array.isArray(entries) ? entries : [];
    return critOnly ? safe.filter((e) => e.is_critical_path) : safe;
  }, [entries, critOnly]);

  const rows = useMemo(() => {
    if (mode === "BY_OPERATOR") return groupByOperator(filtered);
    if (mode === "BY_WORKCENTER") return groupByWorkcenter(filtered);
    return groupByOrder(filtered);
  }, [filtered, mode]);

  const { minMs, maxMs } = useMemo(() => {
    if (!filtered.length) { const n = Date.now(); return { minMs: n - 86_400_000, maxMs: n + 86_400_000 }; }
    const starts = filtered.map((e) => new Date(e.scheduled_start).getTime());
    const ends = filtered.map((e) => new Date(e.scheduled_end).getTime());
    const mn = Math.min(...starts), mx = Math.max(...ends), pad = (mx - mn) * 0.03;
    return { minMs: mn - pad, maxMs: mx + pad };
  }, [filtered]);

  const { pxPerMs, ticks, totalPx } = useMemo(() => buildAxis(minMs, maxMs, zoom), [minMs, maxMs, zoom]);

  const xFor = useCallback((iso: string) => Math.round((new Date(iso).getTime() - minMs) * pxPerMs), [minMs, pxPerMs]);
  const wFor = useCallback((s: string, e: string) => Math.max(MIN_BAR_W, Math.round((new Date(e).getTime() - new Date(s).getTime()) * pxPerMs)), [pxPerMs]);

  const nowX = useMemo(() => {
    const n = Date.now();
    if (n < minMs || n > maxMs) return null;
    return Math.round((n - minMs) * pxPerMs);
  }, [minMs, maxMs, pxPerMs]);

  // Scroll to "now" on mount
  useEffect(() => {
    if (nowX !== null && scrollRef.current) {
      scrollRef.current.scrollLeft = Math.max(0, nowX - scrollRef.current.clientWidth / 3);
    }
  }, [nowX]);

  // ── RENDER ────────────────────────────────────────────────────────────────
  // Usiamo una struttura table-like con celle sticky.
  // Ogni "riga" è un div con display:flex:
  //   - Cella sinistra: width:LABEL_COL, position:sticky, left:0 → label sempre visibile
  //   - Cella destra: width:totalPx → barre gantt
  // Lo scroll avviene su UN SOLO contenitore → allineamento garantito dal DOM.

  const containerH = typeof height === "number" ? height : height ?? 600;

  return (
    <TooltipProvider delay={120}>
      <div style={{ display: "flex", flexDirection: "column", height: containerH, border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden", background: "#fff", fontFamily: "system-ui, sans-serif" }}>

        {/* ── TOOLBAR ─────────────────────────────────────────────────────── */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", borderBottom: "1px solid #e2e8f0", background: "#f8fafc", flexShrink: 0, flexWrap: "wrap" }}>
          {(["BY_OPERATOR", "BY_WORKCENTER", "BY_ORDER"] as GroupingMode[]).map((m) => (
            <button key={m} onClick={() => setMode(m)} style={{ padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer", background: mode === m ? "#1e40af" : "transparent", color: mode === m ? "#fff" : "#475569", fontWeight: mode === m ? 700 : 400 }}>
              {m === "BY_OPERATOR" ? "Risorse" : m === "BY_WORKCENTER" ? "Workcenter" : "Ordini BOM"}
            </button>
          ))}
          <div style={{ width: 1, height: 20, background: "#e2e8f0" }} />
          {(["HOUR", "DAY", "WEEK"] as ZoomLevel[]).map((z) => (
            <button key={z} onClick={() => setZoom(z)} style={{ padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer", background: zoom === z ? "#0f766e" : "transparent", color: zoom === z ? "#fff" : "#475569", fontWeight: zoom === z ? 700 : 400 }}>
              {z === "HOUR" ? "Ora" : z === "DAY" ? "Giorno" : "Settimana"}
            </button>
          ))}
          <div style={{ width: 1, height: 20, background: "#e2e8f0" }} />
          <button onClick={() => setShowDeps((v) => !v)} style={{ padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer", background: showDeps ? "#f0fdf4" : "transparent", color: showDeps ? "#15803d" : "#94a3b8" }}>Dipendenze</button>
          <button onClick={() => setShowRp((v) => !v)} style={{ padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer", background: showRp ? "#f5f3ff" : "transparent", color: showRp ? "#7c3aed" : "#94a3b8" }}>Marker RP</button>
          <button onClick={() => setCritOnly((v) => !v)} style={{ padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer", background: critOnly ? "#fefce8" : "transparent", color: critOnly ? "#a16207" : "#94a3b8" }}>Solo critical path</button>
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 11, color: "#94a3b8" }}>{filtered.length} op. · {rows.length} righe</span>
        </div>

        {/* ── CORPO — UNICO scroll container ──────────────────────────────── */}
        {/*
          ARCHITETTURA v5:
          Un solo div con overflow:auto scrolla sia X che Y.
          Ogni riga è un div display:flex con:
            - cella-label: width LABEL_COL, position sticky left:0, zIndex 2
            - cella-chart: width totalPx, position relative
          L'header è una riga speciale con position sticky top:0, zIndex 3.
          L'angolo (header-label) è sticky su entrambi gli assi con zIndex 4.
          Niente JS per sincronizzare scroll: è fisicamente impossibile che
          la cella label e la cella chart di una stessa riga siano disallineate.
        */}
        <div
          ref={scrollRef}
          style={{
            flex: 1,
            overflow: "auto",
            position: "relative",
          }}
        >
          {/* Contenitore interno: larga abbastanza da contenere header + righe */}
          <div style={{ minWidth: LABEL_COL + totalPx, width: LABEL_COL + totalPx }}>

            {/* ── HEADER ROW (sticky top) ────────────────────────────────── */}
            <div style={{ display: "flex", position: "sticky", top: 0, zIndex: 10, height: HEADER_H }}>

              {/* Angolo fisso (sticky left + sticky top) */}
              <div style={{
                width: LABEL_COL, flexShrink: 0,
                position: "sticky", left: 0, zIndex: 11,
                background: "#f8fafc",
                borderRight: "1px solid #e2e8f0",
                borderBottom: "1px solid #e2e8f0",
                display: "flex", alignItems: "center", paddingLeft: 10,
              }}>
                <span style={{ fontSize: 10, color: "#94a3b8", fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" }}>
                  {mode === "BY_OPERATOR" ? "Risorsa" : mode === "BY_WORKCENTER" ? "Workcenter" : "Ordine BOM"}
                </span>
              </div>

              {/* Header date (scorre con X) */}
              <div style={{ width: totalPx, flexShrink: 0, position: "relative", background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                {ticks.map((tick) => {
                  const x = Math.round((tick.ms - minMs) * pxPerMs);
                  if (x < 0 || x > totalPx) return null;
                  return (
                    <div key={tick.ms} style={{ position: "absolute", left: x, top: 0, height: "100%", borderLeft: tick.major ? "1px solid #cbd5e1" : "1px solid #e2e8f0", paddingLeft: 4, display: "flex", alignItems: "center", whiteSpace: "nowrap" }}>
                      <span style={{ fontSize: 10, color: "#64748b", fontWeight: tick.major ? 600 : 400 }}>{tick.label}</span>
                    </div>
                  );
                })}
                {nowX !== null && (
                  <div style={{ position: "absolute", left: nowX, top: 2, zIndex: 1 }}>
                    <div style={{ background: "#ef4444", color: "#fff", fontSize: 9, fontWeight: 700, padding: "2px 5px", borderRadius: 3, whiteSpace: "nowrap" }}>ORA</div>
                  </div>
                )}
              </div>
            </div>

            {/* ── DATA ROWS ─────────────────────────────────────────────── */}
            {rows.map((row, ri) => (
              <div key={row.key} style={{ display: "flex", height: ROW_H, background: ri % 2 === 0 ? "transparent" : "rgba(0,0,0,0.018)", borderBottom: "1px solid #f1f5f9" }}>

                {/* ── CELLA LABEL (sticky left) ── */}
                <div style={{
                  width: LABEL_COL, flexShrink: 0,
                  position: "sticky", left: 0, zIndex: 2,
                  background: ri % 2 === 0 ? "#fff" : "#fafafa",
                  borderRight: "1px solid #e2e8f0",
                  display: "flex", flexDirection: "column", justifyContent: "center",
                  padding: `0 8px 0 ${8 + row.indent}px`,
                  overflow: "hidden",
                }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: row.labelColor ?? "#1e293b", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", lineHeight: 1.2 }}>
                    {row.label}
                  </span>
                  {row.sublabel && (
                    <span style={{ fontSize: 10, color: "#94a3b8", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {row.sublabel}
                    </span>
                  )}
                </div>

                {/* ── CELLA CHART ── */}
                <div style={{ width: totalPx, flexShrink: 0, position: "relative", height: ROW_H }}>

                  {/* Linee verticali tick */}
                  {ticks.map((tick) => {
                    const x = Math.round((tick.ms - minMs) * pxPerMs);
                    if (x < 0 || x > totalPx) return null;
                    return (
                      <div key={tick.ms} style={{ position: "absolute", left: x, top: 0, height: "100%", borderLeft: tick.major ? "1px solid #e2e8f0" : "1px solid #f1f5f9", pointerEvents: "none" }} />
                    );
                  })}

                  {/* Linea "oggi" */}
                  {nowX !== null && (
                    <div style={{ position: "absolute", left: nowX, top: 0, width: 2, height: "100%", background: "#ef4444", opacity: 0.35, pointerEvents: "none", zIndex: 3 }} />
                  )}

                  {/* Marker RP */}
                  {showRp && rpMarkers.map((m, i) => {
                    const mx = Math.round((new Date(m.completion_time).getTime() - minMs) * pxPerMs);
                    if (mx < 0 || mx > totalPx) return null;
                    return (
                      <div key={i} style={{ position: "absolute", left: mx, top: 0, width: 1, height: "100%", background: "#7c3aed", opacity: 0.4, borderLeft: "1px dashed #7c3aed", pointerEvents: "none", zIndex: 3 }}>
                        {ri === 0 && <div style={{ position: "absolute", top: 2, left: 2, background: "#7c3aed", color: "#fff", fontSize: 8, fontWeight: 700, padding: "1px 3px", borderRadius: 3, whiteSpace: "nowrap" }}>{m.rp_code}</div>}
                      </div>
                    );
                  })}

                  {/* Barre operazioni */}
                  {row.entries.map((entry) => {
                    const bx = xFor(entry.scheduled_start);
                    const bw = wFor(entry.scheduled_start, entry.scheduled_end);
                    const col = OP_COLOR[entry.operation_type] ?? OP_COLOR.GENERAL;
                    const sty = STATUS_STYLE[entry.status] ?? STATUS_STYLE.SCHEDULED;
                    const isHov = hovered === entry.id;
                    const isCrit = entry.is_critical_path;

                    return (
                          <div
                            key={entry.id}
                            title={`${entry.operation_description}\n${entry.operator_name}\n${new Date(entry.scheduled_start).toLocaleDateString("it-IT")} → ${new Date(entry.scheduled_end).toLocaleDateString("it-IT")}`}
                            style={{
                              position: "absolute",
                              left: bx,
                              top: BAR_TOP,
                              width: bw,
                              height: BAR_H,
                              background: col.fill,
                              opacity: sty.opacity,
                              outline: isCrit ? "2px solid #ca8a04" : sty.outline,
                              borderRadius: 4,
                              cursor: "pointer",
                              overflow: "hidden",
                              display: "flex",
                              alignItems: "center",
                              boxShadow: isHov ? "0 2px 8px rgba(0,0,0,0.25)" : "0 1px 2px rgba(0,0,0,0.1)",
                              transform: isHov ? "scaleY(1.08)" : "none",
                              transition: "transform 0.1s, box-shadow 0.1s",
                              zIndex: isHov ? 5 : 1,
                            }}
                            onMouseEnter={() => setHovered(entry.id)}
                            onMouseLeave={() => setHovered(null)}
                            onClick={(e) => {
                              e.stopPropagation();
                              setPopup({ entry, x: e.clientX, y: e.clientY });
                              onEntryClick?.(entry);
                            }}
                          >
                            {entry.progress_pct > 0 && (
                              <div style={{ position: "absolute", left: 0, top: 0, width: `${Math.min(100, entry.progress_pct)}%`, height: "100%", background: "rgba(255,255,255,0.3)", borderRight: "1px solid rgba(255,255,255,0.5)" }} />
                            )}
                            {bw > 50 && (
                              <span style={{ position: "relative", padding: "0 5px", fontSize: 10, fontWeight: 600, color: col.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: bw - 10, zIndex: 2, textShadow: "0 0 3px rgba(0,0,0,0.3)" }}>
                                {entry.reference_point_code ? `[${entry.reference_point_code}]` : entry.operation_description}
                              </span>
                            )}
                          </div>
                    );
                  })}
                </div>
              </div>
            ))}

            {/* ── SVG frecce dipendenza (overlay sull'intera area chart) ── */}
            {showDeps && dependencies.length > 0 && (() => {
              // Mappa entry_id → { riga, xStart, xEnd }
              const pos = new Map<string, { ri: number; xs: number; xe: number }>();
              rows.forEach((row, ri) => {
                row.entries.forEach((e) => {
                  pos.set(e.id, { ri, xs: xFor(e.scheduled_start), xe: xFor(e.scheduled_end) });
                });
              });
              const svgH = rows.length * ROW_H;
              return (
                <div style={{ position: "relative", height: 0, overflow: "visible" }}>
                  <svg
                    style={{ position: "absolute", left: LABEL_COL, top: -(svgH), width: totalPx, height: svgH, pointerEvents: "none", zIndex: 6, overflow: "visible" }}
                  >
                    <defs>
                      <marker id="ag-arrow" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
                        <path d="M0,0 L0,6 L6,3 z" fill="#7c3aed" opacity="0.7" />
                      </marker>
                    </defs>
                    {dependencies.map((dep, i) => {
                      const f = pos.get(dep.from_entry_id);
                      const t = pos.get(dep.to_entry_id);
                      if (!f || !t) return null;
                      const x1 = f.xe, y1 = f.ri * ROW_H + ROW_H / 2;
                      const x2 = t.xs, y2 = t.ri * ROW_H + ROW_H / 2;
                      const mx = (x1 + x2) / 2;
                      return (
                        <path key={i} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none"
                          stroke={dep.source === "RP_DAG" ? "#7c3aed" : "#94a3b8"}
                          strokeWidth={dep.source === "RP_DAG" ? 1.5 : 1}
                          strokeDasharray={dep.source === "RP_DAG" ? "none" : "4 3"}
                          opacity="0.6" markerEnd="url(#ag-arrow)" />
                      );
                    })}
                  </svg>
                </div>
              );
            })()}
          </div>
        </div>

        {/* ── LEGENDA ─────────────────────────────────────────────────────── */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "5px 12px", borderTop: "1px solid #e2e8f0", background: "#f8fafc", flexShrink: 0, flexWrap: "wrap" }}>
          {Object.entries(OP_COLOR).map(([type, c]) => (
            <div key={type} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <div style={{ width: 11, height: 11, borderRadius: 2, background: c.fill }} />
              <span style={{ fontSize: 10, color: "#64748b" }}>{type}</span>
            </div>
          ))}
          <div style={{ width: 1, height: 12, background: "#e2e8f0" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 11, height: 11, borderRadius: 2, background: "#facc15", outline: "2px solid #ca8a04" }} />
            <span style={{ fontSize: 10, color: "#64748b" }}>Critical path</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 11, height: 11, borderRadius: 2, background: "#dc2626", opacity: 0.6 }} />
            <span style={{ fontSize: 10, color: "#64748b" }}>Bloccato/Ritardo</span>
          </div>
        </div>
      </div>

      {popup && <EntryPopup entry={popup.entry} x={popup.x} y={popup.y} onClose={() => setPopup(null)} />}
    </TooltipProvider>
  );
}