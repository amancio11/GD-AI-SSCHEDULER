/**
 * DBAdmin — Pagina di amministrazione diretta del database.
 *
 * Permette al planner/sviluppatore di ispezionare e modificare i dati
 * delle 19 tabelle del MES Scheduler senza usare psql o pgAdmin.
 *
 * Funzionalità:
 *  - Seleziona una tabella dalla lista a sinistra
 *  - Vedi i dati in una griglia con paginazione
 *  - Clicca su una cella per modificarla inline
 *  - Premi Salva per inviare il PATCH al backend
 *  - Premi il pulsante rosso per eliminare una riga
 *
 * I dati sono read-only per le colonne 'id', 'created_at', 'updated_at'.
 */
import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Database, ChevronLeft, ChevronRight, Save, Trash2, RefreshCw } from 'lucide-react';
import { apiClient } from '../api/client';

// ── Tipi ─────────────────────────────────────────────────────────────────────

interface TableListResponse {
  tables: string[];
}

interface TableDataResponse {
  table: string;
  columns: string[];
  total: number;
  page: number;
  size: number;
  rows: Record<string, unknown>[];
}

// Colonne che non devono essere modificabili
const READ_ONLY_COLS = new Set(['id', 'created_at', 'updated_at', 'last_activity']);

// ── Componente principale ─────────────────────────────────────────────────────

export default function DBAdmin() {
  const qc = useQueryClient();
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 30;

  // Stato per la cella in edit inline
  const [editCell, setEditCell] = useState<{ rowIdx: number; col: string; value: string } | null>(null);

  // Resetta la pagina quando cambia tabella
  useEffect(() => { setPage(1); setEditCell(null); }, [selectedTable]);

  // ── Query: lista tabelle ─────────────────────────────────────────────────
  const { data: tableList } = useQuery<TableListResponse>({
    queryKey: ['admin-tables'],
    queryFn: () => apiClient.get('/api/admin/tables').then(r => r.data),
    staleTime: Infinity,
  });

  // ── Query: dati tabella selezionata ─────────────────────────────────────
  const { data: tableData, isFetching } = useQuery<TableDataResponse>({
    queryKey: ['admin-table-data', selectedTable, page],
    queryFn: () =>
      apiClient
        .get(`/api/admin/tables/${selectedTable}`, { params: { page, size: PAGE_SIZE } })
        .then(r => r.data),
    enabled: !!selectedTable,
    staleTime: 0,
  });

  // ── Mutation: aggiorna riga ─────────────────────────────────────────────
  const updateMutation = useMutation({
    mutationFn: ({ id, col, value }: { id: string; col: string; value: string }) =>
      apiClient.put(`/api/admin/tables/${selectedTable}/${id}`, { [col]: value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-table-data', selectedTable] });
      setEditCell(null);
    },
  });

  // ── Mutation: elimina riga ─────────────────────────────────────────────
  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.delete(`/api/admin/tables/${selectedTable}/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-table-data', selectedTable] });
    },
  });

  // ── Helpers ──────────────────────────────────────────────────────────────

  const totalPages = tableData ? Math.ceil(tableData.total / PAGE_SIZE) : 0;

  function cellDisplay(val: unknown): string {
    if (val === null || val === undefined) return '';
    if (typeof val === 'boolean') return val ? 'true' : 'false';
    if (typeof val === 'object') return JSON.stringify(val);
    return String(val);
  }

  function handleCellClick(rowIdx: number, col: string, val: unknown) {
    if (READ_ONLY_COLS.has(col)) return;
    setEditCell({ rowIdx, col, value: cellDisplay(val) });
  }

  function handleSave(row: Record<string, unknown>) {
    if (!editCell) return;
    const id = String(row['id']);
    updateMutation.mutate({ id, col: editCell.col, value: editCell.value });
  }

  function handleDelete(row: Record<string, unknown>) {
    if (!confirm(`Eliminare la riga con id ${row['id']}?`)) return;
    deleteMutation.mutate(String(row['id']));
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full">
      {/* ── Sidebar tabelle ── */}
      <aside className="w-52 shrink-0 border-r border-border bg-card overflow-y-auto">
        <div className="p-3 border-b border-border flex items-center gap-2">
          <Database size={14} className="text-primary" />
          <span className="text-sm font-semibold">Tabelle DB</span>
        </div>
        <ul className="py-1">
          {tableList?.tables.map(t => (
            <li key={t}>
              <button
                onClick={() => setSelectedTable(t)}
                className={`w-full text-left px-3 py-1.5 text-xs font-mono transition-colors ${
                  selectedTable === t
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                }`}
              >
                {t}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      {/* ── Area principale ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {!selectedTable ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <Database size={48} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">Seleziona una tabella dalla sidebar</p>
            </div>
          </div>
        ) : (
          <>
            {/* Header tabella */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-card shrink-0">
              <div className="flex items-center gap-2">
                <span className="font-mono font-semibold text-sm">{selectedTable}</span>
                {tableData && (
                  <span className="text-xs text-muted-foreground">
                    {tableData.total} righe totali
                  </span>
                )}
                {isFetching && <RefreshCw size={12} className="animate-spin text-muted-foreground" />}
              </div>

              {/* Paginazione */}
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage(p => p - 1)}
                  className="p-1 rounded hover:bg-accent disabled:opacity-30"
                >
                  <ChevronLeft size={14} />
                </button>
                <span>Pag. {page} / {totalPages || 1}</span>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage(p => p + 1)}
                  className="p-1 rounded hover:bg-accent disabled:opacity-30"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>

            {/* Griglia dati */}
            <div className="flex-1 overflow-auto">
              {tableData && tableData.rows.length > 0 ? (
                <table className="w-full text-xs border-collapse">
                  <thead className="sticky top-0 bg-muted z-10">
                    <tr>
                      <th className="px-2 py-1.5 border border-border text-left font-mono text-muted-foreground w-8">
                        #
                      </th>
                      {tableData.columns.map(col => (
                        <th
                          key={col}
                          className={`px-2 py-1.5 border border-border text-left font-mono whitespace-nowrap ${
                            READ_ONLY_COLS.has(col) ? 'text-muted-foreground' : 'text-foreground'
                          }`}
                        >
                          {col}
                          {READ_ONLY_COLS.has(col) && (
                            <span className="ml-1 text-[10px] opacity-50">🔒</span>
                          )}
                        </th>
                      ))}
                      <th className="px-2 py-1.5 border border-border w-16" />
                    </tr>
                  </thead>
                  <tbody>
                    {tableData.rows.map((row, rowIdx) => (
                      <tr
                        key={rowIdx}
                        className="hover:bg-accent/40 transition-colors"
                      >
                        <td className="px-2 py-1 border border-border text-muted-foreground text-center">
                          {(page - 1) * PAGE_SIZE + rowIdx + 1}
                        </td>
                        {tableData.columns.map(col => {
                          const isEditing = editCell?.rowIdx === rowIdx && editCell?.col === col;
                          const isReadOnly = READ_ONLY_COLS.has(col);
                          const val = row[col];

                          return (
                            <td
                              key={col}
                              className={`px-2 py-1 border border-border max-w-[200px] ${
                                isEditing
                                  ? 'p-0 ring-2 ring-primary ring-inset'
                                  : isReadOnly
                                    ? 'text-muted-foreground'
                                    : 'cursor-pointer hover:bg-primary/10'
                              }`}
                              onClick={() => !isEditing && handleCellClick(rowIdx, col, val)}
                            >
                              {isEditing ? (
                                <div className="flex items-center gap-1">
                                  <input
                                    autoFocus
                                    value={editCell.value}
                                    onChange={e => setEditCell({ ...editCell, value: e.target.value })}
                                    onKeyDown={e => {
                                      if (e.key === 'Enter') handleSave(row);
                                      if (e.key === 'Escape') setEditCell(null);
                                    }}
                                    className="flex-1 min-w-0 px-1 py-0.5 bg-background text-foreground outline-none text-xs font-mono"
                                  />
                                  <button
                                    onClick={() => handleSave(row)}
                                    className="shrink-0 p-0.5 text-primary hover:text-primary/80"
                                    title="Salva (Enter)"
                                  >
                                    <Save size={12} />
                                  </button>
                                </div>
                              ) : (
                                <span className="truncate block font-mono">
                                  {val === null || val === undefined
                                    ? <span className="text-muted-foreground/50 italic">null</span>
                                    : typeof val === 'boolean'
                                      ? <span className={val ? 'text-green-600' : 'text-red-500'}>{String(val)}</span>
                                      : typeof val === 'object'
                                        ? <span className="text-blue-500 text-[10px]">{JSON.stringify(val).slice(0, 60)}</span>
                                        : cellDisplay(val)
                                  }
                                </span>
                              )}
                            </td>
                          );
                        })}
                        {/* Azione elimina */}
                        <td className="px-2 py-1 border border-border text-center">
                          {'id' in row && (
                            <button
                              onClick={() => handleDelete(row)}
                              className="text-destructive hover:text-destructive/70 p-0.5 rounded"
                              title="Elimina riga"
                            >
                              <Trash2 size={12} />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : tableData ? (
                <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
                  Nessuna riga in questa tabella.
                </div>
              ) : null}
            </div>

            {/* Footer con legenda */}
            <div className="px-4 py-1.5 border-t border-border bg-card text-[10px] text-muted-foreground flex items-center gap-4 shrink-0">
              <span>🔒 = colonna non modificabile</span>
              <span>Click su una cella per modificarla • Enter per salvare • Esc per annullare</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
