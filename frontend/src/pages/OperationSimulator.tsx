// frontend/src/pages/OperationSimulator.tsx
//
// Simulatore stati operazioni — permette di:
//   • Cercare e selezionare qualunque operazione schedulate
//   • Cambiare il suo stato (PENDING → IN_PROGRESS → COMPLETED/INTERRUPTED/BLOCKED)
//   • Impostare progress_pct e actual_start/end
//   • Vedere in tempo reale cosa cambia nel piano (rischedulazione incrementale)
//   • Capire se un'operazione completata in ritardo sposta i successori

import { useState, useMemo, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import apiClient from "../api/client";
import { useScheduleStore } from "../store/scheduleStore";
import { useMachineStore } from "../store/machineStore";
import type { UUID } from "../api/types";
import {
  Play,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  Zap,
  RefreshCw,
  ChevronRight,
  Info,
  ArrowRight,
} from "lucide-react";

// ============================================================================
// TYPES
// ============================================================================

type OperationStatus =
  | "PENDING"
  | "IN_PROGRESS"
  | "COMPLETED"
  | "BLOCKED"
  | "INTERRUPTED";

type ScheduleEntryStatus =
  | "SCHEDULED"
  | "IN_PROGRESS"
  | "COMPLETED"
  | "INTERRUPTED"
  | "DELAYED"
  | "STALE";

interface ScheduledOp {
  entry_id: UUID;
  operation_id: UUID;
  operation_description: string;
  operation_type: "ELECTRICAL" | "MECHANICAL" | "GENERAL";
  operator_name: string;
  workcenter_code: string;
  production_order_material: string;
  production_order_level: string;
  scheduled_start: string;
  scheduled_end: string;
  actual_start: string | null;
  actual_end: string | null;
  entry_status: ScheduleEntryStatus;
  op_status: OperationStatus;
  op_progress_pct: number;
  is_critical_path: boolean;
  reference_point_code: string | null;
}

interface SimulationEffect {
  rescheduled_count: number;
  delayed_count: number;
  unblocked_count: number;
  critical_path_changed: boolean;
  new_estimated_end: string | null;
  old_estimated_end: string | null;
  affected_entries: Array<{
    entry_id: UUID;
    operation_description: string;
    old_start: string;
    new_start: string;
    delta_minutes: number;
  }>;
}

// ============================================================================
// CONSTANTS
// ============================================================================

const STATUS_META: Record<
  OperationStatus,
  { label: string; color: string; bg: string; icon: JSX.Element; description: string }
> = {
  PENDING: {
    label: "In attesa",
    color: "#64748b",
    bg: "#f1f5f9",
    icon: <Clock size={14} />,
    description: "L'operazione non è ancora iniziata. Rispetta lo schedule pianificato.",
  },
  IN_PROGRESS: {
    label: "In corso",
    color: "#1d4ed8",
    bg: "#eff6ff",
    icon: <Play size={14} />,
    description: "L'operazione è avviata. Puoi aggiornare il progresso (%).",
  },
  COMPLETED: {
    label: "Completata",
    color: "#15803d",
    bg: "#f0fdf4",
    icon: <CheckCircle size={14} />,
    description:
      "Operazione terminata. Se actual_end > scheduled_end → ritardo → i successori vengono rischedulati.",
  },
  BLOCKED: {
    label: "Bloccata",
    color: "#b45309",
    bg: "#fffbeb",
    icon: <AlertTriangle size={14} />,
    description:
      "Operazione bloccata (es. componente mancante). I successori rimangono in attesa.",
  },
  INTERRUPTED: {
    label: "Interrotta",
    color: "#dc2626",
    bg: "#fef2f2",
    icon: <XCircle size={14} />,
    description:
      "Operazione sospesa. Il solver riprenderà da progress_pct al prossimo reschedule.",
  },
};

const OP_TYPE_COLOR = {
  ELECTRICAL: "#3b82f6",
  MECHANICAL: "#f97316",
  GENERAL: "#22c55e",
};

// ============================================================================
// HELPERS
// ============================================================================

function fmtDt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("it-IT", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDelta(minutes: number): string {
  if (minutes === 0) return "±0";
  const sign = minutes > 0 ? "+" : "";
  const h = Math.floor(Math.abs(minutes) / 60);
  const m = Math.abs(minutes) % 60;
  return `${sign}${h > 0 ? `${h}h ` : ""}${m}m`;
}

function diffMinutes(a: string | null, b: string | null): number {
  if (!a || !b) return 0;
  return Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60000);
}

// ============================================================================
// SUB-COMPONENTS
// ============================================================================

function StatusBadge({ status }: { status: OperationStatus | ScheduleEntryStatus }) {
  const meta = STATUS_META[status as OperationStatus];
  if (!meta) {
    return (
      <span
        style={{
          fontSize: 10,
          padding: "2px 7px",
          borderRadius: 10,
          background: "#f1f5f9",
          color: "#64748b",
          fontWeight: 600,
        }}
      >
        {status}
      </span>
    );
  }
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 10,
        padding: "2px 7px",
        borderRadius: 10,
        background: meta.bg,
        color: meta.color,
        fontWeight: 600,
      }}
    >
      {meta.icon}
      {meta.label}
    </span>
  );
}

function OperationCard({
  op,
  isSelected,
  onClick,
}: {
  op: ScheduledOp;
  isSelected: boolean;
  onClick: () => void;
}) {
  const delay = diffMinutes(op.scheduled_end, op.actual_end);
  const isDelayed = delay > 0;

  return (
    <div
      onClick={onClick}
      style={{
        padding: "10px 12px",
        borderRadius: 8,
        border: isSelected
          ? "2px solid #1d4ed8"
          : "1px solid #e2e8f0",
        background: isSelected ? "#eff6ff" : "#fff",
        cursor: "pointer",
        transition: "all 0.15s",
        marginBottom: 6,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 8,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 12,
              fontWeight: 700,
              color: "#1e293b",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {op.operation_description}
          </div>
          <div
            style={{
              fontSize: 10,
              color: "#64748b",
              marginTop: 2,
              display: "flex",
              alignItems: "center",
              gap: 6,
              flexWrap: "wrap",
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                borderRadius: "50%",
                background:
                  OP_TYPE_COLOR[op.operation_type] ?? "#94a3b8",
                flexShrink: 0,
              }}
            />
            <span>{op.production_order_material}</span>
            <span>·</span>
            <span>{op.operator_name}</span>
            <span>·</span>
            <span>{op.workcenter_code}</span>
            {op.is_critical_path && (
              <span style={{ color: "#f59e0b", fontWeight: 700 }}>⭐ CP</span>
            )}
          </div>
          <div
            style={{
              fontSize: 10,
              color: "#94a3b8",
              marginTop: 3,
            }}
          >
            📅 {fmtDt(op.scheduled_start)} → {fmtDt(op.scheduled_end)}
          </div>
          {isDelayed && (
            <div
              style={{
                fontSize: 10,
                color: "#dc2626",
                fontWeight: 600,
                marginTop: 2,
              }}
            >
              ⚠️ Ritardo: {fmtDelta(delay)}
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-end" }}>
          <StatusBadge status={op.op_status} />
          {op.op_progress_pct > 0 && (
            <span
              style={{
                fontSize: 10,
                color: "#1d4ed8",
                fontWeight: 600,
              }}
            >
              {Math.round(op.op_progress_pct)}%
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// MAIN PAGE
// ============================================================================

export default function OperationSimulator() {
  const { activeScenarioId } = useScheduleStore();
  const { selectedMachineOrderId } = useMachineStore();
  const qc = useQueryClient();

  const [search, setSearch] = useState("");
  const [filterType, setFilterType] = useState<string>("ALL");
  const [filterLevel, setFilterLevel] = useState<string>("ALL");
  const [selectedOp, setSelectedOp] = useState<ScheduledOp | null>(null);

  // Form stato
  const [newStatus, setNewStatus] = useState<OperationStatus>("PENDING");
  const [progressPct, setProgressPct] = useState(0);
  const [actualStart, setActualStart] = useState("");
  const [actualEnd, setActualEnd] = useState("");
  const [interruptionReason, setInterruptionReason] = useState("");
  const [triggerReschedule, setTriggerReschedule] = useState(true);

  // Risultato simulazione
  const [simEffect, setSimEffect] = useState<SimulationEffect | null>(null);
  const [applySuccess, setApplySuccess] = useState(false);

  // ── Carica operazioni schedulate ─────────────────────────────────────────
  const { data: ops = [], isLoading } = useQuery<ScheduledOp[]>({
    queryKey: ["sim-ops", activeScenarioId],
    queryFn: async () => {
      if (!activeScenarioId) return [];
      const { data } = await apiClient.get<ScheduledOp[]>(
        `/api/gantt/${activeScenarioId}/operations-flat`
      );
      return data;
    },
    enabled: !!activeScenarioId,
    staleTime: 30_000,
  });

  // ── Filtra ───────────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    return ops.filter((op) => {
      const matchSearch =
        !search ||
        op.operation_description.toLowerCase().includes(search.toLowerCase()) ||
        op.production_order_material.toLowerCase().includes(search.toLowerCase()) ||
        op.operator_name.toLowerCase().includes(search.toLowerCase());
      const matchType =
        filterType === "ALL" || op.operation_type === filterType;
      const matchLevel =
        filterLevel === "ALL" || op.production_order_level === filterLevel;
      return matchSearch && matchType && matchLevel;
    });
  }, [ops, search, filterType, filterLevel]);

  // ── Quando si seleziona un'operazione → precompila il form ───────────────
  useEffect(() => {
    if (!selectedOp) return;
    setNewStatus(selectedOp.op_status);
    setProgressPct(Math.round(selectedOp.op_progress_pct));
    setActualStart(
      selectedOp.actual_start
        ? selectedOp.actual_start.slice(0, 16)
        : ""
    );
    setActualEnd(
      selectedOp.actual_end ? selectedOp.actual_end.slice(0, 16) : ""
    );
    setInterruptionReason("");
    setSimEffect(null);
    setApplySuccess(false);
  }, [selectedOp]);

  // ── Calcola ritardo stimato per la preview ────────────────────────────────
  const estimatedDelay = useMemo(() => {
    if (!selectedOp || !actualEnd) return null;
    const delta = diffMinutes(selectedOp.scheduled_end, actualEnd);
    return delta;
  }, [selectedOp, actualEnd]);

  // ── Mutation: aggiorna stato operazione ──────────────────────────────────
  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!selectedOp || !activeScenarioId) throw new Error("Nessuna operazione selezionata");

      // 1) Aggiorna stato operazione
      await apiClient.patch(`/api/operations/${selectedOp.operation_id}/status`, {
        status: newStatus,
        progress_pct: progressPct,
        actual_start: actualStart || null,
        actual_end: actualEnd || null,
        interruption_reason:
          newStatus === "INTERRUPTED" ? interruptionReason : null,
      });

      // 2) Aggiorna schedule entry
      await apiClient.patch(`/api/schedule/entries/${selectedOp.entry_id}`, {
        status: entryStatusFromOp(newStatus),
        actual_start: actualStart || null,
        actual_end: actualEnd || null,
        interruption_reason:
          newStatus === "INTERRUPTED" ? interruptionReason : null,
      });

      // 3) Se richiesto → triggera reschedulazione incrementale
      if (triggerReschedule) {
        const { data: effect } = await apiClient.post<SimulationEffect>(
          `/api/scenarios/${activeScenarioId}/reschedule`,
          { reason: `Aggiornamento stato operazione: ${selectedOp.operation_description}` }
        );
        return effect;
      }
      return null;
    },
    onSuccess: (effect) => {
      setSimEffect(effect);
      setApplySuccess(true);
      qc.invalidateQueries({ queryKey: ["sim-ops", activeScenarioId] });
      qc.invalidateQueries({ queryKey: ["gantt", activeScenarioId] });
      qc.invalidateQueries({ queryKey: ["enriched-gantt", activeScenarioId] });
    },
  });

  function entryStatusFromOp(s: OperationStatus): ScheduleEntryStatus {
    const map: Record<OperationStatus, ScheduleEntryStatus> = {
      PENDING: "SCHEDULED",
      IN_PROGRESS: "IN_PROGRESS",
      COMPLETED: "COMPLETED",
      BLOCKED: "SCHEDULED",
      INTERRUPTED: "INTERRUPTED",
    };
    return map[s];
  }

  // ── UI ────────────────────────────────────────────────────────────────────
  if (!activeScenarioId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Seleziona uno scenario attivo dal Scenario Manager per usare il simulatore.
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        height: "100%",
        overflow: "hidden",
        fontFamily: "system-ui, sans-serif",
        background: "#f8fafc",
      }}
    >
      {/* ── PANNELLO SX: lista operazioni ── */}
      <div
        style={{
          width: 380,
          flexShrink: 0,
          borderRight: "1px solid #e2e8f0",
          background: "#fff",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "14px 14px 10px",
            borderBottom: "1px solid #f1f5f9",
            flexShrink: 0,
          }}
        >
          <h2
            style={{ fontSize: 14, fontWeight: 700, color: "#1e293b", marginBottom: 2 }}
          >
            🎛️ Simulatore Operazioni
          </h2>
          <p style={{ fontSize: 11, color: "#64748b", lineHeight: 1.4 }}>
            Seleziona un'operazione, cambia stato e osserva l'impatto sul piano.
          </p>
        </div>

        {/* Filtri */}
        <div
          style={{
            padding: "8px 12px",
            borderBottom: "1px solid #f1f5f9",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            flexShrink: 0,
          }}
        >
          <input
            type="text"
            placeholder="Cerca operazione, ordine, operatore…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              width: "100%",
              padding: "6px 10px",
              fontSize: 12,
              border: "1px solid #e2e8f0",
              borderRadius: 6,
              outline: "none",
              boxSizing: "border-box",
            }}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              style={{
                flex: 1,
                fontSize: 11,
                padding: "4px 6px",
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                background: "#fff",
              }}
            >
              <option value="ALL">Tutti i tipi</option>
              <option value="ELECTRICAL">Electrical</option>
              <option value="MECHANICAL">Mechanical</option>
              <option value="GENERAL">General</option>
            </select>
            <select
              value={filterLevel}
              onChange={(e) => setFilterLevel(e.target.value)}
              style={{
                flex: 1,
                fontSize: 11,
                padding: "4px 6px",
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                background: "#fff",
              }}
            >
              <option value="ALL">Tutti i livelli</option>
              <option value="MACHINE">Machine</option>
              <option value="MACROAGGREGATE">Macroaggregato</option>
              <option value="AGGREGATE">Aggregato</option>
              <option value="GROUP">Gruppo</option>
            </select>
          </div>
          <div style={{ fontSize: 10, color: "#94a3b8" }}>
            {filtered.length} di {ops.length} operazioni
          </div>
        </div>

        {/* Lista */}
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 10px" }}>
          {isLoading ? (
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                padding: 24,
                color: "#94a3b8",
                fontSize: 12,
              }}
            >
              Caricamento…
            </div>
          ) : filtered.length === 0 ? (
            <div
              style={{
                textAlign: "center",
                padding: 24,
                color: "#94a3b8",
                fontSize: 12,
              }}
            >
              Nessuna operazione trovata
            </div>
          ) : (
            filtered.map((op) => (
              <OperationCard
                key={op.entry_id}
                op={op}
                isSelected={selectedOp?.entry_id === op.entry_id}
                onClick={() => setSelectedOp(op)}
              />
            ))
          )}
        </div>
      </div>

      {/* ── PANNELLO CENTRALE: form ── */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minWidth: 0,
        }}
      >
        {!selectedOp ? (
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              color: "#94a3b8",
              gap: 12,
            }}
          >
            <div style={{ fontSize: 48 }}>🎯</div>
            <p style={{ fontSize: 14, fontWeight: 600 }}>
              Seleziona un'operazione dalla lista
            </p>
            <p style={{ fontSize: 12, textAlign: "center", maxWidth: 300 }}>
              Potrai cambiarne lo stato, impostare avanzamento e date reali, e
              vedere in tempo reale come il piano si aggiusta.
            </p>
          </div>
        ) : (
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              padding: 20,
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            {/* Header operazione selezionata */}
            <div
              style={{
                background: "#fff",
                border: "1px solid #e2e8f0",
                borderRadius: 10,
                padding: 16,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: 12,
                }}
              >
                <div>
                  <h3
                    style={{ fontSize: 15, fontWeight: 700, color: "#1e293b", marginBottom: 4 }}
                  >
                    {selectedOp.operation_description}
                  </h3>
                  <div
                    style={{
                      display: "flex",
                      gap: 8,
                      flexWrap: "wrap",
                      fontSize: 11,
                      color: "#64748b",
                    }}
                  >
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: 10,
                        background:
                          OP_TYPE_COLOR[selectedOp.operation_type] + "20",
                        color: OP_TYPE_COLOR[selectedOp.operation_type],
                        fontWeight: 600,
                      }}
                    >
                      {selectedOp.operation_type}
                    </span>
                    <span>📦 {selectedOp.production_order_material}</span>
                    <span>·</span>
                    <span>👤 {selectedOp.operator_name}</span>
                    <span>·</span>
                    <span>🏭 {selectedOp.workcenter_code}</span>
                    {selectedOp.is_critical_path && (
                      <span style={{ color: "#f59e0b", fontWeight: 700 }}>
                        ⭐ Critical Path
                      </span>
                    )}
                  </div>
                </div>
                <StatusBadge status={selectedOp.op_status} />
              </div>

              <div
                style={{
                  marginTop: 10,
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 8,
                }}
              >
                <div
                  style={{
                    background: "#f8fafc",
                    borderRadius: 6,
                    padding: "8px 10px",
                    fontSize: 11,
                  }}
                >
                  <div style={{ color: "#94a3b8", marginBottom: 2 }}>
                    Inizio pianificato
                  </div>
                  <div style={{ color: "#1e293b", fontWeight: 600 }}>
                    {fmtDt(selectedOp.scheduled_start)}
                  </div>
                </div>
                <div
                  style={{
                    background: "#f8fafc",
                    borderRadius: 6,
                    padding: "8px 10px",
                    fontSize: 11,
                  }}
                >
                  <div style={{ color: "#94a3b8", marginBottom: 2 }}>
                    Fine pianificata
                  </div>
                  <div style={{ color: "#1e293b", fontWeight: 600 }}>
                    {fmtDt(selectedOp.scheduled_end)}
                  </div>
                </div>
              </div>
            </div>

            {/* Form: cambia stato */}
            <div
              style={{
                background: "#fff",
                border: "1px solid #e2e8f0",
                borderRadius: 10,
                padding: 16,
              }}
            >
              <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: "#1e293b" }}>
                Nuovo stato
              </h4>

              {/* Selezione stato */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: 8,
                  marginBottom: 16,
                }}
              >
                {(
                  [
                    "PENDING",
                    "IN_PROGRESS",
                    "COMPLETED",
                    "BLOCKED",
                    "INTERRUPTED",
                  ] as OperationStatus[]
                ).map((s) => {
                  const meta = STATUS_META[s];
                  const isSelected = newStatus === s;
                  return (
                    <button
                      key={s}
                      onClick={() => setNewStatus(s)}
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        gap: 4,
                        padding: "10px 8px",
                        borderRadius: 8,
                        border: `2px solid ${isSelected ? meta.color : "#e2e8f0"}`,
                        background: isSelected ? meta.bg : "#fff",
                        cursor: "pointer",
                        transition: "all 0.15s",
                      }}
                    >
                      <span style={{ color: meta.color }}>{meta.icon}</span>
                      <span
                        style={{
                          fontSize: 11,
                          fontWeight: 600,
                          color: isSelected ? meta.color : "#64748b",
                        }}
                      >
                        {meta.label}
                      </span>
                    </button>
                  );
                })}
              </div>

              {/* Descrizione stato selezionato */}
              <div
                style={{
                  padding: "8px 10px",
                  background: STATUS_META[newStatus].bg,
                  borderRadius: 6,
                  fontSize: 11,
                  color: STATUS_META[newStatus].color,
                  marginBottom: 16,
                  display: "flex",
                  gap: 6,
                  alignItems: "flex-start",
                }}
              >
                <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />
                {STATUS_META[newStatus].description}
              </div>

              {/* Progress % — solo se IN_PROGRESS o INTERRUPTED */}
              {(newStatus === "IN_PROGRESS" || newStatus === "INTERRUPTED") && (
                <div style={{ marginBottom: 14 }}>
                  <label
                    style={{ fontSize: 12, fontWeight: 600, color: "#374151", display: "block", marginBottom: 6 }}
                  >
                    Avanzamento: {progressPct}%
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={99}
                    value={progressPct}
                    onChange={(e) => setProgressPct(Number(e.target.value))}
                    style={{ width: "100%", accentColor: "#1d4ed8" }}
                  />
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: 10,
                      color: "#94a3b8",
                    }}
                  >
                    <span>0%</span>
                    <span>Il solver riprenderà da questo punto</span>
                    <span>99%</span>
                  </div>
                </div>
              )}

              {/* Date reali */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
                <div>
                  <label
                    style={{ fontSize: 12, fontWeight: 600, color: "#374151", display: "block", marginBottom: 4 }}
                  >
                    Inizio reale
                  </label>
                  <input
                    type="datetime-local"
                    value={actualStart}
                    onChange={(e) => setActualStart(e.target.value)}
                    style={{
                      width: "100%",
                      padding: "6px 8px",
                      fontSize: 12,
                      border: "1px solid #e2e8f0",
                      borderRadius: 6,
                      boxSizing: "border-box",
                    }}
                  />
                </div>
                <div>
                  <label
                    style={{ fontSize: 12, fontWeight: 600, color: "#374151", display: "block", marginBottom: 4 }}
                  >
                    Fine reale
                    {estimatedDelay !== null && estimatedDelay > 0 && (
                      <span
                        style={{ color: "#dc2626", marginLeft: 6, fontWeight: 700 }}
                      >
                        ⚠️ {fmtDelta(estimatedDelay)} ritardo
                      </span>
                    )}
                    {estimatedDelay !== null && estimatedDelay < 0 && (
                      <span
                        style={{ color: "#15803d", marginLeft: 6, fontWeight: 700 }}
                      >
                        ✅ {fmtDelta(estimatedDelay)} anticipo
                      </span>
                    )}
                  </label>
                  <input
                    type="datetime-local"
                    value={actualEnd}
                    onChange={(e) => setActualEnd(e.target.value)}
                    style={{
                      width: "100%",
                      padding: "6px 8px",
                      fontSize: 12,
                      border: `1px solid ${
                        estimatedDelay !== null && estimatedDelay > 0
                          ? "#dc2626"
                          : "#e2e8f0"
                      }`,
                      borderRadius: 6,
                      boxSizing: "border-box",
                    }}
                  />
                </div>
              </div>

              {/* Motivo interruzione */}
              {newStatus === "INTERRUPTED" && (
                <div style={{ marginBottom: 14 }}>
                  <label
                    style={{ fontSize: 12, fontWeight: 600, color: "#374151", display: "block", marginBottom: 4 }}
                  >
                    Motivo interruzione *
                  </label>
                  <input
                    type="text"
                    placeholder="Es. Componente mancante, guasto attrezzatura…"
                    value={interruptionReason}
                    onChange={(e) => setInterruptionReason(e.target.value)}
                    style={{
                      width: "100%",
                      padding: "6px 8px",
                      fontSize: 12,
                      border: "1px solid #e2e8f0",
                      borderRadius: 6,
                      boxSizing: "border-box",
                    }}
                  />
                </div>
              )}

              {/* Toggle rischedulazione */}
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  cursor: "pointer",
                  marginBottom: 14,
                  fontSize: 12,
                  color: "#374151",
                }}
              >
                <input
                  type="checkbox"
                  checked={triggerReschedule}
                  onChange={(e) => setTriggerReschedule(e.target.checked)}
                  style={{ accentColor: "#1d4ed8", width: 14, height: 14 }}
                />
                <span>
                  <strong>Rischedulare automaticamente</strong> dopo il cambio stato
                  {triggerReschedule && (
                    <span style={{ color: "#64748b", marginLeft: 4 }}>
                      — CP-SAT aggiorna il piano considerando il nuovo stato
                    </span>
                  )}
                </span>
              </label>

              {/* Pulsante applica */}
              <button
                onClick={() => updateMutation.mutate()}
                disabled={updateMutation.isPending}
                style={{
                  width: "100%",
                  padding: "10px",
                  background: updateMutation.isPending ? "#94a3b8" : "#1d4ed8",
                  color: "#fff",
                  border: "none",
                  borderRadius: 8,
                  fontSize: 13,
                  fontWeight: 700,
                  cursor: updateMutation.isPending ? "not-allowed" : "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 8,
                  transition: "background 0.15s",
                }}
              >
                {updateMutation.isPending ? (
                  <>
                    <RefreshCw size={14} className="animate-spin" />
                    {triggerReschedule ? "Aggiornando e rischedulando…" : "Aggiornando…"}
                  </>
                ) : (
                  <>
                    <Zap size={14} />
                    Applica cambio stato
                    {triggerReschedule && " + Reschedula"}
                  </>
                )}
              </button>

              {updateMutation.isError && (
                <div
                  style={{
                    marginTop: 8,
                    padding: "8px 10px",
                    background: "#fef2f2",
                    border: "1px solid #fecaca",
                    borderRadius: 6,
                    fontSize: 11,
                    color: "#dc2626",
                  }}
                >
                  ❌ Errore durante l'aggiornamento. Controlla la console.
                </div>
              )}
            </div>

            {/* Risultato rischedulazione */}
            {applySuccess && (
              <div
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 16,
                }}
              >
                <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 12, color: "#1e293b", display: "flex", alignItems: "center", gap: 8 }}>
                  <CheckCircle size={16} color="#15803d" />
                  Cambio applicato
                  {triggerReschedule && simEffect && " — Piano aggiornato"}
                </h4>

                {simEffect ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {/* KPI effetto */}
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(3, 1fr)",
                        gap: 8,
                      }}
                    >
                      {[
                        {
                          label: "Operazioni rischeduled",
                          value: simEffect.rescheduled_count,
                          color: "#1d4ed8",
                        },
                        {
                          label: "In ritardo",
                          value: simEffect.delayed_count,
                          color: simEffect.delayed_count > 0 ? "#dc2626" : "#15803d",
                        },
                        {
                          label: "Sbloccate",
                          value: simEffect.unblocked_count,
                          color: "#15803d",
                        },
                      ].map((kpi) => (
                        <div
                          key={kpi.label}
                          style={{
                            background: "#f8fafc",
                            borderRadius: 8,
                            padding: "10px",
                            textAlign: "center",
                          }}
                        >
                          <div
                            style={{
                              fontSize: 22,
                              fontWeight: 800,
                              color: kpi.color,
                              lineHeight: 1,
                            }}
                          >
                            {kpi.value}
                          </div>
                          <div style={{ fontSize: 10, color: "#64748b", marginTop: 3 }}>
                            {kpi.label}
                          </div>
                        </div>
                      ))}
                    </div>

                    {/* Delta makespan */}
                    {simEffect.old_estimated_end && simEffect.new_estimated_end && (
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "10px 12px",
                          background:
                            simEffect.new_estimated_end > simEffect.old_estimated_end
                              ? "#fef2f2"
                              : "#f0fdf4",
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                      >
                        <span style={{ color: "#64748b" }}>Fine stimata:</span>
                        <span style={{ fontWeight: 600, color: "#94a3b8", textDecoration: "line-through" }}>
                          {fmtDt(simEffect.old_estimated_end)}
                        </span>
                        <ArrowRight size={12} color="#64748b" />
                        <span
                          style={{
                            fontWeight: 700,
                            color:
                              simEffect.new_estimated_end > simEffect.old_estimated_end
                                ? "#dc2626"
                                : "#15803d",
                          }}
                        >
                          {fmtDt(simEffect.new_estimated_end)}
                        </span>
                      </div>
                    )}

                    {simEffect.critical_path_changed && (
                      <div
                        style={{
                          padding: "8px 10px",
                          background: "#fffbeb",
                          border: "1px solid #fde68a",
                          borderRadius: 6,
                          fontSize: 11,
                          color: "#92400e",
                          fontWeight: 600,
                        }}
                      >
                        ⚠️ Il critical path è cambiato dopo la rischedulazione
                      </div>
                    )}

                    {/* Operazioni impattate */}
                    {simEffect.affected_entries.length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                          Operazioni spostate ({simEffect.affected_entries.length}):
                        </div>
                        <div
                          style={{
                            maxHeight: 200,
                            overflowY: "auto",
                            display: "flex",
                            flexDirection: "column",
                            gap: 4,
                          }}
                        >
                          {simEffect.affected_entries.map((ae) => (
                            <div
                              key={ae.entry_id}
                              style={{
                                display: "flex",
                                justifyContent: "space-between",
                                alignItems: "center",
                                padding: "6px 10px",
                                background: "#f8fafc",
                                borderRadius: 6,
                                fontSize: 11,
                              }}
                            >
                              <span style={{ color: "#374151", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {ae.operation_description}
                              </span>
                              <span
                                style={{
                                  flexShrink: 0,
                                  marginLeft: 8,
                                  fontWeight: 700,
                                  color: ae.delta_minutes > 0 ? "#dc2626" : "#15803d",
                                }}
                              >
                                {fmtDelta(ae.delta_minutes)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div style={{ fontSize: 12, color: "#64748b" }}>
                    Stato aggiornato senza rischedulazione.
                    <br />
                    Vai al Gantt per vedere la modifica.
                  </div>
                )}

                <button
                  onClick={() => {
                    setApplySuccess(false);
                    setSimEffect(null);
                    setSelectedOp(null);
                  }}
                  style={{
                    marginTop: 12,
                    width: "100%",
                    padding: "8px",
                    background: "transparent",
                    border: "1px solid #e2e8f0",
                    borderRadius: 6,
                    fontSize: 12,
                    cursor: "pointer",
                    color: "#64748b",
                  }}
                >
                  Simula un'altra operazione
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── PANNELLO DX: info contestuale ── */}
      <div
        style={{
          width: 280,
          flexShrink: 0,
          borderLeft: "1px solid #e2e8f0",
          background: "#fff",
          padding: 16,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <h4 style={{ fontSize: 12, fontWeight: 700, color: "#374151", margin: 0 }}>
          📖 Come funziona
        </h4>

        {[
          {
            icon: "1️⃣",
            title: "Seleziona un'operazione",
            body: "Scegli dalla lista a sinistra. Usa i filtri per tipo (ELECTRICAL / MECHANICAL / GENERAL) o livello BOM.",
          },
          {
            icon: "2️⃣",
            title: "Scegli il nuovo stato",
            body: "Ogni stato ha un effetto diverso sul piano: COMPLETED con ritardo sposta i successori, INTERRUPTED lascia il solver riprendere da progress_pct.",
          },
          {
            icon: "3️⃣",
            title: "Imposta date reali",
            body: 'Se "fine reale" > "fine pianificata" il solver vede un ritardo e rischedulerà le operazioni successive del DAG.',
          },
          {
            icon: "4️⃣",
            title: "Reschedula (opzionale)",
            body: "Con il toggle attivo, al salvataggio viene avviato il reschedule incrementale CP-SAT. Il risultato compare qui in pochi secondi.",
          },
        ].map((step) => (
          <div key={step.icon} style={{ display: "flex", gap: 10 }}>
            <span style={{ fontSize: 18, flexShrink: 0, lineHeight: 1.2 }}>{step.icon}</span>
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#1e293b", marginBottom: 2 }}>
                {step.title}
              </div>
              <div style={{ fontSize: 11, color: "#64748b", lineHeight: 1.5 }}>
                {step.body}
              </div>
            </div>
          </div>
        ))}

        <div
          style={{
            marginTop: 4,
            padding: "10px 12px",
            background: "#fffbeb",
            border: "1px solid #fde68a",
            borderRadius: 8,
            fontSize: 11,
            color: "#92400e",
            lineHeight: 1.5,
          }}
        >
          <strong>Nota:</strong> Il simulatore aggiorna il DB reale. Usa questa pagina
          per testare scenari "what-if" su uno scenario non ancora attivato in produzione.
        </div>

        {/* Riepilogo stati */}
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#374151", marginBottom: 8 }}>
            Glossario stati
          </div>
          {(Object.entries(STATUS_META) as [OperationStatus, typeof STATUS_META[OperationStatus]][]).map(
            ([k, v]) => (
              <div
                key={k}
                style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "flex-start" }}
              >
                <span style={{ color: v.color, flexShrink: 0, marginTop: 1 }}>{v.icon}</span>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: v.color }}>
                    {v.label}
                  </div>
                  <div style={{ fontSize: 10, color: "#64748b", lineHeight: 1.4 }}>
                    {v.description}
                  </div>
                </div>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}