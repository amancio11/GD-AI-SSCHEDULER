// frontend/src/components/dag/DAGViewerEnhanced.tsx
//
// DAG Viewer RP — versione corretta
// Fixes:
//   1. machineOrderId vuoto/null → mostra "Seleziona un ordine macchina" invece di spinner infinito
//   2. Errore fetch → mostra dettaglio errore HTTP per debug
//   3. nodes/edges vuoti → mostra stato esplicito con suggerimento
//   4. Layout dagre robusto: nodi isolati (senza archi) ricevono posizione griglia
//   5. Hover highlight funziona su nodi isolati

import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import ReactFlow, {
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
  MarkerType,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
  useReactFlow,
} from "reactflow";
import dagre from "dagre";
import "reactflow/dist/style.css";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import apiClient from "../../api/client";

// ============================================================================
// TYPES
// ============================================================================

interface DagOperation {
  id: string;
  description: string;
}

interface DagNode {
  id: string;
  rp_code: string;
  rp_label: string;
  target_order_material: string;
  target_order_description: string;
  target_level: "MACROAGGREGATE" | "AGGREGATE" | "GROUP";
  operations_count: number;
  operations: DagOperation[];
  priority_rank: number;
}

interface DagEdge {
  from: string;
  to: string;
}

interface EnrichedDagResponse {
  nodes: DagNode[];
  edges: DagEdge[];
}

interface Props {
  machineOrderId: string;
}

// ============================================================================
// LEVEL COLORS
// ============================================================================

const LEVEL_STYLE: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  MACROAGGREGATE: { bg: "#CECBF6", border: "#534AB7", text: "#26215C", badge: "#534AB7" },
  AGGREGATE:      { bg: "#9FE1CB", border: "#1D9E75", text: "#04342C", badge: "#1D9E75" },
  GROUP:          { bg: "#FAC775", border: "#BA7517", text: "#412402", badge: "#BA7517" },
};

const FALLBACK_STYLE = { bg: "#E5E7EB", border: "#6B7280", text: "#1F2937", badge: "#6B7280" };

// ============================================================================
// CUSTOM NODE
// ============================================================================

interface RPNodeData {
  node: DagNode;
  isHighlighted: boolean;
  isDimmed: boolean;
  onOpenOps: (node: DagNode) => void;
  onHover: (id: string | null) => void;
}

function RPNode({ data }: { data: RPNodeData }): JSX.Element {
  const { node, isHighlighted, isDimmed, onOpenOps, onHover } = data;
  const style = LEVEL_STYLE[node.target_level] ?? FALLBACK_STYLE;

  return (
    <div
      onMouseEnter={() => onHover(node.id)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onOpenOps(node)}
      style={{
        background: style.bg,
        border: `2px solid ${isHighlighted ? style.border : style.border + "99"}`,
        borderRadius: 8,
        padding: "8px 12px",
        minWidth: 210,
        maxWidth: 250,
        opacity: isDimmed ? 0.2 : 1,
        boxShadow: isHighlighted ? `0 0 0 3px ${style.border}44, 0 4px 12px rgba(0,0,0,0.15)` : "0 1px 4px rgba(0,0,0,0.08)",
        transition: "all 0.12s ease",
        cursor: "pointer",
        color: style.text,
        fontFamily: "system-ui, sans-serif",
        userSelect: "none",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: style.border, width: 8, height: 8 }} />

      {/* Header: priorità + materiale + badge livello */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 4 }}>
        <span style={{
          fontSize: 10,
          fontWeight: 700,
          fontFamily: "ui-monospace, monospace",
          opacity: 0.75,
        }}>
          #{node.priority_rank} · {node.target_order_material}
        </span>
        <span style={{
          fontSize: 9,
          fontWeight: 600,
          background: style.badge,
          color: "white",
          padding: "1px 6px",
          borderRadius: 8,
          letterSpacing: "0.03em",
          flexShrink: 0,
        }}>
          {node.target_level === "MACROAGGREGATE" ? "MA" : node.target_level === "AGGREGATE" ? "AGG" : "GRP"}
        </span>
      </div>

      {/* Descrizione ordine */}
      <div style={{
        fontSize: 12,
        fontWeight: 600,
        marginBottom: 3,
        lineHeight: 1.3,
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      }}>
        {node.target_order_description}
      </div>

      {/* Codice RP + contatore op */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 4 }}>
        <span style={{ fontSize: 10, opacity: 0.6, fontFamily: "ui-monospace, monospace" }}>
          {node.rp_code}
        </span>
        <span style={{
          fontSize: 10,
          background: style.border + "22",
          color: style.text,
          padding: "1px 5px",
          borderRadius: 6,
          fontWeight: 500,
        }}>
          {node.operations_count} op{node.operations_count !== 1 ? "." : "."}
        </span>
      </div>

      <Handle type="source" position={Position.Right} style={{ background: style.border, width: 8, height: 8 }} />
    </div>
  );
}

const nodeTypes = { rp: RPNode };

// ============================================================================
// LAYOUT — dagre con fallback griglia per nodi isolati
// ============================================================================

function autoLayout(
  rfNodes: Node[],
  rfEdges: Edge[]
): { nodes: Node[]; edges: Edge[] } {
  if (rfNodes.length === 0) return { nodes: [], edges: [] };

  const NODE_W = 250;
  const NODE_H = 80;

  // Nodi che compaiono in almeno un arco
  const connected = new Set<string>();
  rfEdges.forEach((e) => { connected.add(e.source); connected.add(e.target); });

  const connectedNodes = rfNodes.filter((n) => connected.has(n.id));
  const isolatedNodes  = rfNodes.filter((n) => !connected.has(n.id));

  // Layout dagre solo sui nodi connessi
  const laidOut: Node[] = [];

  if (connectedNodes.length > 0) {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 100, marginx: 20, marginy: 20 });
    g.setDefaultEdgeLabel(() => ({}));
    connectedNodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
    rfEdges.forEach((e) => {
      if (connected.has(e.source) && connected.has(e.target)) {
        g.setEdge(e.source, e.target);
      }
    });
    dagre.layout(g);
    connectedNodes.forEach((n) => {
      const pos = g.node(n.id);
      laidOut.push({ ...n, position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 } });
    });
  }

  // Nodi isolati → griglia sotto i connessi
  const maxY = laidOut.length > 0
    ? Math.max(...laidOut.map((n) => n.position.y)) + NODE_H + 60
    : 0;
  const COLS = Math.max(1, Math.ceil(Math.sqrt(isolatedNodes.length)));
  isolatedNodes.forEach((n, i) => {
    const col = i % COLS;
    const row = Math.floor(i / COLS);
    laidOut.push({
      ...n,
      position: {
        x: col * (NODE_W + 30),
        y: maxY + row * (NODE_H + 30),
      },
    });
  });

  return { nodes: laidOut, edges: rfEdges };
}

// ============================================================================
// INNER COMPONENT (accede a useReactFlow)
// ============================================================================

function DAGInner({ machineOrderId }: Props): JSX.Element {
  const [data, setData]     = useState<EnrichedDagResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]   = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [opsDialogNode, setOpsDialogNode] = useState<DagNode | null>(null);

  const { fitView } = useReactFlow();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // ── Fetch ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!machineOrderId || machineOrderId.trim() === "") {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    const url = `/api/dag/${machineOrderId}/enriched`;
    apiClient
      .get<EnrichedDagResponse>(url)
      .then((r) => {
        if (!cancelled) setData(r.data);
      })
      .catch((err) => {
        if (!cancelled) {
          const msg = err?.response
            ? `HTTP ${err.response.status}: ${JSON.stringify(err.response.data)}`
            : String(err);
          setError(msg);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [machineOrderId]);

  // ── Adiacenza per highlight ─────────────────────────────────────────────────
  const adjacency = useMemo(() => {
    const preds = new Map<string, Set<string>>();
    const succs = new Map<string, Set<string>>();
    if (data) {
      for (const e of data.edges ?? []) {
        if (!succs.has(e.from)) succs.set(e.from, new Set());
        succs.get(e.from)!.add(e.to);
        if (!preds.has(e.to)) preds.set(e.to, new Set());
        preds.get(e.to)!.add(e.from);
      }
    }
    return { preds, succs };
  }, [data]);

  const highlighted = useMemo(() => {
    if (!hoveredId || !data) return new Set<string>();
    const result = new Set<string>([hoveredId]);
    // Predecessori
    const stack = [hoveredId];
    while (stack.length) {
      const cur = stack.pop()!;
      for (const p of adjacency.preds.get(cur) ?? []) {
        if (!result.has(p)) { result.add(p); stack.push(p); }
      }
    }
    // Successori
    const stack2 = [hoveredId];
    while (stack2.length) {
      const cur = stack2.pop()!;
      for (const s of adjacency.succs.get(cur) ?? []) {
        if (!result.has(s)) { result.add(s); stack2.push(s); }
      }
    }
    return result;
  }, [hoveredId, adjacency, data]);

  // ── Costruisce nodi/archi React Flow e applica layout ──────────────────────
  useEffect(() => {
    if (!data) { setNodes([]); setEdges([]); return; }

    const rfNodes: Node[] = (data.nodes ?? []).map((n) => ({
      id: n.id,
      type: "rp",
      position: { x: 0, y: 0 },
      data: {
        node: n,
        isHighlighted: hoveredId !== null && highlighted.has(n.id),
        isDimmed: hoveredId !== null && !highlighted.has(n.id),
        onOpenOps: (node: DagNode) => setOpsDialogNode(node),
        onHover: setHoveredId,
      } as RPNodeData,
    }));

    const rfEdges: Edge[] = (data.edges ?? []).map((e, i) => {
      const isHl = hoveredId !== null && highlighted.has(e.from) && highlighted.has(e.to);
      const isDim = hoveredId !== null && !isHl;
      return {
        id: `e-${i}`,
        source: e.from,
        target: e.to,
        type: "smoothstep",
        animated: isHl,
        style: {
          stroke: isHl ? "#534AB7" : "#94a3b8",
          strokeWidth: isHl ? 2.5 : 1.5,
          opacity: isDim ? 0.1 : 0.8,
          transition: "all 0.12s",
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: isHl ? "#534AB7" : "#94a3b8",
          width: 16,
          height: 16,
        },
      };
    });

    const { nodes: laidNodes, edges: laidEdges } = autoLayout(rfNodes, rfEdges);
    setNodes(laidNodes);
    setEdges(laidEdges);

    // fitView dopo che React Flow ha renderizzato
    setTimeout(() => fitView({ padding: 0.12, duration: 300 }), 80);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, hoveredId, highlighted]);

  // ── Aggiorna solo data hover senza ricalcolare layout ─────────────────────
  // (già gestito sopra includendo hoveredId nelle deps)

  // ── Stati di caricamento / errore / vuoto ──────────────────────────────────
  if (!machineOrderId || machineOrderId.trim() === "") {
    return (
      <div style={centeredStyle}>
        <span style={{ fontSize: 32, marginBottom: 12 }}>🔍</span>
        <p style={{ color: "#64748b", fontSize: 14 }}>Seleziona un Ordine Macchina per visualizzare il DAG RP.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={centeredStyle}>
        <div style={spinnerStyle} />
        <p style={{ color: "#64748b", fontSize: 13, marginTop: 12 }}>Caricamento DAG…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ ...centeredStyle, flexDirection: "column", gap: 8, padding: 24 }}>
        <span style={{ fontSize: 28 }}>⚠️</span>
        <p style={{ color: "#dc2626", fontWeight: 600, fontSize: 14 }}>Errore nel caricamento del DAG</p>
        <pre style={{
          background: "#fef2f2",
          border: "1px solid #fca5a5",
          borderRadius: 6,
          padding: "8px 12px",
          fontSize: 11,
          color: "#991b1b",
          maxWidth: 480,
          overflowX: "auto",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}>
          {error}
        </pre>
        <p style={{ color: "#6b7280", fontSize: 12 }}>
          Verifica che il backend sia avviato e che l'endpoint{" "}
          <code style={{ background: "#f1f5f9", padding: "1px 4px", borderRadius: 3 }}>
            /api/dag/{"{machine_order_id}"}/enriched
          </code>{" "}
          risponda correttamente.
        </p>
      </div>
    );
  }

  if (!data || (data.nodes ?? []).length === 0) {
    return (
      <div style={centeredStyle}>
        <span style={{ fontSize: 32, marginBottom: 12 }}>📭</span>
        <p style={{ color: "#64748b", fontSize: 14, fontWeight: 600 }}>DAG RP — 0 nodi</p>
        <p style={{ color: "#94a3b8", fontSize: 12, marginTop: 4, textAlign: "center", maxWidth: 360 }}>
          Non ci sono Reference Point configurati per questo modello macchina.
          <br />
          Verifica che il seed abbia popolato la tabella <code>reference_points</code>{" "}
          e <code>reference_point_precedences</code>.
        </p>
        <p style={{ color: "#94a3b8", fontSize: 11, marginTop: 8 }}>
          Machine Order ID: <code style={{ background: "#f1f5f9", padding: "1px 4px", borderRadius: 3 }}>{machineOrderId}</code>
        </p>
      </div>
    );
  }

  return (
    <>
      {/* Legenda */}
      <div style={{
        position: "absolute",
        top: 12,
        right: 12,
        zIndex: 10,
        background: "rgba(255,255,255,0.97)",
        border: "1px solid #e2e8f0",
        borderRadius: 10,
        padding: "10px 14px",
        fontSize: 11,
        boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
        display: "flex",
        flexDirection: "column",
        gap: 5,
      }}>
        <div style={{ fontWeight: 700, color: "#334155", marginBottom: 2 }}>Legenda livelli</div>
        {(["MACROAGGREGATE", "AGGREGATE", "GROUP"] as const).map((lvl) => {
          const s = LEVEL_STYLE[lvl];
          return (
            <div key={lvl} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{
                width: 14, height: 14, borderRadius: 3,
                background: s.bg, border: `2px solid ${s.border}`,
                flexShrink: 0,
              }} />
              <span style={{ color: "#475569" }}>
                {lvl === "MACROAGGREGATE" ? "Macroaggregato" : lvl === "AGGREGATE" ? "Aggregato" : "Gruppo"}
              </span>
            </div>
          );
        })}
        <div style={{ borderTop: "1px solid #e2e8f0", marginTop: 4, paddingTop: 4, color: "#94a3b8", fontSize: 10 }}>
          {(data.nodes ?? []).length} nodi · {(data.edges ?? []).length} archi
          <br />
          Hover = predecessori/successori · Click = operazioni
        </div>
      </div>

      {/* React Flow canvas */}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        style={{ background: "#f8fafc" }}
        minZoom={0.05}
        maxZoom={2.5}
        attributionPosition="bottom-left"
      >
        <Background color="#e2e8f0" gap={20} />
        <Controls style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8 }} />
        <MiniMap
          style={{ background: "#f1f5f9", border: "1px solid #e2e8f0", borderRadius: 8 }}
          nodeColor={(n) => {
            const nd = n.data as RPNodeData;
            return LEVEL_STYLE[nd?.node?.target_level]?.border ?? "#94a3b8";
          }}
        />
      </ReactFlow>

      {/* Dialog operazioni */}
      <Dialog open={!!opsDialogNode} onOpenChange={() => setOpsDialogNode(null)}>
        <DialogContent style={{ maxWidth: 480 }}>
          <DialogHeader>
            <DialogTitle>
              {opsDialogNode?.rp_code} — {opsDialogNode?.target_order_description}
            </DialogTitle>
          </DialogHeader>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Badge variant="outline">{opsDialogNode?.target_level}</Badge>
              <Badge variant="outline">{opsDialogNode?.target_order_material}</Badge>
              <Badge variant="outline">Priorità #{opsDialogNode?.priority_rank}</Badge>
            </div>
            <p style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
              Operazioni dell'ordine target ({opsDialogNode?.operations_count ?? 0}):
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 280, overflowY: "auto" }}>
              {(opsDialogNode?.operations ?? []).length === 0 ? (
                <p style={{ color: "#94a3b8", fontSize: 12 }}>Nessuna operazione.</p>
              ) : (
                opsDialogNode?.operations.map((op) => (
                  <div key={op.id} style={{
                    background: "#f8fafc",
                    border: "1px solid #e2e8f0",
                    borderRadius: 6,
                    padding: "6px 10px",
                    fontSize: 12,
                    color: "#334155",
                  }}>
                    <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 10, color: "#94a3b8" }}>
                      {op.id.slice(0, 8)}
                    </span>
                    <br />
                    {op.description || "(senza descrizione)"}
                  </div>
                ))
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ============================================================================
// STYLES
// ============================================================================

const centeredStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
  minHeight: 300,
  gap: 8,
};

const spinnerStyle: React.CSSProperties = {
  width: 32,
  height: 32,
  border: "3px solid #e2e8f0",
  borderTop: "3px solid #534AB7",
  borderRadius: "50%",
  animation: "spin 0.8s linear infinite",
};

// ============================================================================
// PUBLIC EXPORT — wrappato in ReactFlowProvider
// ============================================================================

export default function DAGViewerEnhanced(props: Props): JSX.Element {
  return (
    <ReactFlowProvider>
      <div style={{ width: "100%", height: "100%", position: "relative" }}>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        <DAGInner {...props} />
      </div>
    </ReactFlowProvider>
  );
}