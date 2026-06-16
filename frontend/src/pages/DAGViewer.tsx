/**
 * DAGViewer.tsx — Visualizzatore DAG completo con React Flow
 *
 * Mostra l'intera gerarchia degli ordini di produzione come grafo:
 * - Ogni nodo = un ProductionOrder con lista delle sue operazioni
 * - Archi BOM_PARENT = relazione gerarchia (grigio)
 * - Archi RP_PRECEDENCE = vincoli di precedenza RP (arancione)
 *
 * Colori per livello:
 *   MACHINE        → blu scuro
 *   MACROAGGREGATE → viola
 *   AGGREGATE      → teal
 *   GROUP          → verde
 *   COMPONENT      → grigio
 *
 * Colori per tipo operazione:
 *   ELECTRICAL → blu
 *   MECHANICAL → arancione
 *   GENERAL    → verde
 */

import { useEffect, useState, useCallback, useMemo } from 'react';
import DAGViewerEnhanced from '../components/dag/DAGViewerEnhanced';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { useQuery } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useMachineStore } from '../store/machineStore';

// ── Tipi API ─────────────────────────────────────────────────────────────────

interface DAGOperation {
  id: string;
  sap_operation_id: string | null;
  description: string | null;
  operation_type: 'ELECTRICAL' | 'MECHANICAL' | 'GENERAL';
  planned_duration_minutes: number;
  progress_pct: number;
  status: string;
  reference_point_id: string | null;
  reference_point_code: string | null;
  workcenter_id: string | null;
}

interface DAGOrder {
  id: string;
  sap_order_id: string;
  description: string | null;
  level: 'MACHINE' | 'MACROAGGREGATE' | 'AGGREGATE' | 'GROUP' | 'COMPONENT';
  material_code: string | null;
  progress_pct: number;
  status: string;
  parent_order_id: string | null;
  workcenter_id: string | null;
  operations: DAGOperation[];
}

interface DAGEdge {
  id: string;
  source: string;
  target: string;
  edge_type: 'BOM_PARENT' | 'RP_PRECEDENCE';
  rp_predecessor_code: string | null;
  rp_successor_code: string | null;
  label: string | null;
}

interface DAGFullResponse {
  machine_order_id: string;
  machine_description: string | null;
  orders: DAGOrder[];
  edges: DAGEdge[];
  reference_points: Record<string, { code: string; name: string; target_level: string }>;
}

// ── Costanti di stile ─────────────────────────────────────────────────────────

const LEVEL_COLORS: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  MACHINE:        { bg: '#1e3a5f', border: '#2563eb', text: '#e0f2fe', badge: '#1d4ed8' },
  MACROAGGREGATE: { bg: '#2d1b69', border: '#7c3aed', text: '#f3e8ff', badge: '#6d28d9' },
  AGGREGATE:      { bg: '#134e4a', border: '#0d9488', text: '#ccfbf1', badge: '#0f766e' },
  GROUP:          { bg: '#14532d', border: '#16a34a', text: '#dcfce7', badge: '#15803d' },
  COMPONENT:      { bg: '#1f2937', border: '#6b7280', text: '#e5e7eb', badge: '#4b5563' },
};

const OP_TYPE_COLORS: Record<string, string> = {
  ELECTRICAL: '#3b82f6',
  MECHANICAL: '#f97316',
  GENERAL:    '#22c55e',
};

const STATUS_COLORS: Record<string, string> = {
  PLANNED:     '#6b7280',
  IN_PROGRESS: '#f59e0b',
  COMPLETED:   '#22c55e',
  BLOCKED:     '#ef4444',
  MISSING:     '#dc2626',
  PENDING:     '#6b7280',
  INTERRUPTED: '#f97316',
};

// ── Nodo custom React Flow ────────────────────────────────────────────────────

interface OrderNodeData {
  order: DAGOrder;
  isSelected: boolean;
}

function OrderNode({ data }: NodeProps<OrderNodeData>) {
  const { order } = data;
  const colors = LEVEL_COLORS[order.level] || LEVEL_COLORS.COMPONENT;
  const statusColor = STATUS_COLORS[order.status] || '#6b7280';

  return (
    <div
      style={{
        background: colors.bg,
        border: `2px solid ${colors.border}`,
        borderRadius: '8px',
        minWidth: '240px',
        maxWidth: '280px',
        fontFamily: 'system-ui, sans-serif',
        boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: colors.border }} />

      {/* Header ordine */}
      <div
        style={{
          padding: '8px 12px',
          borderBottom: `1px solid ${colors.border}40`,
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}
      >
        <span
          style={{
            background: colors.badge,
            color: colors.text,
            fontSize: '9px',
            fontWeight: 700,
            padding: '2px 6px',
            borderRadius: '4px',
            letterSpacing: '0.05em',
            flexShrink: 0,
          }}
        >
          {order.level}
        </span>
        <span style={{ color: colors.text, fontSize: '11px', fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {order.description || order.sap_order_id}
        </span>
        <span
          style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: statusColor,
            flexShrink: 0,
          }}
          title={order.status}
        />
      </div>

      {/* Info ordine */}
      <div style={{ padding: '4px 12px 6px', color: colors.text, opacity: 0.7, fontSize: '10px' }}>
        {order.material_code && <span>{order.material_code} · </span>}
        <span>Progresso: {order.progress_pct.toFixed(0)}%</span>
      </div>

      {/* Barra progresso */}
      <div style={{ padding: '0 12px 6px' }}>
        <div style={{ height: '3px', background: '#ffffff20', borderRadius: '2px', overflow: 'hidden' }}>
          <div
            style={{
              height: '100%',
              width: `${order.progress_pct}%`,
              background: colors.border,
              borderRadius: '2px',
              transition: 'width 0.3s',
            }}
          />
        </div>
      </div>

      {/* Lista operazioni */}
      {order.operations.length > 0 && (
        <div style={{ borderTop: `1px solid ${colors.border}30`, padding: '6px 0' }}>
          {order.operations.map((op) => (
            <div
              key={op.id}
              style={{
                padding: '3px 12px',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                fontSize: '10px',
                color: colors.text,
                opacity: 0.85,
              }}
            >
              <span
                style={{
                  width: '6px',
                  height: '6px',
                  borderRadius: '50%',
                  background: OP_TYPE_COLORS[op.operation_type] || '#6b7280',
                  flexShrink: 0,
                }}
                title={op.operation_type}
              />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {op.description || op.sap_operation_id || 'Op. senza nome'}
              </span>
              <span style={{ opacity: 0.5, flexShrink: 0 }}>{op.planned_duration_minutes}m</span>
              {op.reference_point_code && (
                <span
                  style={{
                    background: '#f97316',
                    color: '#fff',
                    fontSize: '8px',
                    padding: '1px 4px',
                    borderRadius: '3px',
                    flexShrink: 0,
                  }}
                  title={`Reference Point: ${op.reference_point_code}`}
                >
                  {op.reference_point_code}
                </span>
              )}
              <span
                style={{
                  width: '5px',
                  height: '5px',
                  borderRadius: '50%',
                  background: STATUS_COLORS[op.status] || '#6b7280',
                  flexShrink: 0,
                }}
                title={op.status}
              />
            </div>
          ))}
        </div>
      )}

      {order.operations.length === 0 && (
        <div style={{ padding: '4px 12px 8px', fontSize: '10px', color: colors.text, opacity: 0.4, fontStyle: 'italic' }}>
          Nessuna operazione
        </div>
      )}

      <Handle type="source" position={Position.Bottom} style={{ background: colors.border }} />
    </div>
  );
}

const nodeTypes = { orderNode: OrderNode };

// ── Layout automatico top-down ────────────────────────────────────────────────

const LEVEL_Y: Record<string, number> = {
  MACHINE: 0,
  MACROAGGREGATE: 280,
  AGGREGATE: 560,
  GROUP: 840,
  COMPONENT: 1120,
};

function computeLayout(orders: DAGOrder[]): Record<string, { x: number; y: number }> {
  const byLevel: Record<string, DAGOrder[]> = {};
  for (const o of orders) {
    byLevel[o.level] = byLevel[o.level] || [];
    byLevel[o.level].push(o);
  }

  const positions: Record<string, { x: number; y: number }> = {};
  for (const [level, lvlOrders] of Object.entries(byLevel)) {
    const y = LEVEL_Y[level] ?? 0;
    const totalWidth = lvlOrders.length * 300;
    lvlOrders.forEach((o, i) => {
      positions[o.id] = {
        x: i * 300 - totalWidth / 2 + 150,
        y,
      };
    });
  }
  return positions;
}

// ── Hook dati ─────────────────────────────────────────────────────────────────

function useDAGFull(machineOrderId: string | null) {
  return useQuery<DAGFullResponse>({
    queryKey: ['dag-full', machineOrderId],
    queryFn: async () => {
      const { data } = await apiClient.get<DAGFullResponse>(
        `/api/dag/machine/${machineOrderId}/full`
      );
      return data;
    },
    enabled: !!machineOrderId,
    staleTime: 30_000,
  });
}

// ── Legenda ───────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 80,
        left: 16,
        background: '#111827',
        border: '1px solid #374151',
        borderRadius: '8px',
        padding: '12px 16px',
        zIndex: 10,
        fontSize: '11px',
        color: '#d1d5db',
        minWidth: '200px',
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: '8px', color: '#f9fafb' }}>Legenda</div>

      <div style={{ marginBottom: '6px', fontWeight: 600, color: '#9ca3af', fontSize: '10px' }}>
        LIVELLO ORDINE
      </div>
      {Object.entries(LEVEL_COLORS).map(([level, c]) => (
        <div key={level} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '2px', background: c.border }} />
          <span>{level}</span>
        </div>
      ))}

      <div style={{ marginTop: '8px', marginBottom: '6px', fontWeight: 600, color: '#9ca3af', fontSize: '10px' }}>
        TIPO OPERAZIONE (punto colorato)
      </div>
      {Object.entries(OP_TYPE_COLORS).map(([type, color]) => (
        <div key={type} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
          <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: color }} />
          <span>{type}</span>
        </div>
      ))}

      <div style={{ marginTop: '8px', marginBottom: '6px', fontWeight: 600, color: '#9ca3af', fontSize: '10px' }}>
        TIPO ARCO
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
        <div style={{ width: '20px', height: '2px', background: '#6b7280' }} />
        <span>Gerarchia BOM</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <div style={{ width: '20px', height: '2px', background: '#f97316' }} />
        <span>Precedenza RP</span>
      </div>
    </div>
  );
}

// ── Pannello filtri ───────────────────────────────────────────────────────────

interface FilterState {
  showBOM: boolean;
  showRP: boolean;
  levels: Set<string>;
  onlyWithOps: boolean;
}

function FilterPanel({ filters, onChange }: { filters: FilterState; onChange: (f: FilterState) => void }) {
  const allLevels = ['MACHINE', 'MACROAGGREGATE', 'AGGREGATE', 'GROUP', 'COMPONENT'];
  return (
    <div
      style={{
        position: 'absolute',
        top: 16,
        right: 16,
        background: '#111827',
        border: '1px solid #374151',
        borderRadius: '8px',
        padding: '12px 16px',
        zIndex: 10,
        fontSize: '11px',
        color: '#d1d5db',
        minWidth: '180px',
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: '8px', color: '#f9fafb' }}>Filtri</div>

      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px', cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={filters.showBOM}
          onChange={(e) => onChange({ ...filters, showBOM: e.target.checked })}
        />
        Archi BOM
      </label>
      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '8px', cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={filters.showRP}
          onChange={(e) => onChange({ ...filters, showRP: e.target.checked })}
        />
        Archi RP (precedenze)
      </label>

      <div style={{ marginBottom: '4px', fontWeight: 600, color: '#9ca3af', fontSize: '10px' }}>LIVELLI VISIBILI</div>
      {allLevels.map((level) => (
        <label key={level} style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={filters.levels.has(level)}
            onChange={(e) => {
              const next = new Set(filters.levels);
              if (e.target.checked) next.add(level);
              else next.delete(level);
              onChange({ ...filters, levels: next });
            }}
          />
          {level}
        </label>
      ))}

      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '8px', cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={filters.onlyWithOps}
          onChange={(e) => onChange({ ...filters, onlyWithOps: e.target.checked })}
        />
        Solo con operazioni
      </label>
    </div>
  );
}

// ── Componente principale ─────────────────────────────────────────────────────

export default function DAGViewer() {
  const { selectedMachineOrderId } = useMachineStore();
  const { data, isLoading, error } = useDAGFull(selectedMachineOrderId);

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  const [dagViewMode, setDagViewMode] = useState<'BOM' | 'RP'>('RP');
  const [filters, setFilters] = useState<FilterState>({
    showBOM: true,
    showRP: true,
    levels: new Set(['MACHINE', 'MACROAGGREGATE', 'AGGREGATE', 'GROUP']),
    onlyWithOps: false,
  });

  // Costruisci nodi e archi al cambio dati o filtri
  useEffect(() => {
    if (!data) return;

    const filteredOrders = data.orders.filter((o) => {
      if (!filters.levels.has(o.level)) return false;
      if (filters.onlyWithOps && o.operations.length === 0) return false;
      return true;
    });

    const filteredIds = new Set(filteredOrders.map((o) => o.id));
    const positions = computeLayout(filteredOrders);

    const newNodes: Node[] = filteredOrders.map((order) => ({
      id: order.id,
      type: 'orderNode',
      position: positions[order.id] || { x: 0, y: 0 },
      data: { order, isSelected: order.id === selectedOrderId },
    }));

    const newEdges: Edge[] = [];
    for (const e of data.edges) {
      if (!filteredIds.has(e.source) || !filteredIds.has(e.target)) continue;
      if (e.edge_type === 'BOM_PARENT' && !filters.showBOM) continue;
      if (e.edge_type === 'RP_PRECEDENCE' && !filters.showRP) continue;

      const isBOM = e.edge_type === 'BOM_PARENT';
      newEdges.push({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.edge_type === 'RP_PRECEDENCE' ? e.label || '' : '',
        style: {
          stroke: isBOM ? '#6b7280' : '#f97316',
          strokeWidth: isBOM ? 1 : 2,
          strokeDasharray: isBOM ? '4 2' : undefined,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: isBOM ? '#6b7280' : '#f97316',
        },
        labelStyle: { fill: '#f97316', fontSize: 9, fontWeight: 600 },
        labelBgStyle: { fill: '#1a1a2e', opacity: 0.8 },
      });
    }

    setNodes(newNodes);
    setEdges(newEdges);
  }, [data, filters, selectedOrderId, setNodes, setEdges]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedOrderId((prev) => (prev === node.id ? null : node.id));
  }, []);

  const selectedOrder = useMemo(
    () => data?.orders.find((o) => o.id === selectedOrderId) || null,
    [data, selectedOrderId]
  );

  if (!selectedMachineOrderId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        Seleziona un machine order dal menu in alto per visualizzare il DAG.
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
        <div className="text-center">
          <div className="animate-spin w-8 h-8 border-2 border-primary border-t-transparent rounded-full mx-auto mb-3" />
          Caricamento DAG in corso…
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-destructive text-sm">
        Errore nel caricamento del DAG. Assicurarsi che il backend sia avviato e che l'endpoint /api/dag/machine/{'{id}'}/full esista.
      </div>
    );
  }

  const stats = data ? {
    totalOrders: data.orders.length,
    totalOps: data.orders.reduce((acc, o) => acc + o.operations.length, 0),
    totalEdges: data.edges.length,
    rpEdges: data.edges.filter((e) => e.edge_type === 'RP_PRECEDENCE').length,
  } : null;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: '#0f172a' }}>
      {/* Header */}
      <div style={{
        padding: '12px 20px',
        borderBottom: '1px solid #1e293b',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexShrink: 0,
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: '#f1f5f9' }}>
            DAG Produzione — {data?.machine_description || selectedMachineOrderId}
          </h1>
          {stats && (
            <p style={{ margin: '2px 0 0', fontSize: '11px', color: '#64748b' }}>
              {stats.totalOrders} ordini · {stats.totalOps} operazioni · {stats.totalEdges} archi ({stats.rpEdges} RP)
            </p>
          )}
        </div>

        {/* Badge stato */}
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {/* View toggle */}
          <div style={{ display: 'flex', borderRadius: '6px', overflow: 'hidden', border: '1px solid #374151', marginRight: '8px' }}>
            <button
              onClick={() => setDagViewMode('RP')}
              style={{
                padding: '4px 12px',
                fontSize: '11px',
                fontWeight: 600,
                background: dagViewMode === 'RP' ? '#4f46e5' : 'transparent',
                color: dagViewMode === 'RP' ? '#fff' : '#94a3b8',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              Priorità RP
            </button>
            <button
              onClick={() => setDagViewMode('BOM')}
              style={{
                padding: '4px 12px',
                fontSize: '11px',
                fontWeight: 600,
                background: dagViewMode === 'BOM' ? '#4f46e5' : 'transparent',
                color: dagViewMode === 'BOM' ? '#fff' : '#94a3b8',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              BOM Completo
            </button>
          </div>
          {Object.entries(STATUS_COLORS).slice(0, 4).map(([s, c]) => (
            <span key={s} style={{
              padding: '2px 8px',
              borderRadius: '12px',
              fontSize: '10px',
              fontWeight: 600,
              background: c + '30',
              color: c,
              border: `1px solid ${c}50`,
            }}>
              {s}
            </span>
          ))}
        </div>
      </div>

      {/* View principale */}
      {dagViewMode === 'RP' ? (
        <div style={{ flex: 1, overflow: 'auto', padding: '16px', background: '#0f172a' }}>
          <DAGViewerEnhanced
            machineOrderId={selectedMachineOrderId ?? ''}
            apiBase=""
          />
        </div>
      ) : (
      <div style={{ flex: 1, position: 'relative' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.1 }}
          style={{ background: '#0f172a' }}
          defaultEdgeOptions={{ animated: false }}
          minZoom={0.1}
          maxZoom={2}
        >
          <Background color="#1e293b" gap={20} />
          <Controls style={{ background: '#1e293b', border: '1px solid #374151' }} />
          <MiniMap
            style={{ background: '#111827', border: '1px solid #374151' }}
            nodeColor={(n) => {
              const order = (n.data as OrderNodeData).order;
              return LEVEL_COLORS[order.level]?.border || '#6b7280';
            }}
          />
        </ReactFlow>

        <Legend />
        <FilterPanel filters={filters} onChange={setFilters} />

        {/* Pannello dettaglio ordine selezionato */}
        {selectedOrder && (
          <div
            style={{
              position: 'absolute',
              bottom: 80,
              right: 220,
              background: '#111827',
              border: '1px solid #374151',
              borderRadius: '8px',
              padding: '14px 18px',
              zIndex: 10,
              fontSize: '11px',
              color: '#d1d5db',
              maxWidth: '340px',
              maxHeight: '400px',
              overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
              <span style={{ fontWeight: 700, color: '#f9fafb', fontSize: '12px' }}>
                Dettaglio: {selectedOrder.description || selectedOrder.sap_order_id}
              </span>
              <button
                onClick={() => setSelectedOrderId(null)}
                style={{ background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', fontSize: '14px' }}
              >
                ✕
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px', marginBottom: '10px' }}>
              <div style={{ color: '#6b7280' }}>Livello:</div>
              <div style={{ color: LEVEL_COLORS[selectedOrder.level]?.border || '#fff' }}>{selectedOrder.level}</div>
              <div style={{ color: '#6b7280' }}>SAP Order:</div>
              <div>{selectedOrder.sap_order_id}</div>
              <div style={{ color: '#6b7280' }}>Materiale:</div>
              <div>{selectedOrder.material_code || '—'}</div>
              <div style={{ color: '#6b7280' }}>Stato:</div>
              <div style={{ color: STATUS_COLORS[selectedOrder.status] || '#fff' }}>{selectedOrder.status}</div>
              <div style={{ color: '#6b7280' }}>Progresso:</div>
              <div>{selectedOrder.progress_pct.toFixed(1)}%</div>
            </div>

            <div style={{ fontWeight: 600, color: '#9ca3af', fontSize: '10px', marginBottom: '6px' }}>
              OPERAZIONI ({selectedOrder.operations.length})
            </div>
            {selectedOrder.operations.map((op) => (
              <div
                key={op.id}
                style={{
                  padding: '5px 8px',
                  background: '#1f2937',
                  borderRadius: '4px',
                  marginBottom: '4px',
                  borderLeft: `3px solid ${OP_TYPE_COLORS[op.operation_type] || '#6b7280'}`,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: '2px' }}>
                  {op.description || op.sap_operation_id || 'Op. senza descrizione'}
                </div>
                <div style={{ color: '#9ca3af', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  <span>{op.operation_type}</span>
                  <span>{op.planned_duration_minutes}min</span>
                  <span style={{ color: STATUS_COLORS[op.status] || '#fff' }}>{op.status}</span>
                  {op.reference_point_code && (
                    <span style={{ color: '#f97316' }}>RP: {op.reference_point_code}</span>
                  )}
                </div>
              </div>
            ))}
            {selectedOrder.operations.length === 0 && (
              <div style={{ color: '#6b7280', fontStyle: 'italic' }}>Nessuna operazione</div>
            )}
          </div>
        )}
      </div>
      )}
    </div>
  );
}