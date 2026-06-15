/**
 * DBAdmin.tsx — Esploratore dati completo del database
 *
 * Permette di consultare tutte le tabelle chiave con:
 * - Dati denormalizzati (operazioni con ordine + workcenter + RP associato)
 * - Reference Point con le operazioni e gli ordini collegati
 * - Ricerca e filtri per ogni tabella
 * - Paginazione
 * - Esportazione CSV
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useMachineStore } from '../store/machineStore';
import { Database, Search, Download, RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react';

// ── Tipi ─────────────────────────────────────────────────────────────────────

interface ProductionOrder {
  id: string;
  sap_order_id: string;
  description: string | null;
  level: string;
  material_code: string | null;
  progress_pct: number;
  status: string;
  parent_order_id: string | null;
  workcenter_id: string | null;
}

interface OperationEnriched {
  operation_id: string;
  sap_operation_id: string | null;
  op_description: string | null;
  op_type: string;
  planned_duration_minutes: number;
  op_progress_pct: number;
  op_status: string;
  reference_point_id: string | null;
  reference_point_code: string | null;
  order_id: string;
  order_sap_id: string;
  order_description: string | null;
  order_level: string;
  order_material: string | null;
  order_status: string;
  workcenter_id: string | null;
}

interface Operator {
  id: string;
  employee_id: string;
  full_name: string;
  skill: string;
  workcenter_id: string;
  is_active: boolean;
}

interface ReferencePointFull {
  id: string;
  code: string;
  name: string;
  target_level: string;
  target_order_material: string | null;
  target_order_description: string | null;
  operations_linked: { op_id: string; op_desc: string | null; order_sap_id: string }[];
  predecessors: string[];
  successors: string[];
}

interface ScheduleScenario {
  id: string;
  name: string;
  objective_mode: string;
  is_active: boolean;
  created_at: string;
  machine_order_id: string;
}

interface DAGOperation {
  id: string;
  sap_operation_id: string | null;
  description: string | null;
  operation_type: 'ELECTRICAL' | 'MECHANICAL' | 'GENERAL';
  planned_duration_minutes: number;
  progress_pct: number;
  status: string;
  reference_point_id: string | null;
  reference_point_code?: string | null;
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
  reference_points: Record<string, {
    code?: string;
    name?: string;
    target_level?: string;
    target_order_material?: string | null;
  }>;
}

// ── Hooks dati ────────────────────────────────────────────────────────────────

function useProductionOrders(machineOrderId: string | null) {
  return useQuery<ProductionOrder[]>({
    queryKey: ['db-admin-orders', machineOrderId],
    queryFn: async () => {
      // Usa il BOM tree e lo appiattisce
      const { data } = await apiClient.get<{ id: string; [k: string]: unknown }[]>(
        `/api/orders/machine/${machineOrderId}/bom-tree`
      );
      // Appiattisce la struttura ad albero ricorsiva
      const flatten = (node: Record<string, unknown>): ProductionOrder[] => {
        const result: ProductionOrder[] = [{
          id: String(node.id || ''),
          sap_order_id: String(node.sap_order_id || ''),
          description: node.description as string | null,
          level: String(node.level || ''),
          material_code: node.material_code as string | null,
          progress_pct: Number(node.progress_pct || 0),
          status: String(node.status || ''),
          parent_order_id: node.parent_order_id as string | null,
          workcenter_id: node.workcenter_id as string | null,
        }];
        const children = (node.children as Record<string, unknown>[]) || [];
        for (const child of children) result.push(...flatten(child));
        return result;
      };
      return data.flatMap((n) => flatten(n as Record<string, unknown>));
    },
    enabled: !!machineOrderId,
    staleTime: 60_000,
  });
}

function useDAGFull(machineOrderId: string | null) {
  return useQuery<DAGFullResponse>({
    queryKey: ['dag-full', machineOrderId],
    queryFn: async () => {
      const { data } = await apiClient.get<DAGFullResponse>(`/api/dag/machine/${machineOrderId}/full`);
      return data;
    },
    enabled: !!machineOrderId,
    staleTime: 60_000,
  });
}

function useOperators() {
  return useQuery<Operator[]>({
    queryKey: ['db-admin-operators'],
    queryFn: async () => {
      const { data } = await apiClient.get<Operator[]>('/api/operators?page=1&size=200');
      return data;
    },
    staleTime: 120_000,
  });
}

function useScenarios() {
  return useQuery<ScheduleScenario[]>({
    queryKey: ['db-admin-scenarios'],
    queryFn: async () => {
      const { data } = await apiClient.get<ScheduleScenario[]>('/api/scenarios?page=1&size=50');
      return data;
    },
    staleTime: 30_000,
  });
}

// ── Componenti utilità ────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    PLANNED: 'bg-gray-700 text-gray-200',
    IN_PROGRESS: 'bg-amber-900 text-amber-200',
    COMPLETED: 'bg-green-900 text-green-200',
    BLOCKED: 'bg-red-900 text-red-200',
    MISSING: 'bg-red-950 text-red-300',
    PENDING: 'bg-gray-800 text-gray-300',
    INTERRUPTED: 'bg-orange-900 text-orange-200',
    SCHEDULED: 'bg-blue-900 text-blue-200',
  };
  return (
    <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold ${colors[status] || 'bg-gray-800 text-gray-300'}`}>
      {status}
    </span>
  );
}

function LevelBadge({ level }: { level: string }) {
  const colors: Record<string, string> = {
    MACHINE: 'bg-blue-900 text-blue-200',
    MACROAGGREGATE: 'bg-purple-900 text-purple-200',
    AGGREGATE: 'bg-teal-900 text-teal-200',
    GROUP: 'bg-green-900 text-green-200',
    COMPONENT: 'bg-gray-800 text-gray-300',
  };
  return (
    <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold ${colors[level] || 'bg-gray-800 text-gray-300'}`}>
      {level}
    </span>
  );
}

function exportCSV<T extends object>(data: T[], filename: string) {
  if (!data.length) return;
  const headers = Object.keys(data[0] as Record<string, unknown>);
  const rows = data.map((row) =>
    headers.map((h) => JSON.stringify((row as Record<string, unknown>)[h] ?? '')).join(',')
  );
  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function useSearch<T extends object, K extends keyof T>(items: T[], searchFields: K[]) {
  const [query, setQuery] = useState('');
  const filtered = query.trim()
    ? items.filter((item) =>
        searchFields.some((f) => String((item as any)[f] ?? '').toLowerCase().includes(query.toLowerCase()))
      )
    : items;
  return { query, setQuery, filtered };
}

function Pagination({ page, total, pageSize, onPage }: {
  page: number; total: number; pageSize: number; onPage: (p: number) => void;
}) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground mt-2">
      <button
        onClick={() => onPage(page - 1)}
        disabled={page === 1}
        className="p-1 disabled:opacity-40 hover:text-foreground"
      >
        <ChevronLeft size={14} />
      </button>
      <span>Pagina {page} di {totalPages} ({total} righe)</span>
      <button
        onClick={() => onPage(page + 1)}
        disabled={page === totalPages}
        className="p-1 disabled:opacity-40 hover:text-foreground"
      >
        <ChevronRight size={14} />
      </button>
    </div>
  );
}

// ── Tab: Ordini di Produzione ──────────────────────────────────────────────────

function OrdersTab({ machineOrderId }: { machineOrderId: string }) {
  const { data: orders = [], isLoading, refetch } = useProductionOrders(machineOrderId);
  const { query, setQuery, filtered } = useSearch<ProductionOrder, keyof ProductionOrder>(orders, ['sap_order_id', 'description', 'material_code', 'level', 'status']);
  const [page, setPage] = useState(1);
  const PAGE = 20;
  const paged = filtered.slice((page - 1) * PAGE, page * PAGE);

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            placeholder="Cerca per SAP order, descrizione, materiale…"
            className="w-full pl-7 pr-3 py-1.5 text-xs bg-background border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <button onClick={() => refetch()} className="p-1.5 hover:bg-accent rounded" title="Aggiorna">
          <RefreshCw size={13} />
        </button>
        <button
          onClick={() => exportCSV(filtered, 'production_orders.csv')}
          className="p-1.5 hover:bg-accent rounded"
          title="Esporta CSV"
        >
          <Download size={13} />
        </button>
      </div>

      {isLoading ? (
        <div className="text-xs text-muted-foreground">Caricamento…</div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-1.5 pr-3 font-medium">SAP Order</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Descrizione</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Livello</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Materiale</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Stato</th>
                  <th className="text-right py-1.5 font-medium">Progresso</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((o) => (
                  <tr key={o.id} className="border-b border-border/50 hover:bg-accent/30">
                    <td className="py-1.5 pr-3 font-mono text-[10px]">{o.sap_order_id}</td>
                    <td className="py-1.5 pr-3 max-w-[200px] truncate">{o.description || '—'}</td>
                    <td className="py-1.5 pr-3"><LevelBadge level={o.level} /></td>
                    <td className="py-1.5 pr-3 font-mono text-[10px]">{o.material_code || '—'}</td>
                    <td className="py-1.5 pr-3"><StatusBadge status={o.status} /></td>
                    <td className="py-1.5 text-right">
                      <div className="inline-flex items-center gap-1.5">
                        <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
                          <div className="h-full bg-primary rounded-full" style={{ width: `${o.progress_pct}%` }} />
                        </div>
                        <span className="text-[10px] text-muted-foreground">{o.progress_pct.toFixed(0)}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} total={filtered.length} pageSize={PAGE} onPage={setPage} />
        </>
      )}
    </div>
  );
}

// ── Tab: Operazioni (denormalizzate con ordine + RP) ─────────────────────────

function OperationsTab({ machineOrderId }: { machineOrderId: string }) {
  const { data: dagData, isLoading } = useDAGFull(machineOrderId);
  const [query, setQuery] = useState('');
  const [filterType, setFilterType] = useState('');
  const [page, setPage] = useState(1);
  const PAGE = 25;

  // Appiattisce le operazioni dal DAG con info dell'ordine padre
  const enrichedOps: OperationEnriched[] = (dagData?.orders || []).flatMap((order: DAGOrder) =>
    (order.operations || []).map((op: DAGOperation) => ({
      operation_id: String(op.id),
      sap_operation_id: op.sap_operation_id,
      op_description: op.description,
      op_type: op.operation_type,
      planned_duration_minutes: op.planned_duration_minutes,
      op_progress_pct: op.progress_pct,
      op_status: op.status,
      reference_point_id: op.reference_point_id,
      reference_point_code: op.reference_point_code ?? null,
      order_id: order.id,
      order_sap_id: order.sap_order_id,
      order_description: order.description,
      order_level: order.level,
      order_material: order.material_code,
      order_status: order.status,
      workcenter_id: op.workcenter_id,
    }))
  );

  const filtered = enrichedOps.filter((op) => {
    if (filterType && op.op_type !== filterType) return false;
    if (query) {
      const q = query.toLowerCase();
      return [op.op_description, op.order_sap_id, op.order_description, op.reference_point_code, op.order_material]
        .some((f) => f && f.toLowerCase().includes(q));
    }
    return true;
  });

  const paged = filtered.slice((page - 1) * PAGE, page * PAGE);

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            placeholder="Cerca per descrizione, SAP order, materiale, RP…"
            className="w-full pl-7 pr-3 py-1.5 text-xs bg-background border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <select
          value={filterType}
          onChange={(e) => { setFilterType(e.target.value); setPage(1); }}
          className="text-xs border border-border rounded px-2 py-1.5 bg-background"
        >
          <option value="">Tutti i tipi</option>
          <option value="ELECTRICAL">ELECTRICAL</option>
          <option value="MECHANICAL">MECHANICAL</option>
          <option value="GENERAL">GENERAL</option>
        </select>
        <button
          onClick={() => exportCSV(filtered as unknown as Record<string, unknown>[], 'operations.csv')}
          className="p-1.5 hover:bg-accent rounded"
          title="Esporta CSV"
        >
          <Download size={13} />
        </button>
      </div>

      {isLoading ? (
        <div className="text-xs text-muted-foreground">Caricamento DAG…</div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-1.5 pr-3 font-medium">Descrizione Operazione</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Tipo</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Ordine Padre</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Livello</th>
                  <th className="text-left py-1.5 pr-3 font-medium">RP Associato</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Stato Op.</th>
                  <th className="text-right py-1.5 font-medium">Durata</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((op) => (
                  <tr key={op.operation_id} className="border-b border-border/50 hover:bg-accent/30">
                    <td className="py-1.5 pr-3">
                      <div>{op.op_description || <span className="text-muted-foreground italic">senza nome</span>}</div>
                      {op.sap_operation_id && (
                        <div className="font-mono text-[9px] text-muted-foreground">{op.sap_operation_id}</div>
                      )}
                    </td>
                    <td className="py-1.5 pr-3">
                      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                        op.op_type === 'ELECTRICAL' ? 'bg-blue-900/60 text-blue-300' :
                        op.op_type === 'MECHANICAL' ? 'bg-orange-900/60 text-orange-300' :
                        'bg-green-900/60 text-green-300'
                      }`}>
                        {op.op_type}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3">
                      <div className="font-mono text-[10px]">{op.order_sap_id}</div>
                      <div className="text-muted-foreground text-[9px] max-w-[150px] truncate">{op.order_description}</div>
                    </td>
                    <td className="py-1.5 pr-3"><LevelBadge level={op.order_level} /></td>
                    <td className="py-1.5 pr-3">
                      {op.reference_point_code ? (
                        <span className="bg-orange-900/50 text-orange-300 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold">
                          {op.reference_point_code}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3"><StatusBadge status={op.op_status} /></td>
                    <td className="py-1.5 text-right text-muted-foreground">{op.planned_duration_minutes}m</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} total={filtered.length} pageSize={PAGE} onPage={setPage} />
        </>
      )}
    </div>
  );
}

// ── Tab: Reference Points (con operazioni e ordini collegati) ──────────────────

function ReferencePointsTab({ machineOrderId }: { machineOrderId: string }) {
  const { data: dagData, isLoading } = useDAGFull(machineOrderId);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  // Arricchisce i RP con le operazioni collegate (da operazioni che li referenziano)
  const referencePoints = dagData?.reference_points as Record<
    string,
    {
      code?: string;
      name?: string;
      target_level?: string;
      target_order_material?: string | null;
    }
  > | undefined;

  const rpFull: ReferencePointFull[] = Object.entries(referencePoints ?? {}).map(([rpId, rp]) => {
    const linkedOps: { op_id: string; op_desc: string | null; order_sap_id: string }[] = [];
    for (const order of dagData?.orders || []) {
      for (const op of order.operations || []) {
        if (String(op.reference_point_id || '') === rpId) {
          linkedOps.push({
            op_id: String(op.id),
            op_desc: op.description,
            order_sap_id: order.sap_order_id,
          });
        }
      }
    }

    // Predecessori/successori dagli archi RP nel DAG
    const rp_edges = dagData?.edges || [];
    const preds: string[] = [];
    const succs: string[] = [];
    // Trova l'ordine target di questo RP
    const targetMaterial = String(rp.target_order_material || '');
    const targetOrder = (dagData?.orders || []).find((o) => o.material_code === targetMaterial);
    if (targetOrder) {
      for (const edge of rp_edges.filter((e) => e.edge_type === 'RP_PRECEDENCE')) {
        if (edge.target === String(targetOrder.id)) preds.push(edge.rp_predecessor_code || '');
        if (edge.source === String(targetOrder.id)) succs.push(edge.rp_successor_code || '');
      }
    }

    return {
      id: rpId,
      code: String(rp.code || ''),
      name: String(rp.name || ''),
      target_level: String(rp.target_level || ''),
      target_order_material: rp.target_order_material as string | null,
      target_order_description: targetOrder ? String(targetOrder.description || '') : null,
      operations_linked: linkedOps,
      predecessors: preds.filter(Boolean),
      successors: succs.filter(Boolean),
    };
  });

  const filtered = query
    ? rpFull.filter((rp) => [rp.code, rp.name, rp.target_order_material, rp.target_order_description]
        .some((f) => f && f.toLowerCase().includes(query.toLowerCase())))
    : rpFull;

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Cerca per codice RP, nome, materiale target…"
            className="w-full pl-7 pr-3 py-1.5 text-xs bg-background border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <span className="text-xs text-muted-foreground">{rpFull.length} RP totali</span>
      </div>

      {isLoading ? (
        <div className="text-xs text-muted-foreground">Caricamento…</div>
      ) : (
        <div className="space-y-1">
          {filtered.map((rp) => (
            <div key={rp.id} className="border border-border rounded overflow-hidden">
              <button
                onClick={() => setExpandedId(expandedId === rp.id ? null : rp.id)}
                className="w-full flex items-center gap-3 px-3 py-2 hover:bg-accent/30 text-left"
              >
                <span className="font-mono font-semibold text-orange-400 text-xs w-28 shrink-0">{rp.code}</span>
                <span className="text-xs flex-1">{rp.name}</span>
                <LevelBadge level={rp.target_level} />
                <span className="text-[10px] text-muted-foreground">
                  {rp.operations_linked.length} op.
                </span>
                <span className="text-muted-foreground text-xs">{expandedId === rp.id ? '▲' : '▼'}</span>
              </button>

              {expandedId === rp.id && (
                <div className="px-3 pb-3 pt-1 bg-muted/20 text-xs space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-muted-foreground">Materiale target: </span>
                      <span className="font-mono">{rp.target_order_material || '—'}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Ordine target: </span>
                      <span>{rp.target_order_description || '—'}</span>
                    </div>
                  </div>

                  {rp.predecessors.length > 0 && (
                    <div>
                      <span className="text-muted-foreground font-medium">Predecessori RP: </span>
                      {rp.predecessors.map((p) => (
                        <span key={p} className="inline-block mr-1 bg-orange-900/40 text-orange-300 px-1.5 py-0.5 rounded text-[10px] font-mono">
                          {p}
                        </span>
                      ))}
                    </div>
                  )}
                  {rp.successors.length > 0 && (
                    <div>
                      <span className="text-muted-foreground font-medium">Successori RP: </span>
                      {rp.successors.map((s) => (
                        <span key={s} className="inline-block mr-1 bg-blue-900/40 text-blue-300 px-1.5 py-0.5 rounded text-[10px] font-mono">
                          {s}
                        </span>
                      ))}
                    </div>
                  )}

                  {rp.operations_linked.length > 0 ? (
                    <div>
                      <div className="text-muted-foreground font-medium mb-1">Operazioni che referenziano questo RP:</div>
                      <div className="space-y-1">
                        {rp.operations_linked.map((op) => (
                          <div key={op.op_id} className="flex items-center gap-2 px-2 py-1 bg-background rounded">
                            <span className="font-mono text-[10px] text-muted-foreground">{op.order_sap_id}</span>
                            <span className="text-[11px]">{op.op_desc || 'Operazione senza descrizione'}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="text-muted-foreground italic">Nessuna operazione collega questo RP.</div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Tab: Operatori ────────────────────────────────────────────────────────────

function OperatorsTab() {
  const { data: operators = [], isLoading, refetch } = useOperators();
  const { query, setQuery, filtered } = useSearch<Operator, keyof Operator>(operators, ['full_name', 'employee_id', 'skill']);

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <div className="relative flex-1">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Cerca per nome, employee ID, skill…"
            className="w-full pl-7 pr-3 py-1.5 text-xs bg-background border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
        <button onClick={() => refetch()} className="p-1.5 hover:bg-accent rounded"><RefreshCw size={13} /></button>
        <button onClick={() => exportCSV(filtered, 'operators.csv')} className="p-1.5 hover:bg-accent rounded"><Download size={13} /></button>
      </div>

      {isLoading ? (
        <div className="text-xs text-muted-foreground">Caricamento…</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left py-1.5 pr-3 font-medium">Nome</th>
                <th className="text-left py-1.5 pr-3 font-medium">Employee ID</th>
                <th className="text-left py-1.5 pr-3 font-medium">Skill</th>
                <th className="text-left py-1.5 pr-3 font-medium">Workcenter</th>
                <th className="text-left py-1.5 font-medium">Attivo</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((op) => (
                <tr key={op.id} className="border-b border-border/50 hover:bg-accent/30">
                  <td className="py-1.5 pr-3 font-medium">{op.full_name}</td>
                  <td className="py-1.5 pr-3 font-mono text-[10px] text-muted-foreground">{op.employee_id}</td>
                  <td className="py-1.5 pr-3">
                    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                      op.skill === 'ELECTRICAL' ? 'bg-blue-900/60 text-blue-300' :
                      op.skill === 'MECHANICAL' ? 'bg-orange-900/60 text-orange-300' :
                      'bg-purple-900/60 text-purple-300'
                    }`}>
                      {op.skill}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[10px]">{op.workcenter_id}</td>
                  <td className="py-1.5">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${op.is_active ? 'bg-green-900/60 text-green-300' : 'bg-red-900/60 text-red-300'}`}>
                      {op.is_active ? 'Sì' : 'No'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Tab: Scenari ──────────────────────────────────────────────────────────────

function ScenariosTab() {
  const { data: scenarios = [], isLoading, refetch } = useScenarios();

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs text-muted-foreground">{scenarios.length} scenari</span>
        <button onClick={() => refetch()} className="p-1.5 hover:bg-accent rounded"><RefreshCw size={13} /></button>
      </div>

      {isLoading ? (
        <div className="text-xs text-muted-foreground">Caricamento…</div>
      ) : (
        <div className="space-y-2">
          {scenarios.map((sc) => (
            <div key={sc.id} className="border border-border rounded p-3 hover:bg-accent/20">
              <div className="flex items-center justify-between mb-1">
                <span className="font-semibold text-sm">{sc.name}</span>
                <div className="flex gap-1">
                  {sc.is_active && (
                    <span className="text-[10px] bg-green-900/50 text-green-300 px-1.5 py-0.5 rounded font-semibold">ATTIVO</span>
                  )}
                  <span className="text-[10px] bg-blue-900/30 text-blue-300 px-1.5 py-0.5 rounded">{sc.objective_mode}</span>
                </div>
              </div>
              <div className="text-[10px] text-muted-foreground space-x-3">
                <span>ID: <span className="font-mono">{sc.id.slice(0, 8)}…</span></span>
                <span>Creato: {new Date(sc.created_at).toLocaleDateString('it-IT')}</span>
                <span>Machine: <span className="font-mono">{sc.machine_order_id.slice(0, 8)}…</span></span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Pagina principale ─────────────────────────────────────────────────────────

const TABS = [
  { id: 'orders',    label: 'Ordini Produzione' },
  { id: 'operations', label: 'Operazioni (+ RP)' },
  { id: 'rp',        label: 'Reference Points' },
  { id: 'operators', label: 'Operatori' },
  { id: 'scenarios', label: 'Scenari' },
] as const;

type TabId = typeof TABS[number]['id'];

export default function DBAdmin() {
  const { selectedMachineOrderId } = useMachineStore();
  const [activeTab, setActiveTab] = useState<TabId>('orders');

  return (
    <div className="h-full overflow-auto p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Database size={20} className="text-primary" />
        <div>
          <h1 className="text-lg font-bold">Database Explorer</h1>
          <p className="text-xs text-muted-foreground">
            Consulta le tabelle del database con dati denormalizzati e leggibili
          </p>
        </div>
      </div>

      {!selectedMachineOrderId && (
        <div className="border border-amber-600/40 bg-amber-900/20 rounded-lg p-4 mb-4 text-amber-200 text-sm">
          ⚠ Seleziona un machine order dal menu in alto per caricare gli ordini e le operazioni.
          Operatori e Scenari sono sempre visibili.
        </div>
      )}

      {/* Tab bar */}
      <div className="flex border-b border-border mb-4 overflow-x-auto">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-xs font-medium whitespace-nowrap border-b-2 transition-colors ${
              activeTab === tab.id
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Contenuto tab */}
      <div>
        {activeTab === 'orders' && selectedMachineOrderId && (
          <OrdersTab machineOrderId={selectedMachineOrderId} />
        )}
        {activeTab === 'operations' && selectedMachineOrderId && (
          <OperationsTab machineOrderId={selectedMachineOrderId} />
        )}
        {activeTab === 'rp' && selectedMachineOrderId && (
          <ReferencePointsTab machineOrderId={selectedMachineOrderId} />
        )}
        {(activeTab === 'orders' || activeTab === 'operations' || activeTab === 'rp') && !selectedMachineOrderId && (
          <div className="text-sm text-muted-foreground italic">
            Seleziona un machine order per visualizzare questa tab.
          </div>
        )}
        {activeTab === 'operators' && <OperatorsTab />}
        {activeTab === 'scenarios' && <ScenariosTab />}
      </div>
    </div>
  );
}