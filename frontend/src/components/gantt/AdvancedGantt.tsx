// frontend/src/components/gantt/AdvancedGantt.tsx
//
// v3 — Fix applicati:
//   BUG 1 (sidebar non scrolla): aggiunto labelColRef + listener scroll che
//          sincronizza labelColRef.current.scrollTop = scrollRef.current.scrollTop
//   BUG 2 (barre tra le righe): tutte le righe (sidebar + chart) usano
//          boxSizing:"border-box" + borderBottom incluso in ROW_H → nessun drift
//
// Invarianti rispettate:
//   - La sidebar NON mostra scrollbar (overflowY:"hidden") ma segue il chart
//   - Il calcolo top: HEADER_H + ri * ROW_H rimane preciso perché border è
//     dentro ROW_H grazie a box-sizing:border-box
//   - L'export PNG cattura exportRef che wrappa entrambe le colonne

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { toPng } from "html-to-image";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Download, HelpCircle, X } from "lucide-react";

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
  height?: number;
  externalZoom?: ZoomLevel;
  onEntryClick?: (entry: GanttEntry) => void;
}

// ============================================================================
// CONSTANTS
// ============================================================================

const LABEL_COL = 240;    // px colonna sinistra fissa
const ROW_H     = 36;     // px altezza riga — il border-bottom è DENTRO (box-sizing:border-box)
const HEADER_H  = 48;     // px header date
const MIN_BAR_W = 4;      // px larghezza minima barra
const BAR_H     = 22;     // px altezza barra
const BAR_TOP   = (ROW_H - BAR_H) / 2;

const PX_PER_HOUR = 80;
const PX_PER_DAY  = PX_PER_HOUR * 24;
const PX_PER_WEEK = PX_PER_DAY * 7;

const PX_PER_MS: Record<ZoomLevel, number> = {
  HOUR: PX_PER_HOUR / 3_600_000,
  DAY:  PX_PER_DAY  / 86_400_000,
  WEEK: PX_PER_WEEK / 604_800_000,
};

const TICK_MS: Record<ZoomLevel, number> = {
  HOUR:  3_600_000,
  DAY:   86_400_000,
  WEEK:  604_800_000,
};

// ============================================================================
// COLORS
// ============================================================================

const OP_TYPE_COLOR: Record<string, { fill: string; text: string }> = {
  ELECTRICAL: { fill: "#3b82f6", text: "#fff" },
  MECHANICAL: { fill: "#f97316", text: "#fff" },
  GENERAL:    { fill: "#22c55e", text: "#fff" },
};

const STATUS_OVERLAY: Record<GanttStatus, { opacity: number; border: string }> = {
  SCHEDULED:   { opacity: 0.85, border: "none" },
  IN_PROGRESS: { opacity: 1.0,  border: "2px solid #1d4ed8" },
  COMPLETED:   { opacity: 0.45, border: "none" },
  BLOCKED:     { opacity: 0.9,  border: "2px solid #dc2626" },
  INTERRUPTED: { opacity: 0.8,  border: "2px dashed #ea580c" },
  DELAYED:     { opacity: 0.9,  border: "2px solid #dc2626" },
  STALE:       { opacity: 0.3,  border: "none" },
};

const LEVEL_INDENT: Record<BomLevel, number> = {
  MACHINE: 0, MACROAGGREGATE: 12, AGGREGATE: 24, GROUP: 36,
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
  if (!Array.isArray(entries)) return [];
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.operator_id) ?? [];
    arr.push(e);
    map.set(e.operator_id, arr);
  }
  return [...map.entries()]
    .map(([, ents]) => {
      const f = ents[0];
      return {
        key: f.operator_id,
        label: f.operator_name,
        sublabel: `${f.operator_skill} · ${f.workcenter_code}`,
        indent: 0,
        entries: ents.sort((a, b) => a.scheduled_start.localeCompare(b.scheduled_start)),
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

function groupByWorkcenter(entries: GanttEntry[]): GanttRow[] {
  if (!Array.isArray(entries)) return [];
  const map = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = map.get(e.workcenter_id) ?? [];
    arr.push(e);
    map.set(e.workcenter_id, arr);
  }
  return [...map.entries()]
    .map(([, ents]) => {
      const f = ents[0];
      return {
        key: f.workcenter_id,
        label: f.workcenter_code,
        sublabel: f.workcenter_name,
        indent: 0,
        entries: ents.sort((a, b) => a.scheduled_start.localeCompare(b.scheduled_start)),
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

function groupByOrder(entries: GanttEntry[]): GanttRow[] {
  if (!Array.isArray(entries)) return [];
  const byOrder = new Map<string, GanttEntry[]>();
  for (const e of entries) {
    const arr = byOrder.get(e.production_order_id) ?? [];
    arr.push(e);
    byOrder.set(e.production_order_id, arr);
  }

  type ONode = {
    id: string;
    material: string;
    desc: string;
    level: BomLevel;
    parent: string | null;
    entries: GanttEntry[];
  };
  const orders = new Map<string, ONode>();
  for (const [oid, ents] of byOrder) {
    const f = ents[0];
    orders.set(oid, {
      id: oid,
      material: f.production_order_material,
      desc: f.production_order_description,
      level: f.production_order_level,
      parent: f.parent_order_id,
      entries: ents,
    });
  }

  const rows: GanttRow[] = [];
  function dfs(node: ONode): void {
    rows.push({
      key: node.id,
      label: node.material,
      sublabel: node.desc,
      indent: LEVEL_INDENT[node.level] ?? 0,
      labelColor: LEVEL_COLOR[node.level],
      entries: node.entries.sort((a, b) =>
        a.scheduled_start.localeCompare(b.scheduled_start)
      ),
    });
    [...orders.values()]
      .filter((o) => o.parent === node.id)
      .sort((a, b) => a.material.localeCompare(b.material))
      .forEach(dfs);
  }
  [...orders.values()]
    .filter((o) => o.parent === null || !orders.has(o.parent!))
    .sort((a, b) => a.material.localeCompare(b.material))
    .forEach(dfs);
  return rows;
}

// ============================================================================
// AXIS BUILDER
// ============================================================================

interface Tick {
  ms: number;
  label: string;
  isDay: boolean;
}

function buildAxis(
  minMs: number,
  maxMs: number,
  zoom: ZoomLevel
): { pxPerMs: number; ticks: Tick[]; totalPx: number } {
  const pxPerMs = PX_PER_MS[zoom];
  const tickMs  = TICK_MS[zoom];
  const totalPx = (maxMs - minMs) * pxPerMs;

  const ticks: Tick[] = [];
  let t = Math.floor(minMs / tickMs) * tickMs;
  while (t <= maxMs) {
    const d = new Date(t);
    let label = "";
    let isDay = false;
    if (zoom === "HOUR") {
      label = d.toLocaleString("it-IT", { hour: "2-digit", minute: "2-digit" });
    } else if (zoom === "DAY") {
      label = d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" });
      isDay = true;
    } else {
      label = `W${getWeekNumber(d)} — ${d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}`;
      isDay = true;
    }
    ticks.push({ ms: t, label, isDay });
    t += tickMs;
  }
  return { pxPerMs, ticks, totalPx };
}

function getWeekNumber(d: Date): number {
  const onejan = new Date(d.getFullYear(), 0, 1);
  return Math.ceil(((d.getTime() - onejan.getTime()) / 86400000 + onejan.getDay() + 1) / 7);
}

// ============================================================================
// LEGEND BAR
// ============================================================================

function LegendBar({ entries }: { entries: GanttEntry[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "5px 12px",
        borderTop: "1px solid #e2e8f0",
        background: "#f8fafc",
        flexWrap: "wrap",
        flexShrink: 0,
      }}
    >
      {Object.entries(OP_TYPE_COLOR).map(([type, c]) => (
        <div key={type} style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <div style={{ width: 12, height: 12, borderRadius: 2, background: c.fill }} />
          <span style={{ fontSize: 10, color: "#64748b" }}>{type}</span>
        </div>
      ))}

      <div style={{ width: 1, height: 14, background: "#e2e8f0" }} />

      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <div
          style={{
            width: 12,
            height: 12,
            borderRadius: 2,
            background: "#3b82f6",
            border: "2px solid #f59e0b",
          }}
        />
        <span style={{ fontSize: 10, color: "#64748b" }}>⭐ Critical path</span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <div style={{ width: 14, height: 2, background: "#ef4444" }} />
        <span style={{ fontSize: 10, color: "#64748b" }}>Ora</span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <div style={{ width: 1, height: 12, borderLeft: "2px dashed #7c3aed" }} />
        <span style={{ fontSize: 10, color: "#7c3aed", fontWeight: 600 }}>Marker RP</span>
      </div>

      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 10, color: "#cbd5e1" }}>{entries.length} operazioni</span>

        <div style={{ position: "relative" }}>
          <button
            onClick={() => setOpen((v) => !v)}
            title="Legenda dettagliata"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 8px",
              borderRadius: 6,
              border: `1px solid ${open ? "#6366f1" : "#e2e8f0"}`,
              background: open ? "#eef2ff" : "#fff",
              color: open ? "#6366f1" : "#64748b",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            <HelpCircle size={12} />
            {open ? "Chiudi" : "Legenda"}
          </button>

          {open && (
            <div
              style={{
                position: "absolute",
                bottom: "100%",
                right: 0,
                width: 320,
                background: "#fff",
                border: "1px solid #e2e8f0",
                borderRadius: 10,
                padding: 14,
                boxShadow: "0 8px 30px rgba(0,0,0,0.12)",
                zIndex: 100,
                marginBottom: 6,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 10,
                }}
              >
                <span style={{ fontSize: 12, fontWeight: 700 }}>Come leggere il Gantt</span>
                <button
                  onClick={() => setOpen(false)}
                  style={{ background: "none", border: "none", cursor: "pointer", color: "#94a3b8" }}
                >
                  <X size={14} />
                </button>
              </div>

              <div style={{ fontSize: 11, color: "#475569", lineHeight: 1.7 }}>
                <p>
                  <strong>Colore barra</strong> — tipo operazione: blu=ELECTRICAL,
                  arancione=MECHANICAL, verde=GENERAL
                </p>
                <p>
                  <strong>Bordo barra</strong> — status: blu pieno=IN_PROGRESS,
                  rosso=BLOCKED/DELAYED, tratteggiato=INTERRUPTED
                </p>
                <p>
                  <strong>Opacità ridotta</strong> — operazione COMPLETED o STALE
                </p>
                <p>
                  <strong>Overlay bianco</strong> — % avanzamento (progress_pct)
                </p>
                <p>
                  <strong>Bordo dorato</strong> — operazione sul critical path
                </p>
                <p>
                  <strong>Linea viola tratteggiata</strong> — Reference Point (RP): segna
                  quando un sotto-ordine deve essere completato prima che il padre possa
                  proseguire
                </p>
                <p>
                  <strong>Frecce viola</strong> — dipendenze RP_DAG; frecce grigie
                  tratteggiate — dipendenze intra-routing
                </p>
              </div>

              <div
                style={{
                  marginTop: 10,
                  padding: "7px 10px",
                  background: "#f8fafc",
                  border: "1px solid #e2e8f0",
                  borderRadius: 6,
                  fontSize: 10,
                  color: "#64748b",
                }}
              >
                💡 Le frecce cambiano percorso cambiando la modalità di raggruppamento
                perché le righe si riorganizzano. Il vincolo rimane lo stesso.
              </div>
            </div>
          )}
        </div>
      </div>
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
}: AdvancedGanttProps): JSX.Element {
  const [mode, setMode] = useState<GroupingMode>(initialMode);
  const [zoom, setZoom] = useState<ZoomLevel>(externalZoom ?? "DAY");

  useEffect(() => {
    if (externalZoom) setZoom(externalZoom);
  }, [externalZoom]);

  const [showDeps, setShowDeps]   = useState(true);
  const [showRpM,  setShowRpM]    = useState(true);
  const [critOnly, setCritOnly]   = useState(false);
  const [hoveredBar, setHoveredBar] = useState<string | null>(null);

  // ── FIX BUG 1: due ref, scroll sincronizzato ──────────────────────────────
  const scrollRef   = useRef<HTMLDivElement>(null);   // area chart (scroll reale)
  const labelColRef = useRef<HTMLDivElement>(null);   // sidebar (scroll pilotato via JS)
  const exportRef   = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scrollEl = scrollRef.current;
    const labelEl  = labelColRef.current;
    if (!scrollEl || !labelEl) return;

    const onScroll = () => {
      // Sincronizza scrollTop: la sidebar segue il chart senza mostrare scrollbar
      labelEl.scrollTop = scrollEl.scrollTop;
    };
    scrollEl.addEventListener("scroll", onScroll, { passive: true });
    return () => scrollEl.removeEventListener("scroll", onScroll);
  }, []);

  // Filtra
  const filtered = useMemo(() => {
    const safe = Array.isArray(entries) ? entries : [];
    return critOnly ? safe.filter((e) => e.is_critical_path) : safe;
  }, [entries, critOnly]);

  // Righe
  const rows = useMemo(() => {
    if (mode === "BY_OPERATOR")   return groupByOperator(filtered);
    if (mode === "BY_WORKCENTER") return groupByWorkcenter(filtered);
    return groupByOrder(filtered);
  }, [filtered, mode]);

  // Range temporale
  const { minMs, maxMs } = useMemo(() => {
    if (filtered.length === 0) {
      const now = Date.now();
      return { minMs: now - 86_400_000, maxMs: now + 86_400_000 };
    }
    const starts = filtered.map((e) => new Date(e.scheduled_start).getTime());
    const ends   = filtered.map((e) => new Date(e.scheduled_end).getTime());
    const mn = Math.min(...starts);
    const mx = Math.max(...ends);
    const pad = (mx - mn) * 0.03;
    return { minMs: mn - pad, maxMs: mx + pad };
  }, [filtered]);

  const { pxPerMs, ticks, totalPx } = useMemo(
    () => buildAxis(minMs, maxMs, zoom),
    [minMs, maxMs, zoom]
  );

  const xFor = useCallback(
    (iso: string) => (new Date(iso).getTime() - minMs) * pxPerMs,
    [minMs, pxPerMs]
  );
  const wFor = useCallback(
    (start: string, end: string) =>
      Math.max(MIN_BAR_W, (new Date(end).getTime() - new Date(start).getTime()) * pxPerMs),
    [pxPerMs]
  );

  const nowX = useMemo(() => {
    const now = Date.now();
    if (now < minMs || now > maxMs) return null;
    return (now - minMs) * pxPerMs;
  }, [minMs, maxMs, pxPerMs]);

  // Scroll a "oggi" all'avvio
  useEffect(() => {
    if (nowX !== null && scrollRef.current) {
      const viewW = scrollRef.current.clientWidth;
      scrollRef.current.scrollLeft = Math.max(0, nowX - viewW / 3);
    }
  }, [nowX]);

  const exportPng = useCallback(async () => {
    if (!exportRef.current) return;
    const url = await toPng(exportRef.current, { backgroundColor: "#ffffff" });
    const a = document.createElement("a");
    a.download = `gantt-${new Date().toISOString().slice(0, 10)}.png`;
    a.href = url;
    a.click();
  }, []);

  // ── FIX BUG 2: altezza totale calcolata con ROW_H che include il border ──
  // Grazie a box-sizing:border-box nelle righe, 1px di border NON si aggiunge
  // ai 36px → il calcolo top: HEADER_H + ri * ROW_H rimane esatto
  const totalH = HEADER_H + rows.length * ROW_H;

  // ── STILE RIGA condiviso (stesso in sidebar e chart) ─────────────────────
  const rowStyle = (ri: number): React.CSSProperties => ({
    height: ROW_H,
    boxSizing: "border-box",                       // ← FIX BUG 2
    borderBottom: "1px solid #f1f5f9",
    background: ri % 2 === 0 ? "transparent" : "rgba(0,0,0,0.018)",
  });

  // ── RENDER ────────────────────────────────────────────────────────────────
  return (
    <TooltipProvider delay={120}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: height ?? "100%",
          background: "#fff",
          borderRadius: 10,
          border: "1px solid #e2e8f0",
          overflow: "hidden",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        {/* ── Toolbar ── */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 10px",
            borderBottom: "1px solid #e2e8f0",
            background: "#f8fafc",
            flexShrink: 0,
            flexWrap: "wrap",
          }}
        >
          {/* Raggruppamento */}
          {(["BY_OPERATOR", "BY_WORKCENTER", "BY_ORDER"] as GroupingMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                cursor: "pointer",
                background: mode === m ? "#1e40af" : "transparent",
                color: mode === m ? "#fff" : "#64748b",
                fontWeight: mode === m ? 600 : 400,
              }}
            >
              {m === "BY_OPERATOR" ? "Operatori" : m === "BY_WORKCENTER" ? "Workcenter" : "Ordini BOM"}
            </button>
          ))}

          <div style={{ width: 1, height: 20, background: "#e2e8f0", margin: "0 2px" }} />

          {/* Zoom */}
          {(["HOUR", "DAY", "WEEK"] as ZoomLevel[]).map((z) => (
            <button
              key={z}
              onClick={() => setZoom(z)}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                cursor: "pointer",
                background: zoom === z ? "#475569" : "transparent",
                color: zoom === z ? "#fff" : "#64748b",
                fontWeight: zoom === z ? 600 : 400,
              }}
            >
              {z === "HOUR" ? "Ora" : z === "DAY" ? "Giorno" : "Settimana"}
            </button>
          ))}

          <div style={{ width: 1, height: 20, background: "#e2e8f0", margin: "0 2px" }} />

          <button
            onClick={() => setShowDeps((v) => !v)}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              cursor: "pointer",
              background: showDeps ? "#f0fdf4" : "transparent",
              color: showDeps ? "#15803d" : "#94a3b8",
            }}
          >
            Dipendenze
          </button>

          <button
            onClick={() => setShowRpM((v) => !v)}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              cursor: "pointer",
              background: showRpM ? "#f5f3ff" : "transparent",
              color: showRpM ? "#7c3aed" : "#94a3b8",
            }}
          >
            Marker RP
          </button>

          <button
            onClick={() => setCritOnly((v) => !v)}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              cursor: "pointer",
              background: critOnly ? "#fefce8" : "transparent",
              color: critOnly ? "#a16207" : "#94a3b8",
            }}
          >
            Solo critical path
          </button>

          <div style={{ flex: 1 }} />

          <span style={{ fontSize: 11, color: "#94a3b8" }}>
            {filtered.length} op. · {rows.length} righe
          </span>

          <Button size="sm" variant="outline" onClick={exportPng} style={{ height: 28, fontSize: 11 }}>
            <Download size={12} style={{ marginRight: 4 }} /> PNG
          </Button>
        </div>

        {/* ── Corpo ── */}
        <div
          ref={exportRef}
          style={{ display: "flex", flex: 1, overflow: "hidden", minHeight: 0 }}
        >
          {/* ── SIDEBAR SINISTRA (FIX BUG 1) ── */}
          <div
            ref={labelColRef}
            style={{
              width: LABEL_COL,
              flexShrink: 0,
              borderRight: "1px solid #e2e8f0",
              // overflowY:"hidden" nasconde la scrollbar ma scrollTop è pilotato dal listener
              overflowY: "hidden",
              background: "#fff",
              // display:flex+flexDirection:column per garantire le righe occupino esattamente ROW_H
              display: "flex",
              flexDirection: "column",
            }}
          >
            {/* Header placeholder — stessa altezza dell'header date.
                CRITICO: boxSizing border-box obbligatorio.
                Senza, borderBottom aggiunge 1px extra → 1px di drift per riga. */}
            <div
              style={{
                height: HEADER_H,
                boxSizing: "border-box",   // ← FIX ALLINEAMENTO: border incluso in HEADER_H
                flexShrink: 0,
                borderBottom: "1px solid #e2e8f0",
                background: "#f8fafc",
                display: "flex",
                alignItems: "center",
                paddingLeft: 10,
              }}
            >
              <span style={{ fontSize: 10, color: "#94a3b8", fontWeight: 600, letterSpacing: "0.05em" }}>
                {mode === "BY_OPERATOR" ? "OPERATORE" : mode === "BY_WORKCENTER" ? "WORKCENTER" : "ORDINE BOM"}
              </span>
            </div>

            {/* Righe etichette — stessa struttura di altezza delle righe chart */}
            {rows.map((row, ri) => (
              <div
                key={row.key}
                style={{
                  ...rowStyle(ri),
                  flexShrink: 0,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  padding: `0 8px 0 ${8 + row.indent}px`,
                  overflow: "hidden",
                }}
              >
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: row.labelColor ?? "#1e293b",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    lineHeight: 1.2,
                  }}
                >
                  {row.label}
                </span>
                {row.sublabel && (
                  <span
                    style={{
                      fontSize: 10,
                      color: "#94a3b8",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {row.sublabel}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* ── AREA CHART (scroll reale) ── */}
          <div
            ref={scrollRef}
            style={{
              flex: 1,
              overflowX: "auto",
              overflowY: "auto",   // scroll verticale reale — la sidebar lo segue
              position: "relative",
              minHeight: 0,
            }}
          >
            {/* Contenitore interno: larghezza = range temporale, altezza = totale righe */}
            <div
              style={{
                minWidth: totalPx,
                width: totalPx,
                position: "relative",
                height: totalH,
              }}
            >
              {/* ── Header date (sticky top) ── */}
              <div
                style={{
                  position: "sticky",
                  top: 0,
                  zIndex: 20,
                  height: HEADER_H,
                  boxSizing: "border-box",
                  background: "#f8fafc",
                  borderBottom: "1px solid #e2e8f0",
                }}
              >
                {ticks.map((tick) => {
                  const x = (tick.ms - minMs) * pxPerMs;
                  return (
                    <div
                      key={tick.ms}
                      style={{
                        position: "absolute",
                        left: x,
                        top: 0,
                        height: "100%",
                        borderLeft: tick.isDay ? "1px solid #cbd5e1" : "1px solid #e2e8f0",
                        paddingLeft: 4,
                        display: "flex",
                        alignItems: "center",
                        whiteSpace: "nowrap",
                      }}
                    >
                      <span
                        style={{
                          fontSize: 10,
                          color: "#64748b",
                          fontWeight: tick.isDay ? 600 : 400,
                        }}
                      >
                        {tick.label}
                      </span>
                    </div>
                  );
                })}

                {/* Badge ORA nell'header */}
                {nowX !== null && (
                  <div
                    style={{
                      position: "absolute",
                      left: nowX,
                      top: 0,
                      height: "100%",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                    }}
                  >
                    <div
                      style={{
                        background: "#ef4444",
                        color: "#fff",
                        fontSize: 9,
                        fontWeight: 700,
                        padding: "2px 5px",
                        borderRadius: 3,
                        marginTop: 6,
                        whiteSpace: "nowrap",
                      }}
                    >
                      ORA
                    </div>
                  </div>
                )}
              </div>

              {/* ── Righe chart — FIX BUG 2: box-sizing:border-box uniforme ── */}
              {rows.map((row, ri) => {
                // top calcolato DOPO l'header, con ri * ROW_H esatto
                // perché il border-bottom è dentro ROW_H (box-sizing:border-box)
                const y = HEADER_H + ri * ROW_H;
                return (
                  <div
                    key={row.key}
                    style={{
                      position: "absolute",
                      top: y,
                      left: 0,
                      width: "100%",
                      ...rowStyle(ri),
                    }}
                  >
                    {/* Linee verticali tick */}
                    {ticks.map((tick) => (
                      <div
                        key={tick.ms}
                        style={{
                          position: "absolute",
                          left: (tick.ms - minMs) * pxPerMs,
                          top: 0,
                          height: "100%",
                          borderLeft: tick.isDay
                            ? "1px solid #f1f5f9"
                            : "1px solid #f8fafc",
                          pointerEvents: "none",
                        }}
                      />
                    ))}

                    {/* ── Barre operazioni ── */}
                    {row.entries.map((entry) => {
                      const x = xFor(entry.scheduled_start);
                      const w = wFor(entry.scheduled_start, entry.scheduled_end);
                      const colors  = OP_TYPE_COLOR[entry.operation_type] ?? OP_TYPE_COLOR.GENERAL;
                      const overlay = STATUS_OVERLAY[entry.status] ?? STATUS_OVERLAY.SCHEDULED;
                      const isHov   = hoveredBar === entry.id;
                      const isCrit  = entry.is_critical_path;

                      return (
                        <Tooltip key={entry.id}>
                          <TooltipTrigger asChild>
                            <div
                              onMouseEnter={() => setHoveredBar(entry.id)}
                              onMouseLeave={() => setHoveredBar(null)}
                              onClick={() => onEntryClick?.(entry)}
                              style={{
                                position: "absolute",
                                left: x,
                                top: BAR_TOP,
                                width: w,
                                height: BAR_H,
                                background: colors.fill,
                                opacity: overlay.opacity,
                                border: isCrit
                                  ? "2px solid #f59e0b"
                                  : overlay.border,
                                borderRadius: 4,
                                cursor: onEntryClick ? "pointer" : "default",
                                boxShadow: isHov
                                  ? "0 2px 8px rgba(0,0,0,0.25)"
                                  : "0 1px 2px rgba(0,0,0,0.10)",
                                transform: isHov ? "scaleY(1.08)" : "scaleY(1)",
                                transition: "all 0.1s ease",
                                overflow: "hidden",
                                display: "flex",
                                alignItems: "center",
                                zIndex: isHov ? 10 : 1,
                              }}
                            >
                              {/* Progress overlay */}
                              {entry.progress_pct > 0 && (
                                <div
                                  style={{
                                    position: "absolute",
                                    left: 0,
                                    top: 0,
                                    width: `${Math.min(100, entry.progress_pct)}%`,
                                    height: "100%",
                                    background: "rgba(255,255,255,0.35)",
                                    borderRight: "1px solid rgba(255,255,255,0.6)",
                                  }}
                                />
                              )}

                              {/* Label in barra */}
                              {w > 50 && (
                                <span
                                  style={{
                                    position: "relative",
                                    padding: "0 5px",
                                    fontSize: 10,
                                    fontWeight: 600,
                                    color: colors.text,
                                    whiteSpace: "nowrap",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    maxWidth: w - 10,
                                    zIndex: 2,
                                    textShadow: "0 0 3px rgba(0,0,0,0.3)",
                                  }}
                                >
                                  {entry.reference_point_code
                                    ? `[${entry.reference_point_code}]`
                                    : entry.operation_description}
                                </span>
                              )}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent
                            side="top"
                            style={{ maxWidth: 280, fontSize: 11 }}
                          >
                            <div style={{ lineHeight: 1.6 }}>
                              <div style={{ fontWeight: 700, marginBottom: 2 }}>
                                {entry.operation_description}
                              </div>
                              <div>
                                👤 {entry.operator_name} ({entry.operator_skill})
                              </div>
                              <div>
                                🏭 {entry.workcenter_code} — {entry.workcenter_name}
                              </div>
                              <div>
                                📦 {entry.production_order_material}
                              </div>
                              <div>
                                🕐{" "}
                                {new Date(entry.scheduled_start).toLocaleString("it-IT", {
                                  day: "2-digit",
                                  month: "short",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}{" "}
                                →{" "}
                                {new Date(entry.scheduled_end).toLocaleString("it-IT", {
                                  day: "2-digit",
                                  month: "short",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </div>
                              <div>
                                📊 Avanzamento: {Math.round(entry.progress_pct)}%
                              </div>
                              <div
                                style={{
                                  marginTop: 3,
                                  padding: "1px 6px",
                                  borderRadius: 3,
                                  display: "inline-block",
                                  background: "#f1f5f9",
                                  fontSize: 10,
                                }}
                              >
                                {entry.status}
                                {isCrit && " ⭐ CRITICAL"}
                              </div>
                              {entry.missing_components.length > 0 && (
                                <div style={{ color: "#dc2626", marginTop: 3 }}>
                                  ⚠️ Mancanti:{" "}
                                  {entry.missing_components.join(", ")}
                                </div>
                              )}
                            </div>
                          </TooltipContent>
                        </Tooltip>
                      );
                    })}
                  </div>
                );
              })}

              {/* ── Linea ORA verticale ── */}
              {nowX !== null && (
                <div
                  style={{
                    position: "absolute",
                    left: nowX,
                    top: HEADER_H,
                    width: 2,
                    height: totalH - HEADER_H,
                    background: "#ef4444",
                    opacity: 0.7,
                    pointerEvents: "none",
                    zIndex: 15,
                  }}
                />
              )}

              {/* ── Marker RP ── */}
              {showRpM &&
                rpMarkers.map((m, i) => {
                  const x = (new Date(m.completion_time).getTime() - minMs) * pxPerMs;
                  if (x < 0 || x > totalPx) return null;
                  return (
                    <div
                      key={i}
                      style={{
                        position: "absolute",
                        left: x,
                        top: HEADER_H,
                        width: 1,
                        height: totalH - HEADER_H,
                        background: "#7c3aed",
                        opacity: 0.5,
                        borderLeft: "1px dashed #7c3aed",
                        pointerEvents: "none",
                        zIndex: 14,
                      }}
                    >
                      <div
                        style={{
                          position: "absolute",
                          top: 4,
                          left: 3,
                          background: "#7c3aed",
                          color: "#fff",
                          fontSize: 9,
                          fontWeight: 700,
                          padding: "1px 4px",
                          borderRadius: 3,
                          whiteSpace: "nowrap",
                        }}
                      >
                        {m.rp_code}
                      </div>
                    </div>
                  );
                })}

              {/* ── Frecce dipendenza (SVG overlay) ── */}
              {showDeps &&
                dependencies.length > 0 &&
                (() => {
                  const entryPos = new Map<
                    string,
                    { row: number; xEnd: number; xStart: number }
                  >();
                  rows.forEach((row, ri) => {
                    row.entries.forEach((e) => {
                      entryPos.set(e.id, {
                        row: ri,
                        xEnd:   xFor(e.scheduled_end),
                        xStart: xFor(e.scheduled_start),
                      });
                    });
                  });

                  return (
                    <svg
                      style={{
                        position: "absolute",
                        top: HEADER_H,
                        left: 0,
                        width: "100%",
                        height: totalH - HEADER_H,
                        pointerEvents: "none",
                        overflow: "visible",
                        zIndex: 5,
                      }}
                    >
                      <defs>
                        <marker
                          id="adv-arrow"
                          markerWidth="6"
                          markerHeight="6"
                          refX="5"
                          refY="3"
                          orient="auto"
                        >
                          <path d="M0 0 L6 3 L0 6 Z" fill="#94a3b8" />
                        </marker>
                        <marker
                          id="adv-arrow-rp"
                          markerWidth="6"
                          markerHeight="6"
                          refX="5"
                          refY="3"
                          orient="auto"
                        >
                          <path d="M0 0 L6 3 L0 6 Z" fill="#7c3aed" />
                        </marker>
                      </defs>
                      {dependencies.map((dep, i) => {
                        const from = entryPos.get(dep.from_entry_id);
                        const to   = entryPos.get(dep.to_entry_id);
                        if (!from || !to) return null;
                        const x1   = from.xEnd;
                        const y1   = from.row * ROW_H + ROW_H / 2;
                        const x2   = to.xStart;
                        const y2   = to.row * ROW_H + ROW_H / 2;
                        const midX = (x1 + x2) / 2;
                        const isRp = dep.source === "RP_DAG";
                        return (
                          <path
                            key={i}
                            d={`M${x1} ${y1} C${midX} ${y1} ${midX} ${y2} ${x2} ${y2}`}
                            fill="none"
                            stroke={isRp ? "#7c3aed" : "#94a3b8"}
                            strokeWidth={isRp ? 1.5 : 1}
                            strokeDasharray={isRp ? "none" : "3 2"}
                            opacity={0.45}
                            markerEnd={`url(#${isRp ? "adv-arrow-rp" : "adv-arrow"})`}
                          />
                        );
                      })}
                    </svg>
                  );
                })()}
            </div>
          </div>
        </div>

        {/* ── Legenda ── */}
        <LegendBar entries={entries} />
      </div>
    </TooltipProvider>
  );
}