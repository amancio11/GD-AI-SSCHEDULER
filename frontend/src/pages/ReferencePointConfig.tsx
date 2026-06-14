import { useCallback, useEffect, useMemo, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useReferencePoints, useReferencePointPrecedences } from '../api/hooks/useReferencePoints';
import type { ReferencePoint, ReferencePointPrecedence } from '../api/types';
import { X, Save, AlertTriangle } from 'lucide-react';

// ── Constants ─────────────────────────────────────────────────────────────────

const MACHINE_MODEL_ID = import.meta.env.VITE_DEFAULT_MACHINE_MODEL_ID ?? '';

const LEVEL_COLORS: Record<string, string> = {
  MACROAGGREGATE: '#7c3aed', // violet
  AGGREGATE:      '#0d9488', // teal
};

// ── DFS cycle detection ───────────────────────────────────────────────────────

/**
 * Returns the set of node IDs that are part of a cycle in the given adjacency map.
 * Returns empty set if the graph is acyclic.
 */
function detectCycle(adjacency: Map<string, string[]>): Set<string> {
  const visited  = new Set<string>();
  const recStack = new Set<string>();
  const cycleNodes = new Set<string>();

  function dfs(node: string): boolean {
    visited.add(node);
    recStack.add(node);

    for (const neighbour of adjacency.get(node) ?? []) {
      if (!visited.has(neighbour)) {
        if (dfs(neighbour)) {
          cycleNodes.add(neighbour);
          return true;
        }
      } else if (recStack.has(neighbour)) {
        cycleNodes.add(node);
        cycleNodes.add(neighbour);
        return true;
      }
    }

    recStack.delete(node);
    return false;
  }

  for (const node of adjacency.keys()) {
    if (!visited.has(node)) dfs(node);
  }

  return cycleNodes;
}

// ── Auto-layout (simple top-down, layer by in-degree) ────────────────────────

function buildLayout(
  rps: ReferencePoint[],
  predecessorMap: Map<string, string[]>, // rp_id → predecessor_ids
): { x: number; y: number }[] {
  // Topological sort to assign layers
  const inDegree = new Map<string, number>(rps.map((rp) => [rp.id, 0]));
  const successors = new Map<string, string[]>(rps.map((rp) => [rp.id, []]));

  for (const [succ, preds] of predecessorMap) {
    for (const pred of preds) {
      inDegree.set(succ, (inDegree.get(succ) ?? 0) + 1);
      successors.get(pred)?.push(succ);
    }
  }

  const layers = new Map<string, number>();
  const queue = rps.filter((rp) => inDegree.get(rp.id) === 0).map((rp) => rp.id);
  for (const id of queue) layers.set(id, 0);

  while (queue.length) {
    const cur = queue.shift()!;
    for (const succ of successors.get(cur) ?? []) {
      const layer = Math.max(layers.get(succ) ?? 0, (layers.get(cur) ?? 0) + 1);
      layers.set(succ, layer);
      inDegree.set(succ, (inDegree.get(succ) ?? 1) - 1);
      if (inDegree.get(succ) === 0) queue.push(succ);
    }
  }

  // Group by layer and space evenly
  const byLayer = new Map<number, string[]>();
  for (const rp of rps) {
    const l = layers.get(rp.id) ?? 0;
    const arr = byLayer.get(l) ?? [];
    arr.push(rp.id);
    byLayer.set(l, arr);
  }

  const positions = new Map<string, { x: number; y: number }>();
  const X_SPACING = 180;
  const Y_SPACING = 120;

  for (const [layer, ids] of byLayer) {
    ids.forEach((id, idx) => {
      positions.set(id, {
        x: idx * X_SPACING - ((ids.length - 1) * X_SPACING) / 2 + 400,
        y: layer * Y_SPACING + 40,
      });
    });
  }

  return rps.map((rp) => positions.get(rp.id) ?? { x: 0, y: 0 });
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ReferencePointConfig() {
  const qc = useQueryClient();

  // For demo purposes we read the machine model ID from env; in production
  // this comes from the global machine order selector.
  const modelId = MACHINE_MODEL_ID;

  const { data: rps = [], isLoading } = useReferencePoints(modelId || undefined);
  const { data: precedences = [] }    = useReferencePointPrecedences(modelId || undefined);

  // ── Local predecessor map: rp_id → Set<predecessor_id> ───────────────────
  const [predecessorMap, setPredecessorMap] = useState<Map<string, Set<string>>>(new Map());

  // ── Highlighted node from DAG click ──────────────────────────────────────
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  // ── Cycle detection result ────────────────────────────────────────────────
  const cycleNodes = useMemo(() => {
    const adj = new Map<string, string[]>();
    for (const rp of rps) adj.set(rp.id, []);
    for (const [succ, preds] of predecessorMap) {
      for (const pred of preds) adj.get(pred)?.push(succ);
    }
    return detectCycle(adj);
  }, [predecessorMap, rps]);

  const hasCycle = cycleNodes.size > 0;

  // ── Sync precedences from API into local state ────────────────────────────
  useEffect(() => {
    const map = new Map<string, Set<string>>();
    for (const rp of rps) map.set(rp.id, new Set());
    for (const p of precedences) {
      map.get(p.reference_point_id)?.add(p.predecessor_reference_point_id);
    }
    setPredecessorMap(map);
  }, [rps, precedences]);

  // ── React Flow state ──────────────────────────────────────────────────────
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // Rebuild React Flow nodes/edges whenever RP list or precedences change
  useEffect(() => {
    if (!rps.length) return;

    const pmForLayout = new Map<string, string[]>();
    for (const [id, preds] of predecessorMap) pmForLayout.set(id, [...preds]);

    const positions = buildLayout(rps, pmForLayout);

    const nodes: Node[] = rps.map((rp, i) => ({
      id: rp.id,
      position: positions[i],
      data: { label: rp.code, rp },
      style: {
        background: LEVEL_COLORS[rp.target_level] ?? '#64748b',
        color: '#fff',
        border: cycleNodes.has(rp.id) ? '2px solid #ef4444' : '1px solid transparent',
        borderRadius: 8,
        padding: '6px 12px',
        fontWeight: highlightedId === rp.id ? 700 : 400,
        boxShadow: highlightedId === rp.id ? '0 0 0 3px #fbbf24' : 'none',
        fontSize: 12,
      },
    }));

    const edges: Edge[] = [];
    for (const [succId, preds] of predecessorMap) {
      for (const predId of preds) {
        edges.push({
          id: `${predId}-${succId}`,
          source: predId,
          target: succId,
          type: 'smoothstep',
          markerEnd: { type: 'arrowclosed' } as Edge['markerEnd'],
          animated: cycleNodes.has(predId) && cycleNodes.has(succId),
          style: { stroke: cycleNodes.has(predId) ? '#ef4444' : '#94a3b8' },
        });
      }
    }

    setRfNodes(nodes);
    setRfEdges(edges);
  }, [rps, predecessorMap, cycleNodes, highlightedId]);

  // ── Mutations ─────────────────────────────────────────────────────────────

  const saveRPMutation = useMutation({
    mutationFn: (rp: Partial<ReferencePoint> & { id: string }) =>
      apiClient.put(`/api/reference-points/${rp.id}`, rp),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reference-points', modelId] }),
  });

  const savePrecMutation = useMutation({
    mutationFn: () => {
      const precedencesList: { rp_id: string; predecessor_ids: string[] }[] = [];
      for (const [rpId, preds] of predecessorMap) {
        precedencesList.push({ rp_id: rpId, predecessor_ids: [...preds] });
      }
      return apiClient.put('/api/reference-points/precedences', {
        machine_model_id: modelId,
        precedences: precedencesList,
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['rp-precedences', modelId] }),
  });

  // ── Helpers ───────────────────────────────────────────────────────────────

  const addPredecessor = (rpId: string, predId: string) => {
    setPredecessorMap((m) => {
      const next = new Map(m);
      next.get(rpId)?.add(predId);
      return next;
    });
  };

  const removePredecessor = (rpId: string, predId: string) => {
    setPredecessorMap((m) => {
      const next = new Map(m);
      next.get(rpId)?.delete(predId);
      return next;
    });
  };

  const rootIds = useMemo(
    () => new Set(rps.filter((rp) => (predecessorMap.get(rp.id)?.size ?? 0) === 0).map((rp) => rp.id)),
    [rps, predecessorMap]
  );

  // ── Inline edit row ───────────────────────────────────────────────────────

  const [editId, setEditId]     = useState<string | null>(null);
  const [editFields, setEditFields] = useState<Partial<ReferencePoint>>({});

  function startEdit(rp: ReferencePoint) {
    setEditId(rp.id);
    setEditFields({ name: rp.name, target_level: rp.target_level, target_order_material: rp.target_order_material ?? '' });
  }

  function saveEdit(rp: ReferencePoint) {
    saveRPMutation.mutate({ ...rp, ...editFields });
    setEditId(null);
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Caricamento reference points…
      </div>
    );
  }

  if (!modelId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Configura <code className="mx-1 bg-muted px-1 rounded">VITE_DEFAULT_MACHINE_MODEL_ID</code> nel file .env per usare questa pagina.
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Left panel: tables ──────────────────────────────────────── */}
      <div className="w-1/2 flex flex-col border-r border-border overflow-hidden">
        {/* Cycle warning */}
        {hasCycle && (
          <div className="flex items-center gap-2 px-4 py-2 bg-red-50 border-b border-red-200 text-red-700 text-sm">
            <AlertTriangle size={14} />
            Ciclo rilevato! Correggi le precedenze prima di salvare.
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* ── Reference Points table ─────────────────────────────── */}
          <section>
            <h2 className="text-sm font-semibold mb-2">Reference Points</h2>
            <table className="w-full text-xs border border-border rounded overflow-hidden">
              <thead className="bg-muted">
                <tr>
                  {['Codice', 'Nome', 'Livello', 'Materiale', ''].map((h) => (
                    <th key={h} className="text-left px-2 py-1.5 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rps.map((rp) => (
                  <tr
                    key={rp.id}
                    className={`hover:bg-accent cursor-pointer ${highlightedId === rp.id ? 'bg-yellow-50 dark:bg-yellow-900/20' : ''}`}
                    onClick={() => setHighlightedId(rp.id === highlightedId ? null : rp.id)}
                  >
                    <td className="px-2 py-1.5">
                      <span className="font-mono">{rp.code}</span>
                      {rootIds.has(rp.id) && (
                        <span className="ml-1 text-[9px] bg-green-100 text-green-700 rounded px-1">ROOT</span>
                      )}
                    </td>

                    {editId === rp.id ? (
                      <>
                        <td className="px-2 py-1">
                          <input
                            className="border border-border rounded px-1 py-0.5 w-full bg-background"
                            value={editFields.name ?? ''}
                            onChange={(e) => setEditFields((f) => ({ ...f, name: e.target.value }))}
                          />
                        </td>
                        <td className="px-2 py-1">
                          <select
                            className="border border-border rounded px-1 py-0.5 bg-background"
                            value={editFields.target_level ?? ''}
                            onChange={(e) => setEditFields((f) => ({ ...f, target_level: e.target.value as ReferencePoint['target_level'] }))}
                          >
                            <option value="MACROAGGREGATE">MACROAGGREGATE</option>
                            <option value="AGGREGATE">AGGREGATE</option>
                          </select>
                        </td>
                        <td className="px-2 py-1">
                          <input
                            className="border border-border rounded px-1 py-0.5 w-full bg-background"
                            value={editFields.target_order_material ?? ''}
                            onChange={(e) => setEditFields((f) => ({ ...f, target_order_material: e.target.value }))}
                          />
                        </td>
                        <td className="px-2 py-1">
                          <button onClick={() => saveEdit(rp)} className="text-primary hover:underline mr-2">
                            <Save size={12} />
                          </button>
                          <button onClick={() => setEditId(null)} className="text-muted-foreground">
                            <X size={12} />
                          </button>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="px-2 py-1.5">{rp.name}</td>
                        <td className="px-2 py-1.5">
                          <span
                            className="rounded px-1 py-0.5 text-white text-[10px]"
                            style={{ backgroundColor: LEVEL_COLORS[rp.target_level] ?? '#94a3b8' }}
                          >
                            {rp.target_level}
                          </span>
                        </td>
                        <td className="px-2 py-1.5 font-mono">{rp.target_order_material ?? '—'}</td>
                        <td className="px-2 py-1.5">
                          <button
                            onClick={(e) => { e.stopPropagation(); startEdit(rp); }}
                            className="text-xs text-muted-foreground hover:text-foreground"
                          >
                            Edit
                          </button>
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* ── Precedences table ──────────────────────────────────── */}
          <section>
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-sm font-semibold">Precedenze</h2>
              <button
                onClick={() => !hasCycle && savePrecMutation.mutate()}
                disabled={hasCycle || savePrecMutation.isPending}
                className="flex items-center gap-1 text-xs bg-primary text-primary-foreground rounded px-2 py-1 disabled:opacity-50"
              >
                <Save size={12} />
                {savePrecMutation.isPending ? 'Salvo…' : 'Salva precedenze'}
              </button>
            </div>

            <div className="space-y-2">
              {rps.map((rp) => {
                const preds = predecessorMap.get(rp.id) ?? new Set<string>();
                const available = rps.filter((r) => r.id !== rp.id && !preds.has(r.id));

                return (
                  <div key={rp.id} className="border border-border rounded p-2">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="font-mono text-xs font-semibold">{rp.code}</span>
                      <span className="text-xs text-muted-foreground">{rp.name}</span>
                    </div>

                    {/* Predecessor chips */}
                    <div className="flex flex-wrap gap-1 mb-1.5">
                      {[...preds].map((predId) => {
                        const predRp = rps.find((r) => r.id === predId);
                        const inCycle = cycleNodes.has(predId) && cycleNodes.has(rp.id);
                        return (
                          <span
                            key={predId}
                            className={`flex items-center gap-1 text-[10px] rounded px-1.5 py-0.5 ${inCycle ? 'bg-red-100 text-red-700' : 'bg-muted text-muted-foreground'}`}
                          >
                            {predRp?.code ?? predId}
                            <button
                              onClick={() => removePredecessor(rp.id, predId)}
                              className="hover:text-foreground"
                            >
                              <X size={8} />
                            </button>
                          </span>
                        );
                      })}

                      {preds.size === 0 && (
                        <span className="text-[10px] text-green-600">ROOT (nessun predecessore)</span>
                      )}
                    </div>

                    {/* Add predecessor */}
                    {available.length > 0 && (
                      <select
                        defaultValue=""
                        onChange={(e) => {
                          if (e.target.value) {
                            addPredecessor(rp.id, e.target.value);
                            e.target.value = '';
                          }
                        }}
                        className="text-[10px] border border-border rounded px-1 py-0.5 bg-background"
                      >
                        <option value="">+ Aggiungi predecessore</option>
                        {available.map((r) => (
                          <option key={r.id} value={r.id}>{r.code} — {r.name}</option>
                        ))}
                      </select>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </div>

      {/* ── Right panel: React Flow DAG ─────────────────────────────── */}
      <div className="w-1/2 relative">
        {hasCycle && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 bg-red-100 border border-red-300 text-red-700 text-xs px-3 py-1.5 rounded-full shadow flex items-center gap-1.5">
            <AlertTriangle size={12} />
            Ciclo rilevato nel DAG — salvataggio bloccato
          </div>
        )}

        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={(_, node) => setHighlightedId(node.id === highlightedId ? null : node.id)}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          nodesDraggable
          nodesConnectable={false}
        >
          <Background gap={16} />
          <Controls />
          <MiniMap
            nodeColor={(n) => {
              const rp = rps.find((r) => r.id === n.id);
              return LEVEL_COLORS[rp?.target_level ?? ''] ?? '#94a3b8';
            }}
          />
        </ReactFlow>

        {/* Legend */}
        <div className="absolute bottom-8 right-4 bg-card border border-border rounded-lg p-2 text-xs space-y-1 shadow">
          {Object.entries(LEVEL_COLORS).map(([level, color]) => (
            <div key={level} className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded" style={{ background: color }} />
              {level}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

