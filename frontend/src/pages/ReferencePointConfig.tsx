// frontend/src/pages/ReferencePointConfig.tsx
// FIX: Maximum update depth exceeded
//
// Causa: useEffect che chiama setRfNodes/setRfEdges aveva [cycleNodes, highlightedId]
// come dipendenze. cycleNodes è un Set (nuova reference ad ogni render) e
// highlightedId cambia lo stile dei nodi — entrambi causavano loop:
//   setRfNodes → onNodesChange → re-render → nuovo cycleNodes Set → effect → setRfNodes → ...
//
// Soluzione:
//   1. L'effect del layout usa SOLO [rps, predecessorMap] — non cycleNodes né highlightedId
//   2. cycleNodes e highlightedId aggiornano lo STILE dei nodi esistenti tramite
//      setRfNodes((prev) => prev.map(...)) in due effect separati con deps corrette
//   3. Gli edge del ciclo vengono aggiornati in un effect separato che dipende solo da
//      predecessorMap e cycleNodes (ma cycleNodes è stabilizzato con useRef)

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useReferencePoints, useReferencePointPrecedences } from '../api/hooks/useReferencePoints';
import type { ReferencePoint } from '../api/types';
import { X, Save, AlertTriangle } from 'lucide-react';

// ── Tipo dati nodo React Flow ─────────────────────────────────────────────────

interface RPNodeData {
  label: string;
  rp: ReferencePoint;
}

type RPNode = Node<RPNodeData>;

// ── Constants ─────────────────────────────────────────────────────────────────

const MACHINE_MODEL_ID = import.meta.env.VITE_DEFAULT_MACHINE_MODEL_ID ?? '';

const LEVEL_COLORS: Record<string, string> = {
  MACROAGGREGATE: '#7c3aed',
  AGGREGATE:      '#0d9488',
  GROUP:          '#15803d',
};

// ── DFS cycle detection ───────────────────────────────────────────────────────

function detectCycle(adjacency: Map<string, string[]>): Set<string> {
  const visited   = new Set<string>();
  const recStack  = new Set<string>();
  const cycleSet  = new Set<string>();

  function dfs(node: string): boolean {
    visited.add(node);
    recStack.add(node);
    for (const nb of adjacency.get(node) ?? []) {
      if (!visited.has(nb)) {
        if (dfs(nb)) { cycleSet.add(nb); return true; }
      } else if (recStack.has(nb)) {
        cycleSet.add(node); cycleSet.add(nb); return true;
      }
    }
    recStack.delete(node);
    return false;
  }
  for (const n of adjacency.keys()) { if (!visited.has(n)) dfs(n); }
  return cycleSet;
}

// ── Layout (top-down by topological layer) ────────────────────────────────────

function buildLayout(
  rps: ReferencePoint[],
  predecessorMap: Map<string, string[]>,
): { x: number; y: number }[] {
  const inDegree   = new Map<string, number>(rps.map((r) => [r.id, 0]));
  const successors = new Map<string, string[]>(rps.map((r) => [r.id, []]));

  for (const [succ, preds] of predecessorMap) {
    for (const pred of preds) {
      inDegree.set(succ, (inDegree.get(succ) ?? 0) + 1);
      successors.get(pred)?.push(succ);
    }
  }

  const layers = new Map<string, number>();
  const queue  = rps.filter((r) => inDegree.get(r.id) === 0).map((r) => r.id);
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

  const byLayer = new Map<number, string[]>();
  for (const [id, layer] of layers) {
    const arr = byLayer.get(layer) ?? [];
    arr.push(id);
    byLayer.set(layer, arr);
  }

  const NODE_W = 160; const NODE_H = 50; const GAP_X = 40; const GAP_Y = 80;
  const posMap = new Map<string, { x: number; y: number }>();
  for (const [layer, ids] of byLayer) {
    ids.forEach((id, i) => {
      posMap.set(id, { x: i * (NODE_W + GAP_X), y: layer * (NODE_H + GAP_Y) });
    });
  }
  return rps.map((r) => posMap.get(r.id) ?? { x: 0, y: 0 });
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ReferencePointConfig() {
  const qc      = useQueryClient();
  const modelId = MACHINE_MODEL_ID;

  const { data: rps = [], isLoading } = useReferencePoints(modelId || undefined);
  const { data: precedences = [] }    = useReferencePointPrecedences(modelId || undefined);

  // ── Local predecessor map ─────────────────────────────────────────────────
  const [predecessorMap, setPredecessorMap] = useState<Map<string, Set<string>>>(new Map());

  // ── Highlighted node ──────────────────────────────────────────────────────
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  // ── Cycle detection (stabilizzato con ref per evitare loop effect) ────────
  const cycleNodes = useMemo<Set<string>>(() => {
    const adj = new Map<string, string[]>();
    for (const rp of rps) adj.set(rp.id, []);
    for (const [succ, preds] of predecessorMap) {
      for (const pred of preds) adj.get(pred)?.push(succ);
    }
    return detectCycle(adj);
  }, [predecessorMap, rps]);

  // Ref stabile per cycleNodes — usato negli effect per non causare loop
  const cycleNodesRef = useRef<Set<string>>(cycleNodes);
  useEffect(() => { cycleNodesRef.current = cycleNodes; }, [cycleNodes]);

  const hasCycle = cycleNodes.size > 0;

  // ── Sync API → local state (solo quando rps/precedences cambiano dall'API) ─
  useEffect(() => {
    const map = new Map<string, Set<string>>();
    for (const rp of rps) map.set(rp.id, new Set());
    for (const p of precedences) {
      map.get(p.reference_point_id)?.add(p.predecessor_reference_point_id);
    }
    setPredecessorMap(map);
  }, [rps, precedences]);   // ← SOLO dati API, non cycleNodes/highlightedId

  // ── React Flow state ──────────────────────────────────────────────────────
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<RPNodeData>([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // EFFECT 1: rebuild layout (posizioni) — solo quando rps o predecessorMap cambiano
  // NON dipende da cycleNodes né highlightedId
  useEffect(() => {
    if (!rps.length) return;

    const pmForLayout = new Map<string, string[]>();
    for (const [id, preds] of predecessorMap) pmForLayout.set(id, [...preds]);
    const positions = buildLayout(rps, pmForLayout);

    // Legge cycleNodes e highlightedId dalla ref/snapshot corrente
    const cn = cycleNodesRef.current;
    const hi = highlightedId;   // closure snapshot — OK perché questo effect non è triggato da highlightedId

    const nodes: RPNode[] = rps.map((rp, i) => ({
      id: rp.id,
      position: positions[i],
      data: { label: `${rp.code}\n${rp.name}`, rp },
      style: nodeStyle(rp, cn, hi),
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
          animated: cn.has(predId) && cn.has(succId),
          style: { stroke: cn.has(predId) ? '#ef4444' : '#94a3b8', strokeWidth: 1.5 },
        });
      }
    }

    setRfNodes(nodes);
    setRfEdges(edges);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rps, predecessorMap]);  // ← NON cycleNodes, NON highlightedId

  // EFFECT 2: aggiorna SOLO lo stile dei nodi esistenti quando cycleNodes cambia
  // Usa setRfNodes con updater function — non triggera onNodesChange in loop
  const prevCycleRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    // Confronta per contenuto per evitare aggiornamenti inutili
    const prev = prevCycleRef.current;
    const sameSize = prev.size === cycleNodes.size;
    const sameContent = sameSize && [...cycleNodes].every((id) => prev.has(id));
    if (sameContent) return;
    prevCycleRef.current = cycleNodes;

    setRfNodes((nds) =>
      nds.map((n) => ({
        ...n,
        style: nodeStyle((n.data as RPNodeData).rp, cycleNodes, highlightedId),
      }))
    );
    setRfEdges((eds) =>
      eds.map((e) => ({
        ...e,
        animated: cycleNodes.has(e.source) && cycleNodes.has(e.target),
        style: { stroke: cycleNodes.has(e.source) ? '#ef4444' : '#94a3b8', strokeWidth: 1.5 },
      }))
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cycleNodes]);   // ← solo cycleNodes, non highlightedId (evita loop)

  // EFFECT 3: aggiorna solo lo stile highlight — separato, deps minime
  useEffect(() => {
    setRfNodes((nds) =>
      nds.map((n) => ({
        ...n,
        style: nodeStyle((n.data as RPNodeData).rp, cycleNodesRef.current, highlightedId),
      }))
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightedId]);  // ← solo highlightedId

  // ── Helpers stile nodo ────────────────────────────────────────────────────
  function nodeStyle(
    rp: ReferencePoint,
    cn: Set<string>,
    hi: string | null,
  ): React.CSSProperties {
    return {
      background: LEVEL_COLORS[rp.target_level] ?? '#64748b',
      color: '#fff',
      border: cn.has(rp.id) ? '2px solid #ef4444' : hi === rp.id ? '2px solid #fbbf24' : '1px solid transparent',
      borderRadius: 8,
      padding: '6px 12px',
      fontWeight: hi === rp.id ? 700 : 400,
      boxShadow: hi === rp.id ? '0 0 0 3px #fbbf2466' : 'none',
      fontSize: 11,
      whiteSpace: 'pre-line' as const,
    };
  }

  // ── Mutations ─────────────────────────────────────────────────────────────
  const saveRPMutation = useMutation({
    mutationFn: (rp: Partial<ReferencePoint> & { id: string }) =>
      apiClient.put(`/api/reference-points/${rp.id}`, rp),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reference-points', modelId] }),
  });

  const savePrecMutation = useMutation({
    mutationFn: () => {
      const list: { rp_id: string; predecessor_ids: string[] }[] = [];
      for (const [rpId, preds] of predecessorMap) {
        list.push({ rp_id: rpId, predecessor_ids: [...preds] });
      }
      return apiClient.put('/api/reference-points/precedences', {
        machine_model_id: modelId,
        precedences: list,
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['rp-precedences', modelId] }),
  });

  // ── Predecessor helpers ───────────────────────────────────────────────────
  const addPredecessor = useCallback((rpId: string, predId: string) => {
    setPredecessorMap((m) => {
      const next = new Map(m);
      const s = new Set(next.get(rpId) ?? []);
      s.add(predId);
      next.set(rpId, s);
      return next;
    });
  }, []);

  const removePredecessor = useCallback((rpId: string, predId: string) => {
    setPredecessorMap((m) => {
      const next = new Map(m);
      const s = new Set(next.get(rpId) ?? []);
      s.delete(predId);
      next.set(rpId, s);
      return next;
    });
  }, []);

  const rootIds = useMemo(
    () => new Set(rps.filter((rp) => (predecessorMap.get(rp.id)?.size ?? 0) === 0).map((r) => r.id)),
    [rps, predecessorMap]
  );

  // ── Inline edit ───────────────────────────────────────────────────────────
  const [editId, setEditId]         = useState<string | null>(null);
  const [editFields, setEditFields] = useState<Partial<ReferencePoint>>({});

  function startEdit(rp: ReferencePoint) {
    setEditId(rp.id);
    setEditFields({ name: rp.name, target_level: rp.target_level, target_order_material: rp.target_order_material ?? '' });
  }

  async function saveEdit(rp: ReferencePoint) {
    await saveRPMutation.mutateAsync({ id: rp.id, ...editFields });
    setEditId(null);
  }

  // ── Render ────────────────────────────────────────────────────────────────
  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-sm text-muted-foreground">Caricamento Reference Points…</div>;
  }

  if (!modelId) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        Configura <code className="mx-1 bg-muted px-1 rounded">VITE_DEFAULT_MACHINE_MODEL_ID</code> nel file <code>.env</code>.
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Left panel ──────────────────────────────────────────────────── */}
      <div className="w-1/2 flex flex-col border-r border-border overflow-y-auto">
        <div className="px-4 py-3 border-b border-border bg-card shrink-0">
          <h1 className="text-sm font-bold">Reference Point Config</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            {rps.length} RP · {[...predecessorMap.values()].reduce((a, s) => a + s.size, 0)} archi
          </p>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* Tabella RP */}
          <section>
            <h2 className="text-xs font-semibold uppercase text-muted-foreground mb-2">Reference Points</h2>
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="bg-muted text-left">
                  <th className="px-2 py-1.5 font-medium">Codice</th>
                  <th className="px-2 py-1.5 font-medium">Nome</th>
                  <th className="px-2 py-1.5 font-medium">Livello</th>
                  <th className="px-2 py-1.5 font-medium">Materiale</th>
                  <th className="px-2 py-1.5 font-medium w-16">Azioni</th>
                </tr>
              </thead>
              <tbody>
                {rps.map((rp) => (
                  <tr
                    key={rp.id}
                    className={`border-b border-border cursor-pointer hover:bg-accent/40 ${highlightedId === rp.id ? 'bg-yellow-50 dark:bg-yellow-900/20' : ''}`}
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
                          <input className="border border-border rounded px-1 py-0.5 w-full bg-background text-xs"
                            value={editFields.name ?? ''}
                            onChange={(e) => setEditFields((f) => ({ ...f, name: e.target.value }))} />
                        </td>
                        <td className="px-2 py-1">
                          <select className="border border-border rounded px-1 py-0.5 bg-background text-xs"
                            value={editFields.target_level ?? ''}
                            onChange={(e) => setEditFields((f) => ({ ...f, target_level: e.target.value as ReferencePoint['target_level'] }))}>
                            <option value="MACROAGGREGATE">MACROAGGREGATE</option>
                            <option value="AGGREGATE">AGGREGATE</option>
                            <option value="GROUP">GROUP</option>
                          </select>
                        </td>
                        <td className="px-2 py-1">
                          <input className="border border-border rounded px-1 py-0.5 w-full bg-background text-xs"
                            value={editFields.target_order_material ?? ''}
                            onChange={(e) => setEditFields((f) => ({ ...f, target_order_material: e.target.value }))} />
                        </td>
                        <td className="px-2 py-1 flex gap-1">
                          <button onClick={() => saveEdit(rp)} className="text-primary hover:underline"><Save size={12} /></button>
                          <button onClick={() => setEditId(null)} className="text-muted-foreground"><X size={12} /></button>
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="px-2 py-1.5">{rp.name}</td>
                        <td className="px-2 py-1.5">
                          <span className="rounded px-1 py-0.5 text-white text-[10px]"
                            style={{ backgroundColor: LEVEL_COLORS[rp.target_level] ?? '#64748b' }}>
                            {rp.target_level}
                          </span>
                        </td>
                        <td className="px-2 py-1.5 font-mono text-muted-foreground">{rp.target_order_material ?? '—'}</td>
                        <td className="px-2 py-1.5">
                          <button onClick={(e) => { e.stopPropagation(); startEdit(rp); }} className="text-primary hover:underline text-[10px]">Modifica</button>
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* Tabella precedenze */}
          <section>
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-xs font-semibold uppercase text-muted-foreground">Precedenze</h2>
              <button
                onClick={() => !hasCycle && savePrecMutation.mutate()}
                disabled={hasCycle || savePrecMutation.isPending}
                className="text-[10px] bg-primary text-primary-foreground rounded px-2 py-1 disabled:opacity-50"
              >
                {savePrecMutation.isPending ? 'Salvataggio…' : 'Salva precedenze'}
              </button>
            </div>

            {hasCycle && (
              <div className="flex items-center gap-1.5 text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2 mb-2">
                <AlertTriangle size={12} /> Ciclo rilevato — salvataggio bloccato
              </div>
            )}

            <div className="space-y-2">
              {rps.map((rp) => {
                const preds    = predecessorMap.get(rp.id) ?? new Set<string>();
                const available = rps.filter((r) => r.id !== rp.id && !preds.has(r.id));
                return (
                  <div key={rp.id} className="border border-border rounded p-2">
                    <div className="text-[10px] font-mono font-semibold text-muted-foreground mb-1">{rp.code} — {rp.name}</div>
                    <div className="flex flex-wrap gap-1 mb-1">
                      {[...preds].map((predId) => {
                        const predRp = rps.find((r) => r.id === predId);
                        return (
                          <span key={predId}
                            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] ${cycleNodes.has(predId) ? 'bg-red-100 text-red-700' : 'bg-muted text-muted-foreground'}`}>
                            {predRp?.code ?? predId.slice(0, 8)}
                            <button onClick={() => removePredecessor(rp.id, predId)} className="hover:text-foreground"><X size={8} /></button>
                          </span>
                        );
                      })}
                      {preds.size === 0 && <span className="text-[10px] text-green-600">ROOT</span>}
                    </div>
                    {available.length > 0 && (
                      <select defaultValue=""
                        onChange={(e) => { if (e.target.value) { addPredecessor(rp.id, e.target.value); e.target.value = ''; } }}
                        className="text-[10px] border border-border rounded px-1 py-0.5 bg-background">
                        <option value="">+ Aggiungi predecessore</option>
                        {available.map((r) => <option key={r.id} value={r.id}>{r.code} — {r.name}</option>)}
                      </select>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </div>
      </div>

      {/* ── Right panel: React Flow DAG ─────────────────────────────────── */}
      <div className="w-1/2 relative">
        {hasCycle && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 bg-red-100 border border-red-300 text-red-700 text-xs px-3 py-1.5 rounded-full shadow flex items-center gap-1.5">
            <AlertTriangle size={12} /> Ciclo rilevato nel DAG — salvataggio bloccato
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
          <MiniMap nodeColor={(n) => {
            const rp = rps.find((r) => r.id === n.id);
            return LEVEL_COLORS[rp?.target_level ?? ''] ?? '#94a3b8';
          }} />
        </ReactFlow>
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