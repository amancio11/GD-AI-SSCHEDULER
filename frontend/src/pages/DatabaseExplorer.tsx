// frontend/src/pages/DatabaseExplorer.tsx
//
// Pagina DB Explorer.
// - Sidebar: lista tabelle con conteggio righe
// - Pannello principale: builder query con filtri e join, tabella risultati paginata
// - Export CSV del risultato corrente

import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import {
  Database,
  Filter,
  Plus,
  Trash2,
  Play,
  Download,
  GitMerge,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

interface ColumnInfo {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  foreign_key: string[];
}
interface TableInfo {
  name: string;
  columns: ColumnInfo[];
}
interface JoinOption {
  table: string;
  on: string;
}
type FilterOp =
  | "eq" | "neq" | "gt" | "gte" | "lt" | "lte"
  | "like" | "ilike" | "in" | "not_in" | "is_null" | "not_null";

interface Filter {
  column: string;
  op: FilterOp;
  value: string;
}

interface QueryResult {
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
  columns: string[];
}

const API = ""; // axios baseURL configurata altrove

const OP_LABELS: Record<FilterOp, string> = {
  eq: "=",
  neq: "≠",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  like: "like",
  ilike: "ilike",
  in: "in (csv)",
  not_in: "not in (csv)",
  is_null: "è null",
  not_null: "non null",
};

const VALUELESS_OPS: FilterOp[] = ["is_null", "not_null"];
const LIST_OPS: FilterOp[] = ["in", "not_in"];

export default function DatabaseExplorer(): JSX.Element {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [activeTable, setActiveTable] = useState<string>("");
  const [joinable, setJoinable] = useState<JoinOption[]>([]);
  const [selectedJoins, setSelectedJoins] = useState<string[]>([]);
  const [filters, setFilters] = useState<Filter[]>([]);
  const [orderBy, setOrderBy] = useState<string>("");
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("asc");
  const [limit, setLimit] = useState<number>(50);
  const [offset, setOffset] = useState<number>(0);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    axios.get<{ tables: TableInfo[] }>(`${API}/api/database/tables`).then((r) => {
      setTables(r.data.tables);
      if (r.data.tables.length > 0 && !activeTable) {
        setActiveTable(r.data.tables[0].name);
      }
      // Conteggi in parallelo
      Promise.all(
        r.data.tables.map((t) =>
          axios
            .get<{ count: number }>(`${API}/api/database/tables/${t.name}/count`)
            .then((rr) => [t.name, rr.data.count] as [string, number])
            .catch(() => [t.name, 0] as [string, number])
        )
      ).then((pairs) => {
        const m: Record<string, number> = {};
        for (const [k, v] of pairs) m[k] = v;
        setCounts(m);
      });
    });
  }, []);

  useEffect(() => {
    if (!activeTable) return;
    axios
      .get<{ joinable: JoinOption[] }>(`${API}/api/database/joins/${activeTable}`)
      .then((r) => setJoinable(r.data.joinable))
      .catch(() => setJoinable([]));
    setSelectedJoins([]);
    setFilters([]);
    setOrderBy("");
    setOffset(0);
    setResult(null);
  }, [activeTable]);

  const activeTableInfo = useMemo(
    () => tables.find((t) => t.name === activeTable),
    [tables, activeTable]
  );

  const runQuery = async (newOffset?: number): Promise<void> => {
    if (!activeTable) return;
    setLoading(true);
    setError(null);
    try {
      const body = {
        table: activeTable,
        filters: filters
          .filter((f) => f.column && f.op)
          .map((f) => {
            if (VALUELESS_OPS.includes(f.op)) return { column: f.column, op: f.op };
            if (LIST_OPS.includes(f.op)) {
              return {
                column: f.column,
                op: f.op,
                value: f.value.split(",").map((s) => s.trim()).filter(Boolean),
              };
            }
            return { column: f.column, op: f.op, value: f.value };
          }),
        joins: selectedJoins.map((t) => ({ table: t })),
        order_by: orderBy || undefined,
        order_dir: orderDir,
        limit,
        offset: newOffset ?? offset,
      };
      const r = await axios.post<QueryResult>(`${API}/api/database/query`, body);
      setResult(r.data);
      if (newOffset !== undefined) setOffset(newOffset);
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e.response?.data?.detail ?? String(err));
    } finally {
      setLoading(false);
    }
  };

  const exportCsv = (): void => {
    if (!result || result.rows.length === 0) return;
    const cols = result.columns;
    const escape = (v: unknown): string => {
      if (v === null || v === undefined) return "";
      const s = String(v);
      if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
      return s;
    };
    const lines = [cols.join(",")];
    for (const row of result.rows) {
      lines.push(cols.map((c) => escape(row[c])).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${activeTable}-${Date.now()}.csv`;
    link.click();
  };

  const allColumnOptions = useMemo(() => {
    const opts: { label: string; value: string }[] = [];
    if (activeTableInfo) {
      for (const c of activeTableInfo.columns) {
        opts.push({ label: c.name, value: c.name });
      }
    }
    for (const jt of selectedJoins) {
      const ti = tables.find((t) => t.name === jt);
      if (ti) {
        for (const c of ti.columns) {
          opts.push({ label: `${jt}.${c.name}`, value: `${jt}.${c.name}` });
        }
      }
    }
    return opts;
  }, [activeTableInfo, selectedJoins, tables]);

  return (
    <div className="flex gap-4 h-[calc(100vh-120px)]">
      {/* Sidebar */}
      <Card className="w-64 overflow-auto flex-shrink-0">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="h-4 w-4" />
            Tabelle ({tables.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-2">
          <div className="space-y-1">
            {tables.map((t) => (
              <button
                key={t.name}
                onClick={() => setActiveTable(t.name)}
                className={`w-full text-left px-2 py-1.5 rounded text-xs font-mono flex items-center justify-between ${
                  activeTable === t.name
                    ? "bg-blue-100 text-blue-900"
                    : "hover:bg-stone-100 text-stone-700"
                }`}
              >
                <span>{t.name}</span>
                <span className="text-stone-500 text-[10px]">
                  {counts[t.name] ?? "…"}
                </span>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Main panel */}
      <div className="flex-1 flex flex-col gap-3 overflow-hidden">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center justify-between">
              <span className="font-mono">{activeTable}</span>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => runQuery(0)} disabled={loading}>
                  <Play className="h-3.5 w-3.5 mr-1" />
                  Esegui
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={exportCsv}
                  disabled={!result || result.rows.length === 0}
                >
                  <Download className="h-3.5 w-3.5 mr-1" />
                  CSV
                </Button>
              </div>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 pt-0">
            {/* Joins */}
            <div>
              <div className="text-xs font-semibold text-stone-600 mb-1.5 flex items-center gap-1">
                <GitMerge className="h-3.5 w-3.5" />
                Join disponibili ({joinable.length})
              </div>
              <div className="flex gap-1.5 flex-wrap">
                {joinable.map((j) => {
                  const isSelected = selectedJoins.includes(j.table);
                  return (
                    <Badge
                      key={j.table}
                      variant={isSelected ? "default" : "outline"}
                      className="cursor-pointer text-xs"
                      onClick={() => {
                        if (isSelected) {
                          setSelectedJoins(selectedJoins.filter((t) => t !== j.table));
                        } else {
                          setSelectedJoins([...selectedJoins, j.table]);
                        }
                      }}
                      title={j.on}
                    >
                      {isSelected ? "✓ " : "+ "}
                      {j.table}
                    </Badge>
                  );
                })}
                {joinable.length === 0 && (
                  <span className="text-xs text-stone-500 italic">
                    Nessun join predefinito per questa tabella
                  </span>
                )}
              </div>
            </div>

            {/* Filters */}
            <div>
              <div className="text-xs font-semibold text-stone-600 mb-1.5 flex items-center justify-between">
                <span className="flex items-center gap-1">
                  <Filter className="h-3.5 w-3.5" />
                  Filtri
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 text-xs"
                  onClick={() =>
                    setFilters([...filters, { column: "", op: "eq", value: "" }])
                  }
                >
                  <Plus className="h-3 w-3 mr-1" />
                  Aggiungi
                </Button>
              </div>
              <div className="space-y-1.5">
                {filters.map((f, idx) => (
                  <div key={idx} className="flex gap-1.5 items-center">
                    <Select
                      value={f.column}
                      onValueChange={(v) => {
                        const copy = [...filters];
                        copy[idx] = { ...copy[idx], column: v ?? '' };
                        setFilters(copy);
                      }}
                    >
                      <SelectTrigger className="h-8 w-[200px] text-xs">
                        <SelectValue placeholder="Colonna" />
                      </SelectTrigger>
                      <SelectContent>
                        {allColumnOptions.map((o) => (
                          <SelectItem key={o.value} value={o.value}>
                            {o.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select
                      value={f.op}
                      onValueChange={(v) => {
                        const copy = [...filters];
                        copy[idx] = { ...copy[idx], op: v as FilterOp };
                        setFilters(copy);
                      }}
                    >
                      <SelectTrigger className="h-8 w-[110px] text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(Object.keys(OP_LABELS) as FilterOp[]).map((op) => (
                          <SelectItem key={op} value={op}>
                            {OP_LABELS[op]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {!VALUELESS_OPS.includes(f.op) && (
                      <Input
                        value={f.value}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                          const copy = [...filters];
                          copy[idx] = { ...copy[idx], value: e.target.value };
                          setFilters(copy);
                        }}
                        placeholder={LIST_OPS.includes(f.op) ? "v1,v2,v3" : "valore"}
                        className="h-8 text-xs flex-1"
                      />
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 w-8 p-0"
                      onClick={() => setFilters(filters.filter((_, i) => i !== idx))}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
                {filters.length === 0 && (
                  <div className="text-xs text-stone-500 italic">Nessun filtro</div>
                )}
              </div>
            </div>

            {/* Order + limit */}
            <div className="flex gap-2 items-center text-xs">
              <span className="text-stone-600">Ordina per:</span>
              <Select value={orderBy} onValueChange={(v) => setOrderBy(v ?? '')}>
                <SelectTrigger className="h-8 w-[200px]">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">—</SelectItem>
                  {allColumnOptions.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={orderDir} onValueChange={(v) => setOrderDir((v ?? 'asc') as "asc" | "desc")}>
                <SelectTrigger className="h-8 w-[80px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="asc">asc</SelectItem>
                  <SelectItem value="desc">desc</SelectItem>
                </SelectContent>
              </Select>
              <span className="text-stone-600 ml-4">Limit:</span>
              <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v ?? '50'))}>
                <SelectTrigger className="h-8 w-[80px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[25, 50, 100, 250, 500, 1000].map((n) => (
                    <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {error && (
              <div className="p-2 text-xs bg-red-50 border border-red-200 rounded text-red-800">
                {error}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Results */}
        <Card className="flex-1 overflow-hidden flex flex-col">
          <CardHeader className="pb-2 flex flex-row items-center justify-between">
            <CardTitle className="text-sm">
              Risultati: {result ? `${result.rows.length} di ${result.total}` : "—"}
            </CardTitle>
            {result && (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={offset === 0}
                  onClick={() => runQuery(Math.max(0, offset - limit))}
                  className="h-7"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </Button>
                <span className="text-xs text-stone-600">
                  {offset + 1} – {Math.min(offset + result.rows.length, result.total)}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={offset + limit >= result.total}
                  onClick={() => runQuery(offset + limit)}
                  className="h-7"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent className="overflow-auto flex-1 p-0">
            {result && result.rows.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    {result.columns.map((c) => (
                      <TableHead key={c} className="text-xs font-mono whitespace-nowrap">
                        {c.replace(`${activeTable}__`, "")}
                        {c.startsWith(`${activeTable}__`) ? "" : (
                          <span className="text-stone-400 text-[10px] ml-1">
                            ({c.split("__")[0]})
                          </span>
                        )}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.rows.map((row, ri) => (
                    <TableRow key={ri}>
                      {result.columns.map((c) => (
                        <TableCell key={c} className="text-xs font-mono whitespace-nowrap">
                          {row[c] === null ? (
                            <span className="text-stone-400 italic">null</span>
                          ) : typeof row[c] === "boolean" ? (
                            String(row[c])
                          ) : (
                            String(row[c])
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <div className="p-12 text-center text-stone-500 text-sm">
                {loading ? "Caricamento…" : "Premi Esegui per visualizzare i risultati"}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}