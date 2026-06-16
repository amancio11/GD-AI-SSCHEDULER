// frontend/src/components/gantt/AdvancedGantt.tsx
//
// Gantt avanzato — riscritto con div+position:absolute invece di SVG
// Problemi risolti rispetto alla versione precedente:
//   - Barre sempre visibili (non sub-pixel): usa larghezza minima 4px
//   - ResizeObserver non serve più: il contenitore scroll orizzontale si espande
//     automaticamente grazie a min-width sul wrapper interno
//   - Label operatori/ordini leggibili in colonna fissa 240px
//   - Linea "ORA" con badge visibile
//   - Marker RP come linee verticali tratteggiate con etichetta
//   - Tooltip al hover su ogni barra
//   - Click su barra → callback onEntryClick

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { toPng } from "html-to-image";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Download, Users, GitBranch, Building2, HelpCircle, X } from "lucide-react";

// ============================================================================
// TYPES (riesportati per compatibilità con GanttView.tsx)
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
  externalZoom?: ZoomLevel;   // zoom controllato dal genitore (GanttView toolbar)
  onEntryClick?: (entry: GanttEntry) => void;
}

// ============================================================================
// CONSTANTS
// ============================================================================

const LABEL_COL = 240;   // px colonna sinistra fissa
const ROW_H     = 36;    // px altezza riga
const HEADER_H  = 48;    // px header date
const MIN_BAR_W = 4;     // px larghezza minima barra (visibilità garantita)
const BAR_H     = 22;    // px altezza barra
const BAR_TOP   = (ROW_H - BAR_H) / 2; // centratura verticale

// Pixel per unità di tempo secondo lo zoom
const PX_PER_HOUR = 80;
const PX_PER_DAY  = PX_PER_HOUR * 24;   // 1920 — 1 giorno = 80px/ora * 24
const PX_PER_WEEK = PX_PER_DAY * 7;

// Zoom → px per ms
const PX_PER_MS: Record<ZoomLevel, number> = {
  HOUR: PX_PER_HOUR / 3_600_000,
  DAY:  PX_PER_DAY  / 86_400_000,
  WEEK: PX_PER_WEEK / 604_800_000,
};

// Zoom → intervallo tick (ms)
const TICK_MS: Record<ZoomLevel, number> = {
  HOUR:  3_600_000,       // 1 ora
  DAY:   86_400_000,      // 1 giorno
  WEEK:  604_800_000,     // 1 settimana
};

// ============================================================================
// COLORS
// ============================================================================

const OP_TYPE_COLOR: Record<string, { fill: string; text: string }> = {
  ELECTRICAL: { fill: "#3b82f6", text: "#fff" },
  MECHANICAL: { fill: "#f97316", text: "#fff" },
  GENERAL:    { fill: "#22c55e", text: "#fff" },
};

const STATUS_OVERLAY: Record<GanttStatus, { opacity: number; border: string; pattern?: boolean }> = {
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
        sublabel: `${f.operator_skill} • ${f.workcenter_code}`,
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

  // Costruisci albero
  type ONode = { id: string; material: string; desc: string; level: BomLevel; parent: string | null; entries: GanttEntry[] };
  const orders = new Map<string, ONode>();
  for (const [oid, ents] of byOrder) {
    const f = ents[0];
    orders.set(oid, { id: oid, material: f.production_order_material, desc: f.production_order_description, level: f.production_order_level, parent: f.parent_order_id, entries: ents });
  }

  const rows: GanttRow[] = [];
  function dfs(node: ONode): void {
    rows.push({
      key: node.id,
      label: node.material,
      sublabel: node.desc,
      indent: LEVEL_INDENT[node.level] ?? 0,
      labelColor: LEVEL_COLOR[node.level],
      entries: node.entries.sort((a, b) => a.scheduled_start.localeCompare(b.scheduled_start)),
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
// TIME AXIS
// ============================================================================

interface Tick {
  ms: number;
  label: string;
  isDay: boolean;
}

function buildAxis(minMs: number, maxMs: number, zoom: ZoomLevel): { pxPerMs: number; ticks: Tick[]; totalPx: number } {
  const pxPerMs = PX_PER_MS[zoom];
  const tickMs  = TICK_MS[zoom];
  const totalPx = Math.max((maxMs - minMs) * pxPerMs, 400);

  // Snappa il primo tick all'unità di zoom
  const firstTick = Math.ceil(minMs / tickMs) * tickMs;
  const ticks: Tick[] = [];

  for (let t = firstTick; t <= maxMs + tickMs; t += tickMs) {
    const d = new Date(t);
    let label = "";
    if (zoom === "HOUR") {
      label = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
    } else if (zoom === "DAY") {
      label = d.toLocaleDateString("it-IT", { weekday: "short", day: "2-digit", month: "short" });
    } else {
      label = `Sett. ${d.toLocaleDateString("it-IT", { day: "2-digit", month: "short" })}`;
    }
    ticks.push({ ms: t, label, isDay: zoom !== "HOUR" });
  }

  return { pxPerMs, ticks, totalPx };
}

// ============================================================================
// LEGEND BAR — barra in fondo con pannello espandibile "?"
// ============================================================================

function LegendBar({ entries }: { entries: GanttEntry[] }): JSX.Element {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: "relative", flexShrink: 0 }}>
      {/* Pannello espandibile — appare sopra la barra */}
      {open && (
        <div style={{
          position: "absolute",
          bottom: "100%",
          left: 0,
          right: 0,
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderBottom: "none",
          borderRadius: "10px 10px 0 0",
          padding: "16px 20px 12px",
          boxShadow: "0 -4px 16px rgba(0,0,0,0.08)",
          zIndex: 30,
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 16,
        }}>
          <button
            onClick={() => setOpen(false)}
            style={{
              position: "absolute", top: 10, right: 12,
              background: "none", border: "none", cursor: "pointer",
              color: "#94a3b8", padding: 2,
            }}
          >
            <X size={14} />
          </button>

          {/* Colonna 1: Tipi operazione + stato barre */}
          <div>
            <div style={{ fontWeight: 700, fontSize: 11, color: "#334155", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Tipo operazione (colore barra)
            </div>
            {Object.entries(OP_TYPE_COLOR).map(([type, c]) => (
              <div key={type} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
                <div style={{ width: 28, height: 14, borderRadius: 3, background: c.fill, flexShrink: 0 }} />
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "#334155" }}>{type}</div>
                  <div style={{ fontSize: 10, color: "#94a3b8" }}>
                    {type === "ELECTRICAL" ? "Lavori elettrici" : type === "MECHANICAL" ? "Lavori meccanici" : "Lavori generali"}
                  </div>
                </div>
              </div>
            ))}
            <div style={{ marginTop: 8, borderTop: "1px solid #f1f5f9", paddingTop: 8 }}>
              <div style={{ fontWeight: 700, fontSize: 11, color: "#334155", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Bordi barra (stato)
              </div>
              {[
                { label: "IN_PROGRESS", border: "2px solid #1d4ed8", desc: "Operazione in esecuzione" },
                { label: "INTERRUPTED", border: "2px dashed #ea580c", desc: "Interrotta, da riprendere" },
                { label: "BLOCKED/DELAYED", border: "2px solid #dc2626", desc: "Bloccata o in ritardo" },
                { label: "COMPLETED", border: "none", opacity: 0.45, desc: "Completata (barra opaca)" },
              ].map((s) => (
                <div key={s.label} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
                  <div style={{
                    width: 28, height: 14, borderRadius: 3,
                    background: "#3b82f6",
                    border: s.border ?? "none",
                    opacity: s.opacity ?? 1,
                    flexShrink: 0,
                  }} />
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "#334155" }}>{s.label}</div>
                    <div style={{ fontSize: 10, color: "#94a3b8" }}>{s.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Colonna 2: Linee e marcatori */}
          <div>
            <div style={{ fontWeight: 700, fontSize: 11, color: "#334155", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Linee e marcatori
            </div>

            {/* Linea ORA */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 12 }}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2, flexShrink: 0 }}>
                <div style={{ background: "#ef4444", color: "#fff", fontSize: 8, fontWeight: 700, padding: "1px 4px", borderRadius: 2 }}>ORA</div>
                <div style={{ width: 2, height: 28, background: "#ef4444", opacity: 0.7 }} />
              </div>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "#334155" }}>Linea "ORA"</div>
                <div style={{ fontSize: 10, color: "#64748b", lineHeight: 1.4 }}>
                  Linea rossa verticale continua = momento attuale. Aiuta a capire cosa è passato e cosa è futuro. Il badge "ORA" appare anche nell'header date.
                </div>
              </div>
            </div>

            {/* Marker RP */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 12 }}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2, flexShrink: 0 }}>
                <div style={{ background: "#7c3aed", color: "#fff", fontSize: 8, fontWeight: 700, padding: "1px 4px", borderRadius: 2 }}>RP-01</div>
                <div style={{ width: 1, height: 28, borderLeft: "1px dashed #7c3aed" }} />
              </div>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "#334155" }}>Marker Reference Point</div>
                <div style={{ fontSize: 10, color: "#64748b", lineHeight: 1.4 }}>
                  Linea viola tratteggiata verticale = momento in cui un sottoassemblaggio deve essere completato. Segna il confine temporale imposto da un vincolo RP: le operazioni a destra di questo marker dipendono da quel completamento.
                </div>
              </div>
            </div>

            {/* Critical path */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <div style={{
                width: 28, height: 14, borderRadius: 3,
                background: "#3b82f6",
                border: "2px solid #f59e0b",
                flexShrink: 0,
                marginTop: 2,
              }} />
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "#334155" }}>⭐ Critical Path</div>
                <div style={{ fontSize: 10, color: "#64748b", lineHeight: 1.4 }}>
                  Bordo dorato = operazione sul percorso critico. Un ritardo su queste operazioni ritarda direttamente la data di fine macchina. Attiva "Solo critical path" per isolarle.
                </div>
              </div>
            </div>
          </div>

          {/* Colonna 3: Frecce dipendenza */}
          <div>
            <div style={{ fontWeight: 700, fontSize: 11, color: "#334155", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Frecce dipendenza
            </div>

            {/* RP_DAG */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 12 }}>
              <svg width={40} height={20} style={{ flexShrink: 0, marginTop: 2 }}>
                <defs>
                  <marker id="leg-rp" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                    <path d="M0 0 L5 2.5 L0 5 Z" fill="#7c3aed" />
                  </marker>
                </defs>
                <path d="M2 10 C15 10 25 10 34 10" fill="none" stroke="#7c3aed" strokeWidth="2" markerEnd="url(#leg-rp)" />
              </svg>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "#7c3aed" }}>Dipendenza RP (viola, continua)</div>
                <div style={{ fontSize: 10, color: "#64748b", lineHeight: 1.4 }}>
                  Vincolo derivato dal DAG dei Reference Point. L'operazione di destinazione non può iniziare finché l'operazione sorgente (e tutto il suo sottoalbero BOM) non è completata. È il vincolo più forte e strutturale.
                </div>
              </div>
            </div>

            {/* INTRA_ROUTING */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <svg width={40} height={20} style={{ flexShrink: 0, marginTop: 2 }}>
                <defs>
                  <marker id="leg-rt" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                    <path d="M0 0 L5 2.5 L0 5 Z" fill="#94a3b8" />
                  </marker>
                </defs>
                <path d="M2 10 C15 10 25 10 34 10" fill="none" stroke="#94a3b8" strokeWidth="1.5" strokeDasharray="3 2" markerEnd="url(#leg-rt)" />
              </svg>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "#64748b" }}>Dipendenza intra-routing (grigia, tratteggiata)</div>
                <div style={{ fontSize: 10, color: "#64748b", lineHeight: 1.4 }}>
                  Sequenza interna a un routing (operazione A → B dello stesso ordine). Attualmente non generata dal solver in modalità SIMULTANEOUS, ma visibile se presente nello scenario.
                </div>
              </div>
            </div>

            <div style={{
              marginTop: 12, padding: "8px 10px",
              background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 6,
              fontSize: 10, color: "#64748b", lineHeight: 1.5,
            }}>
              💡 <strong>Modalità di raggruppamento:</strong> le frecce cambiano percorso a seconda della modalità (Operatore/Ordine/Workcenter) perché le righe si riorganizzano. Il vincolo rimane lo stesso — cambia solo la rappresentazione visiva.
            </div>
          </div>
        </div>
      )}

      {/* Barra inferiore sempre visibile */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "5px 12px",
        borderTop: "1px solid #e2e8f0",
        background: "#f8fafc",
        flexWrap: "wrap",
      }}>
        {/* Tipo operazione */}
        {Object.entries(OP_TYPE_COLOR).map(([type, c]) => (
          <div key={type} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 12, height: 12, borderRadius: 2, background: c.fill }} />
            <span style={{ fontSize: 10, color: "#64748b" }}>{type}</span>
          </div>
        ))}

        {/* Separatore */}
        <div style={{ width: 1, height: 14, background: "#e2e8f0" }} />

        {/* Critical path */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <div style={{ width: 12, height: 12, borderRadius: 2, background: "#3b82f6", border: "2px solid #f59e0b" }} />
          <span style={{ fontSize: 10, color: "#64748b" }}>⭐ Critical path</span>
        </div>

        {/* Linea ORA */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <div style={{ width: 14, height: 2, background: "#ef4444" }} />
          <span style={{ fontSize: 10, color: "#64748b" }}>Ora</span>
        </div>

        {/* Marker RP */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <div style={{ width: 1, height: 12, borderLeft: "2px dashed #7c3aed" }} />
          <span style={{ fontSize: 10, color: "#7c3aed", fontWeight: 600 }}>Marker RP</span>
        </div>

        {/* Frecce */}
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <svg width={20} height={10}>
            <defs>
              <marker id="lb-rp" markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto">
                <path d="M0 0 L4 2 L0 4 Z" fill="#7c3aed" />
              </marker>
            </defs>
            <line x1="1" y1="5" x2="16" y2="5" stroke="#7c3aed" strokeWidth="1.5" markerEnd="url(#lb-rp)" />
          </svg>
          <span style={{ fontSize: 10, color: "#64748b" }}>Dipendenza RP</span>
        </div>

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 10, color: "#cbd5e1" }}>
            {entries.length} operazioni
          </span>
          {/* Bottone ? */}
          <button
            onClick={() => setOpen((v) => !v)}
            title="Legenda dettagliata"
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "3px 8px",
              borderRadius: 6,
              border: `1px solid ${open ? "#6366f1" : "#e2e8f0"}`,
              background: open ? "#eef2ff" : "#fff",
              color: open ? "#6366f1" : "#64748b",
              cursor: "pointer",
              fontSize: 11,
              fontWeight: 600,
              transition: "all 0.1s",
            }}
          >
            <HelpCircle size={12} />
            {open ? "Chiudi legenda" : "Come leggere il Gantt"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// MAIN EXPORT
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
  const [mode, setMode]   = useState<GroupingMode>(initialMode);
  const [zoom, setZoom]   = useState<ZoomLevel>(externalZoom ?? "DAY");

  // Sincronizza zoom esterno → stato interno
  useEffect(() => {
    if (externalZoom) setZoom(externalZoom);
  }, [externalZoom]);
  const [showDeps, setShowDeps]   = useState(true);
  const [showRpM,  setShowRpM]    = useState(true);
  const [critOnly, setCritOnly]   = useState(false);
  const [hoveredBar, setHoveredBar] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const exportRef = useRef<HTMLDivElement>(null);

  // Filtra
  const filtered = useMemo(() => {
    const safeEntries = Array.isArray(entries) ? entries : [];
    return critOnly ? safeEntries.filter((e) => e.is_critical_path) : safeEntries;
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
    (iso: string): number => (new Date(iso).getTime() - minMs) * pxPerMs,
    [minMs, pxPerMs]
  );
  const wFor = useCallback(
    (start: string, end: string): number =>
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

  const totalH = HEADER_H + rows.length * ROW_H;

  // ── RENDER ────────────────────────────────────────────────────────────────
  return (
    <TooltipProvider delay={120}>
      <div style={{ display: "flex", flexDirection: "column", height: height ?? "100%", border: "1px solid #e2e8f0", borderRadius: 10, overflow: "hidden", background: "#fff", fontFamily: "system-ui, sans-serif" }}>

        {/* ── Toolbar ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid #e2e8f0", flexWrap: "wrap", background: "#f8fafc", flexShrink: 0 }}>
          {/* Titolo */}
          <span style={{ fontWeight: 700, fontSize: 13, color: "#1e293b", marginRight: 8 }}>
            Schedulazione — Gantt
          </span>

          {/* Grouping */}
          <div style={{ display: "flex", borderRadius: 6, border: "1px solid #e2e8f0", overflow: "hidden" }}>
            {([
              ["BY_OPERATOR",   "Operatore",   <Users key="u" size={12} />],
              ["BY_WORKCENTER", "Workcenter",  <Building2 key="b" size={12} />],
              ["BY_ORDER",      "Ordine",      <GitBranch key="g" size={12} />],
            ] as const).map(([m, label, icon]) => (
              <button
                key={m}
                onClick={() => setMode(m as GroupingMode)}
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  padding: "4px 10px", fontSize: 12, border: "none", cursor: "pointer",
                  background: mode === m ? "#3b82f6" : "transparent",
                  color: mode === m ? "#fff" : "#64748b",
                  fontWeight: mode === m ? 600 : 400,
                  transition: "all 0.1s",
                }}
              >
                {icon} {label}
              </button>
            ))}
          </div>

          {/* Zoom */}
          <div style={{ display: "flex", borderRadius: 6, border: "1px solid #e2e8f0", overflow: "hidden" }}>
            {(["HOUR", "DAY", "WEEK"] as ZoomLevel[]).map((z) => (
              <button
                key={z}
                onClick={() => setZoom(z)}
                style={{
                  padding: "4px 10px", fontSize: 12, border: "none", cursor: "pointer",
                  background: zoom === z ? "#6366f1" : "transparent",
                  color: zoom === z ? "#fff" : "#64748b",
                  fontWeight: zoom === z ? 600 : 400,
                }}
              >
                {z === "HOUR" ? "Ora" : z === "DAY" ? "Giorno" : "Settimana"}
              </button>
            ))}
          </div>

          {/* Toggle dipendenze */}
          <button
            onClick={() => setShowDeps((v) => !v)}
            style={{
              padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer",
              background: showDeps ? "#f0fdf4" : "transparent", color: showDeps ? "#15803d" : "#94a3b8",
            }}
          >
            Dipendenze
          </button>

          {/* Toggle RP */}
          <button
            onClick={() => setShowRpM((v) => !v)}
            style={{
              padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer",
              background: showRpM ? "#f5f3ff" : "transparent", color: showRpM ? "#7c3aed" : "#94a3b8",
            }}
          >
            Marker RP
          </button>

          {/* Critical path toggle */}
          <button
            onClick={() => setCritOnly((v) => !v)}
            style={{
              padding: "4px 10px", fontSize: 12, border: "1px solid #e2e8f0", borderRadius: 6, cursor: "pointer",
              background: critOnly ? "#fefce8" : "transparent", color: critOnly ? "#a16207" : "#94a3b8",
            }}
          >
            Solo critical path
          </button>

          <div style={{ flex: 1 }} />

          {/* Statistiche */}
          <span style={{ fontSize: 11, color: "#94a3b8" }}>
            {filtered.length} op. · {rows.length} righe
          </span>

          {/* Export */}
          <Button size="sm" variant="outline" onClick={exportPng} style={{ height: 28, fontSize: 11 }}>
            <Download size={12} style={{ marginRight: 4 }} /> PNG
          </Button>
        </div>

        {/* ── Corpo principale ── */}
        <div ref={exportRef} style={{ display: "flex", flex: 1, overflow: "hidden" }}>

          {/* Colonna etichette — fissa a sinistra */}
          <div style={{
            width: LABEL_COL,
            flexShrink: 0,
            borderRight: "1px solid #e2e8f0",
            overflowY: "hidden",
            background: "#fff",
          }}>
            {/* Spazio header */}
            <div style={{ height: HEADER_H, borderBottom: "1px solid #e2e8f0", background: "#f8fafc" }} />

            {/* Righe etichette */}
            {rows.map((row, ri) => (
              <div
                key={row.key}
                style={{
                  height: ROW_H,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  padding: `0 8px 0 ${8 + row.indent}px`,
                  borderBottom: "1px solid #f1f5f9",
                  background: ri % 2 === 0 ? "#fff" : "#fafafa",
                }}
              >
                <span style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: row.labelColor ?? "#1e293b",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  lineHeight: 1.2,
                }}>
                  {row.label}
                </span>
                {row.sublabel && (
                  <span style={{
                    fontSize: 10,
                    color: "#94a3b8",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}>
                    {row.sublabel}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Area chart — scroll orizzontale */}
          <div
            ref={scrollRef}
            style={{ flex: 1, overflowX: "auto", overflowY: "auto", position: "relative" }}
          >
            <div style={{ minWidth: totalPx, width: totalPx, position: "relative", height: totalH }}>

              {/* ── Header date ── */}
              <div style={{
                position: "sticky",
                top: 0,
                zIndex: 20,
                height: HEADER_H,
                background: "#f8fafc",
                borderBottom: "1px solid #e2e8f0",
              }}>
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
                      <span style={{ fontSize: 10, color: "#64748b", fontWeight: tick.isDay ? 600 : 400 }}>
                        {tick.label}
                      </span>
                    </div>
                  );
                })}

                {/* Linea ORA nell'header */}
                {nowX !== null && (
                  <div style={{
                    position: "absolute",
                    left: nowX,
                    top: 0,
                    height: "100%",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                  }}>
                    <div style={{
                      background: "#ef4444",
                      color: "#fff",
                      fontSize: 9,
                      fontWeight: 700,
                      padding: "1px 5px",
                      borderRadius: 3,
                      marginTop: 4,
                      whiteSpace: "nowrap",
                    }}>
                      ORA
                    </div>
                  </div>
                )}
              </div>

              {/* ── Righe chart ── */}
              {rows.map((row, ri) => {
                const y = HEADER_H + ri * ROW_H;
                return (
                  <div
                    key={row.key}
                    style={{
                      position: "absolute",
                      top: y,
                      left: 0,
                      width: "100%",
                      height: ROW_H,
                      background: ri % 2 === 0 ? "rgba(0,0,0,0)" : "rgba(0,0,0,0.015)",
                      borderBottom: "1px solid #f1f5f9",
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
                          borderLeft: tick.isDay ? "1px solid #f1f5f9" : "1px solid #f8fafc",
                          pointerEvents: "none",
                        }}
                      />
                    ))}

                    {/* ── Barre operazioni ── */}
                    {row.entries.map((entry) => {
                      const x = xFor(entry.scheduled_start);
                      const w = wFor(entry.scheduled_start, entry.scheduled_end);
                      const colors = OP_TYPE_COLOR[entry.operation_type] ?? OP_TYPE_COLOR.GENERAL;
                      const overlay = STATUS_OVERLAY[entry.status] ?? STATUS_OVERLAY.SCHEDULED;
                      const isCrit = entry.is_critical_path;
                      const isHov = hoveredBar === entry.id;

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
                                border: isCrit ? "2px solid #f59e0b" : (overlay.border === "none" ? undefined : overlay.border),
                                borderRadius: 4,
                                cursor: onEntryClick ? "pointer" : "default",
                                boxShadow: isHov ? "0 2px 8px rgba(0,0,0,0.25)" : "0 1px 2px rgba(0,0,0,0.1)",
                                transform: isHov ? "scaleY(1.1)" : "scaleY(1)",
                                transition: "all 0.1s ease",
                                overflow: "hidden",
                                display: "flex",
                                alignItems: "center",
                                zIndex: isHov ? 10 : 1,
                              }}
                            >
                              {/* Progress overlay */}
                              {entry.progress_pct > 0 && (
                                <div style={{
                                  position: "absolute",
                                  left: 0,
                                  top: 0,
                                  width: `${Math.min(100, entry.progress_pct)}%`,
                                  height: "100%",
                                  background: "rgba(255,255,255,0.35)",
                                  borderRight: "1px solid rgba(255,255,255,0.6)",
                                }} />
                              )}
                              {/* Label nella barra (solo se abbastanza larga) */}
                              {w > 50 && (
                                <span style={{
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
                                }}>
                                  {entry.reference_point_code
                                    ? `${entry.reference_point_code} · `
                                    : ""}
                                  {entry.operation_description}
                                </span>
                              )}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent side="top" align="start" style={{ maxWidth: 280 }}>
                            <div style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 3 }}>
                              <div style={{ fontWeight: 700, color: "#1e293b" }}>
                                {entry.operation_description || "(senza descrizione)"}
                              </div>
                              <div style={{ color: "#64748b" }}>
                                Operatore: <strong>{entry.operator_name}</strong>
                              </div>
                              <div style={{ color: "#64748b" }}>
                                Workcenter: <strong>{entry.workcenter_code}</strong>
                              </div>
                              <div style={{ color: "#64748b" }}>
                                Ordine: <strong>{entry.production_order_material}</strong> — {entry.production_order_description}
                              </div>
                              <div style={{ color: "#64748b" }}>
                                Inizio: <strong>{new Date(entry.scheduled_start).toLocaleString("it-IT")}</strong>
                              </div>
                              <div style={{ color: "#64748b" }}>
                                Fine: <strong>{new Date(entry.scheduled_end).toLocaleString("it-IT")}</strong>
                              </div>
                              <div style={{ display: "flex", gap: 4, marginTop: 2, flexWrap: "wrap" }}>
                                <span style={{
                                  background: colors.fill,
                                  color: colors.text,
                                  padding: "1px 6px",
                                  borderRadius: 4,
                                  fontSize: 10,
                                  fontWeight: 600,
                                }}>
                                  {entry.operation_type}
                                </span>
                                <span style={{
                                  background: "#f1f5f9",
                                  color: "#475569",
                                  padding: "1px 6px",
                                  borderRadius: 4,
                                  fontSize: 10,
                                }}>
                                  {entry.status}
                                </span>
                                {isCrit && (
                                  <span style={{
                                    background: "#fef3c7",
                                    color: "#92400e",
                                    padding: "1px 6px",
                                    borderRadius: 4,
                                    fontSize: 10,
                                    fontWeight: 700,
                                  }}>
                                    ⭐ Critical Path
                                  </span>
                                )}
                                {entry.progress_pct > 0 && (
                                  <span style={{
                                    background: "#f0fdf4",
                                    color: "#166534",
                                    padding: "1px 6px",
                                    borderRadius: 4,
                                    fontSize: 10,
                                  }}>
                                    {entry.progress_pct.toFixed(0)}%
                                  </span>
                                )}
                              </div>
                            </div>
                          </TooltipContent>
                        </Tooltip>
                      );
                    })}
                  </div>
                );
              })}

              {/* ── Linea ORA (verticale) ── */}
              {nowX !== null && (
                <div style={{
                  position: "absolute",
                  left: nowX,
                  top: HEADER_H,
                  width: 2,
                  height: totalH - HEADER_H,
                  background: "#ef4444",
                  opacity: 0.7,
                  pointerEvents: "none",
                  zIndex: 15,
                }} />
              )}

              {/* ── Marker RP ── */}
              {showRpM && rpMarkers.map((m, i) => {
                const x = (new Date(m.completion_time).getTime() - minMs) * pxPerMs;
                if (x < 0 || x > totalPx) return null;
                return (
                  <div key={i} style={{
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
                  }}>
                    <div style={{
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
                    }}>
                      {m.rp_code}
                    </div>
                  </div>
                );
              })}

              {/* ── Frecce dipendenza (SVG overlay) ── */}
              {showDeps && dependencies.length > 0 && (() => {
                // Mappa entry_id → (riga, posizione X fine/inizio)
                const entryPos = new Map<string, { row: number; xEnd: number; xStart: number }>();
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
                    style={{ position: "absolute", top: HEADER_H, left: 0, width: "100%", height: totalH - HEADER_H, pointerEvents: "none", overflow: "visible", zIndex: 5 }}
                  >
                    <defs>
                      <marker id="adv-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
                        <path d="M0 0 L6 3 L0 6 Z" fill="#94a3b8" />
                      </marker>
                      <marker id="adv-arrow-rp" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
                        <path d="M0 0 L6 3 L0 6 Z" fill="#7c3aed" />
                      </marker>
                    </defs>
                    {dependencies.map((dep, i) => {
                      const from = entryPos.get(dep.from_entry_id);
                      const to   = entryPos.get(dep.to_entry_id);
                      if (!from || !to) return null;
                      const x1 = from.xEnd;
                      const y1 = from.row * ROW_H + ROW_H / 2;
                      const x2 = to.xStart;
                      const y2 = to.row * ROW_H + ROW_H / 2;
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

            </div>{/* fine wrapper interno */}
          </div>{/* fine scroll */}
        </div>{/* fine corpo */}

        {/* ── Legenda + bottone ? ── */}
        <LegendBar entries={entries} />

      </div>
    </TooltipProvider>
  );
}