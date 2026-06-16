// frontend/src/components/dag/DAGViewerEnhanced.tsx
//
// DAG Viewer migliorato:
// - Mostra descrizione ordine + materiale + numero di operazioni invece del solo codice RP
// - Numero di priorità calcolato dall'ordine topologico
// - Tooltip con elenco operazioni
// - Highlight predecessori/successori al hover
// - Legenda chiara sulla logica di precedenza
//
// Backend endpoint richiesto: GET /api/dag/{machine_order_id}/enriched
// Schema risposta:
// {
//   nodes: [{ id, rp_code, rp_label, target_order_material, target_order_description,
//             target_level, operations_count, operations: [{id, description}],
//             priority_rank }],
//   edges: [{ from, to }]
// }

import { useEffect, useMemo, useState, useCallback } from "react";
import ReactFlow, {
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
  ConnectionMode,
  MarkerType,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
} from "reactflow";
import dagre from "dagre";
import "reactflow/dist/style.css";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Info, GitBranch, Eye, EyeOff } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import axios from "axios";

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
  apiBase?: string;
}

// ============================================================================
// LEVEL COLORS
// ============================================================================

const LEVEL_STYLE: Record<DagNode["target_level"], { bg: string; border: string; text: string }> = {
  MACROAGGREGATE: { bg: "#CECBF6", border: "#534AB7", text: "#26215C" },
  AGGREGATE:      { bg: "#9FE1CB", border: "#1D9E75", text: "#04342C" },
  GROUP:          { bg: "#FAC775", border: "#BA7517", text: "#412402" },
};

// ============================================================================
// CUSTOM NODE
// ============================================================================

interface RPNodeData {
  node: DagNode;
  isHighlighted: boolean;
  isDimmed: boolean;
  onOpenOps: (node: DagNode) => void;
}

function RPNode({ data }: { data: RPNodeData }): JSX.Element {
  const { node, isHighlighted, isDimmed, onOpenOps } = data;
  const style = LEVEL_STYLE[node.target_level];
  return (
    <div
      style={{
        background: style.bg,
        border: `1.5px solid ${style.border}`,
        borderRadius: 8,
        padding: "8px 12px",
        minWidth: 200,
        maxWidth: 240,
        opacity: isDimmed ? 0.25 : 1,
        boxShadow: isHighlighted ? `0 0 0 3px ${style.border}55` : "none",
        transition: "all 0.15s ease",
        cursor: "pointer",
        color: style.text,
        fontFamily: "system-ui, sans-serif",
      }}
      onClick={() => onOpenOps(node)}
    >
      <Handle type="target" position={Position.Left} style={{ background: style.border }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 11, fontWeight: 600, fontFamily: "ui-monospace, monospace" }}>
          #{node.priority_rank} · {node.target_order_material}
        </span>
        <span style={{
          fontSize: 9,
          fontWeight: 500,
          background: style.border,
          color: "white",
          padding: "1px 6px",
          borderRadius: 8,
        }}>
          {node.target_level.slice(0, 3)}
        </span>
      </div>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2, lineHeight: 1.25 }}>
        {node.target_order_description}
      </div>
      <div style={{ fontSize: 10, opacity: 0.7 }}>
        {node.operations_count} operazion{node.operations_count === 1 ? "e" : "i"} · {node.rp_code}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: style.border }} />
    </div>
  );
}

const nodeTypes = { rp: RPNode };

// ============================================================================
// LAYOUT (dagre)
// ============================================================================

function autoLayout(nodes: Node[], edges: Edge[]): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 30, ranksep: 80 });
  g.setDefaultEdgeLabel(() => ({}));

  nodes.forEach((n) => g.setNode(n.id, { width: 220, height: 70 }));
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  const laidOut = nodes.map((n) => {
    const pos = g.node(n.id);
    return { ...n, position: { x: pos.x - 110, y: pos.y - 35 } };
  });

  return { nodes: laidOut, edges };
}

// ============================================================================
// COMPONENT
// ============================================================================

export default function DAGViewerEnhanced({ machineOrderId, apiBase = "" }: Props): JSX.Element {
  const [data, setData] = useState<EnrichedDagResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [opsDialogNode, setOpsDialogNode] = useState<DagNode | null>(null);
  const [showLegend, setShowLegend] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    axios
      .get<EnrichedDagResponse>(`${apiBase}/api/dag/${machineOrderId}/enriched`)
      .then((r) => {
        if (!cancelled) setData(r.data);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [machineOrderId, apiBase]);

  // Build adjacency for highlight
  const adjacency = useMemo(() => {
    const preds = new Map<string, Set<string>>();
    const succs = new Map<string, Set<string>>();
    if (data) {
      for (const e of data.edges) {
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
    const stack = [hoveredId];
    while (stack.length) {
      const cur = stack.pop()!;
      for (const p of adjacency.preds.get(cur) ?? []) {
        if (!result.has(p)) {
          result.add(p);
          stack.push(p);
        }
      }
    }
    const stack2 = [hoveredId];
    while (stack2.length) {
      const cur = stack2.pop()!;
      for (const s of adjacency.succs.get(cur) ?? []) {
        if (!result.has(s)) {
          result.add(s);
          stack2.push(s);
        }
      }
    }
    return result;
  }, [hoveredId, adjacency, data]);

  const { initialNodes, initialEdges } = useMemo(() => {
    if (!data) return { initialNodes: [], initialEdges: [] };

    const rfNodes: Node[] = data.nodes.map((n) => ({
      id: n.id,
      type: "rp",
      position: { x: 0, y: 0 },
      data: {
        node: n,
        isHighlighted: hoveredId !== null && highlighted.has(n.id),
        isDimmed: hoveredId !== null && !highlighted.has(n.id),
        onOpenOps: (node: DagNode) => setOpsDialogNode(node),
      } as RPNodeData,
    }));

    const rfEdges: Edge[] = data.edges.map((e, i) => {
      const isHl = hoveredId !== null && highlighted.has(e.from) && highlighted.has(e.to);
      const isDimmed = hoveredId !== null && !isHl;
      return {
        id: `e-${i}`,
        source: e.from,
        target: e.to,
        type: "smoothstep",
        animated: isHl,
        style: {
          stroke: isHl ? "#534AB7" : "#999",
          strokeWidth: isHl ? 2.5 : 1.2,
          opacity: isDimmed ? 0.15 : 1,
          transition: "all 0.15s",
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: isHl ? "#534AB7" : "#999",
          width: 14,
          height: 14,
        },
      };
    });

    const laid = autoLayout(rfNodes, rfEdges);
    return { initialNodes: laid.nodes, initialEdges: laid.edges };
  }, [data, hoveredId, highlighted]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  const onNodeMouseEnter = useCallback((_: unknown, n: Node) => {
    setHoveredId(n.id);
  }, []);
  const onNodeMouseLeave = useCallback(() => {
    setHoveredId(null);
  }, []);

  if (loading) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-stone-500">
          Caricamento DAG…
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-red-600">
          Errore caricamento DAG: {error}
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <GitBranch className="h-5 w-5" />
              DAG delle priorità — {data.nodes.length} nodi, {data.edges.length} archi
            </CardTitle>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowLegend((v) => !v)}
              >
                {showLegend ? <EyeOff className="h-3.5 w-3.5 mr-1" /> : <Eye className="h-3.5 w-3.5 mr-1" />}
                Legenda
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {showLegend && (
            <div className="mx-4 my-3 p-3 rounded-md bg-purple-50 border border-purple-200 text-xs text-purple-900 space-y-1">
              <div className="flex items-center gap-2 font-semibold">
                <Info className="h-3.5 w-3.5" />
                Come leggere questo grafo
              </div>
              <div>
                Ogni nodo è un <strong>ordine target</strong> di un Reference Point (un macroaggregato, aggregato o gruppo).
                Il numero <span className="font-mono">#N</span> in alto è la priorità calcolata dall&apos;ordine topologico —
                gli ordini con priorità minore devono completarsi prima di quelli con priorità maggiore.
              </div>
              <div>
                Una freccia <span className="font-mono">A → B</span> significa: <strong>tutte le operazioni dell&apos;ordine A
                e di tutti i suoi figli BOM devono terminare</strong> prima che le operazioni dell&apos;ordine B possano iniziare.
                Lo scheduler traduce questo come <span className="font-mono">op_start(B) ≥ max(op_end di tutte le op del sottoalbero di A)</span>.
              </div>
              <div>
                Passa il mouse su un nodo per evidenziare la sua catena di predecessori e successori. Clicca per vedere le operazioni.
              </div>
              <div className="pt-1 flex gap-3 flex-wrap">
                <div className="flex items-center gap-1">
                  <span className="inline-block w-3 h-3 rounded" style={{ background: LEVEL_STYLE.MACROAGGREGATE.bg, border: `1.5px solid ${LEVEL_STYLE.MACROAGGREGATE.border}` }} />
                  Macroaggregato
                </div>
                <div className="flex items-center gap-1">
                  <span className="inline-block w-3 h-3 rounded" style={{ background: LEVEL_STYLE.AGGREGATE.bg, border: `1.5px solid ${LEVEL_STYLE.AGGREGATE.border}` }} />
                  Aggregato
                </div>
                <div className="flex items-center gap-1">
                  <span className="inline-block w-3 h-3 rounded" style={{ background: LEVEL_STYLE.GROUP.bg, border: `1.5px solid ${LEVEL_STYLE.GROUP.border}` }} />
                  Gruppo
                </div>
              </div>
            </div>
          )}

          <div style={{ height: 600, background: "#FAFAF8" }}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeMouseEnter={onNodeMouseEnter}
              onNodeMouseLeave={onNodeMouseLeave}
              connectionMode={ConnectionMode.Loose}
              proOptions={{ hideAttribution: true }}
              fitView
              fitViewOptions={{ padding: 0.15 }}
            >
              <Background gap={20} size={1} color="#E5E5E5" />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          </div>
        </CardContent>
      </Card>

      {/* Operations dialog */}
      <Dialog open={opsDialogNode !== null} onOpenChange={(v) => !v && setOpsDialogNode(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {opsDialogNode?.target_order_material} — {opsDialogNode?.target_order_description}
            </DialogTitle>
          </DialogHeader>
          {opsDialogNode && (
            <div className="space-y-3">
              <div className="flex gap-2 flex-wrap text-xs">
                <Badge variant="outline">RP: {opsDialogNode.rp_code}</Badge>
                <Badge variant="outline">{opsDialogNode.target_level}</Badge>
                <Badge variant="outline">Priorità #{opsDialogNode.priority_rank}</Badge>
              </div>
              <div>
                <div className="text-sm font-semibold mb-2">
                  Operazioni vincolate da questo nodo ({opsDialogNode.operations.length}):
                </div>
                <ul className="space-y-1 text-sm">
                  {opsDialogNode.operations.map((op) => (
                    <li key={op.id} className="flex items-start gap-2 p-2 bg-stone-50 rounded">
                      <span className="font-mono text-xs text-stone-500 mt-0.5">→</span>
                      <span>{op.description}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="text-xs text-stone-500 italic border-t pt-3">
                Tutte queste operazioni, e quelle di tutti gli ordini figli nella BOM, devono
                terminare prima che le operazioni dei nodi successori possano iniziare.
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}