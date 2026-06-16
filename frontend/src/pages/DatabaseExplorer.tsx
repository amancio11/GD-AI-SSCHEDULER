// frontend/src/pages/DatabaseExplorer.tsx
//
// FIX: crash "Cannot read properties of undefined (reading 'find')" e
//      "Cannot read properties of undefined (reading 'length')"
//
// Causa: il Promise.all annidato dentro il .then di axios creava una race
// condition — quando setCounts triggerava un re-render, tables era ancora []
// nel closure e r.data era già fuori scope. Convertito tutto in async/await
// con guard difensivi su ogni array.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import apiClient from "../api/client";

// Wrapper: usa apiClient (baseURL già configurata a http://localhost:8000)
const api = apiClient;
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import {
  Database, Filter, Plus, Trash2, Play, Download,
  GitMerge, ChevronLeft, ChevronRight,
} from "lucide-react";

// ── Types ─────────────────────────────────────────────────────────────────────

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

interface FilterClause {
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

const OP_LABELS: Record<FilterOp, string> = {
  eq: "=", neq: "≠", gt: ">", gte: "≥", lt: "<", lte: "≤",
  like: "like", ilike: "ilike",
  in: "in (csv)", not_in: "not in (csv)",
  is_null: "è null", not_null: "non null",
};

const VALUELESS_OPS: FilterOp[] = ["is_null", "not_null"];
const LIST_OPS: FilterOp[] = ["in", "not_in"];

// ── Component ─────────────────────────────────────────────────────────────────

export default function DatabaseExplorer(): JSX.Element {
  const [tables, setTables]           = useState<TableInfo[]>([]);
  const [counts, setCounts]           = useState<Record<string, number>>({});
  const [activeTable, setActiveTable] = useState<string>("");
  const [joinable, setJoinable]       = useState<JoinOption[]>([]);
  const [selectedJoins, setSelectedJoins] = useState<string[]>([]);
  const [filters, setFilters]         = useState<FilterClause[]>([]);
  const [orderBy, setOrderBy]         = useState<string>("");
  const [orderDir, setOrderDir]       = useState<"asc" | "desc">("asc");
  const [limit, setLimit]             = useState<number>(50);
  const [offset, setOffset]           = useState<number>(0);
  const [result, setResult]           = useState<QueryResult | null>(null);
  const [loading, setLoading]         = useState<boolean>(false);
  const [error, setError]             = useState<string | null>(null);
  const [initError, setInitError]     = useState<string | null>(null);
  const [retryCount, setRetryCount]   = useState(0);

  // ── Carica tabelle all'avvio ── async/await, no promise annidati ─────────
  useEffect(() => {
    let cancelled = false;

    async function loadTables() {
      try {
        // Prova prima /api/database/tables (endpoint completo con colonne)
        // Se fallisce, fallback a /api/admin/tables (solo nomi)
        let raw: TableInfo[] | string[] | undefined;

        try {
          const r = await api.get<unknown>(`/api/database/tables`);
          const data = r.data as Record<string, unknown>;
          raw = data?.tables as TableInfo[] | string[] | undefined;
        } catch (e1) {
          // Primo endpoint fallito — prova admin
          console.warn("[DatabaseExplorer] /api/database/tables fallito, provo /api/admin/tables", e1);
          try {
            const r2 = await api.get<unknown>(`/api/admin/tables`);
            const data2 = r2.data as Record<string, unknown>;
            raw = data2?.tables as string[] | undefined;
          } catch (e2) {
            throw new Error(`Entrambi gli endpoint falliti.\n/api/database/tables: ${e1}\n/api/admin/tables: ${e2}`);
          }
        }

        if (!Array.isArray(raw)) {
          console.error("[DatabaseExplorer] risposta raw:", raw);
          setInitError(
            `Risposta API non valida: tables non è un array.\n` +
            `Valore ricevuto: ${JSON.stringify(raw)}\n\n` +
            `Verifica che il router /api/database sia registrato in main.py ` +
            `e che import app.models sia presente in database.py.`
          );
          return;
        }

        // Normalizza: se sono stringhe (admin endpoint), convertiamole in TableInfo
        const normalized: TableInfo[] = raw.map((item) =>
          typeof item === "string"
            ? { name: item, columns: [] }
            : { name: (item as TableInfo).name ?? "", columns: (item as TableInfo).columns ?? [] }
        );

        if (cancelled) return;
        setTables(normalized);

        if (normalized.length > 0) {
          setActiveTable((prev) => prev || normalized[0].name);
        }

        // Carica conteggi in parallelo — separato dal set state di tables
        const pairs = await Promise.all(
          normalized.map(async (t) => {
            try {
              const rr = await api.get<{ count: number }>(
                `/api/database/tables/${t.name}/count`
              );
              return [t.name, rr.data?.count ?? 0] as [string, number];
            } catch {
              return [t.name, 0] as [string, number];
            }
          })
        );

        if (cancelled) return;
        const m: Record<string, number> = {};
        for (const [k, v] of pairs) m[k] = v;
        setCounts(m);

      } catch (err) {
        if (!cancelled) {
          setInitError(`Errore caricamento tabelle: ${String(err)}`);
        }
      }
    }

    loadTables();
    return () => { cancelled = true; };
  }, [retryCount]);

  // ── Cambia tabella attiva ─────────────────────────────────────────────────
  useEffect(() => {
    if (!activeTable) return;
    let cancelled = false;

    api
      .get<{ joinable: JoinOption[] }>(`/api/database/joins/${activeTable}`)
      .then((r) => {
        if (!cancelled) setJoinable(Array.isArray(r.data?.joinable) ? r.data.joinable : []);
      })
      .catch(() => { if (!cancelled) setJoinable([]); });

    setSelectedJoins([]);
    setFilters([]);
    setOrderBy("");
    setOffset(0);
    setResult(null);

    return () => { cancelled = true; };
  }, [activeTable]);

  // ── Dati tabella attiva ────────────────────────────────────────────────────
  // Guard: tables potrebbe essere vuoto al primo render
  const activeTableInfo = useMemo<TableInfo | undefined>(() => {
    if (!Array.isArray(tables) || !activeTable) return undefined;
    return tables.find((t) => t.name === activeTable);
  }, [tables, activeTable]);

  // ── Colonne disponibili (tabella attiva + join selezionati) ───────────────
  const allColumnOptions = useMemo<{ label: string; value: string }[]>(() => {
    const opts: { label: string; value: string }[] = [];
    const cols = activeTableInfo?.columns ?? [];
    for (const c of cols) {
      opts.push({ label: c.name, value: c.name });
    }
    for (const jt of selectedJoins) {
      const ti = Array.isArray(tables) ? tables.find((t) => t.name === jt) : undefined;
      if (ti) {
        for (const c of ti.columns ?? []) {
          opts.push({ label: `${jt}.${c.name}`, value: `${jt}.${c.name}` });
        }
      }
    }
    return opts;
  }, [activeTableInfo, selectedJoins, tables]);

  // ── Esegui query ─────────────────────────────────────────────────────────
  const runQuery = useCallback(async (newOffset?: number): Promise<void> => {
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
                column: f.column, op: f.op,
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
      const r = await api.post<QueryResult>(`/api/database/query`, body);
      setResult(r.data);
      if (newOffset !== undefined) setOffset(newOffset);
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e.response?.data?.detail ?? String(err));
    } finally {
      setLoading(false);
    }
  }, [activeTable, filters, selectedJoins, orderBy, orderDir, limit, offset]);

  // ── Export CSV ────────────────────────────────────────────────────────────
  const exportCsv = (): void => {
    if (!result || !result.rows?.length) return;
    const cols = result.columns ?? [];
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

  // ── Helpers filtri ────────────────────────────────────────────────────────
  const addFilter = () =>
    setFilters((prev) => [...prev, { column: allColumnOptions[0]?.value ?? "", op: "eq", value: "" }]);

  const updateFilter = (i: number, patch: Partial<FilterClause>) =>
    setFilters((prev) => prev.map((f, idx) => idx === i ? { ...f, ...patch } : f));

  const removeFilter = (i: number) =>
    setFilters((prev) => prev.filter((_, idx) => idx !== i));

  // ── Render ────────────────────────────────────────────────────────────────
  if (initError) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <div className="text-center max-w-2xl">
          <p className="text-destructive font-semibold text-base mb-3">
            ⚠️ Errore caricamento Database Explorer
          </p>
          <pre className="text-xs bg-muted p-4 rounded text-left whitespace-pre-wrap break-all mb-4 max-h-60 overflow-auto">
            {initError}
          </pre>
          <div className="text-xs text-muted-foreground space-y-1 text-left bg-muted/50 rounded p-3">
            <p className="font-semibold mb-2">Checklist risoluzione:</p>
            <p>1. Il backend FastAPI è avviato? (<code>uvicorn app.main:app --reload</code>)</p>
            <p>2. Il router database è registrato in <code>main.py</code>?</p>
            <p className="font-mono bg-muted px-2 py-1 rounded">
              from app.api.routes import database{"\n"}
              app.include_router(database.router)
            </p>
            <p>3. In <code>database.py</code> c'è <code>import app.models</code> PRIMA della definizione del router?</p>
            <p>4. Apri <code>/api/database/tables</code> nel browser — cosa risponde?</p>
          </div>
          <button
            className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded text-sm"
            onClick={() => { setInitError(null); setRetryCount(c => c + 1); }}
          >
            Riprova
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-4 h-[calc(100vh-120px)]">

      {/* ── Sidebar tabelle ── */}
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
                <span className="truncate">{t.name}</span>
                <span className="text-stone-400 text-[10px] ml-1 flex-shrink-0">
                  {counts[t.name] !== undefined ? counts[t.name] : "…"}
                </span>
              </button>
            ))}
            {tables.length === 0 && (
              <p className="text-xs text-muted-foreground px-2 py-4 text-center">
                Caricamento…
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* ── Pannello principale ── */}
      <div className="flex-1 flex flex-col gap-3 overflow-hidden min-w-0">

        {/* Query builder */}
        <Card className="flex-shrink-0">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Filter className="h-4 w-4" />
              Query Builder — <span className="font-mono">{activeTable || "—"}</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">

            {/* Colonne tabella attiva */}
            {activeTableInfo && (activeTableInfo.columns ?? []).length > 0 && (
              <div className="flex flex-wrap gap-1">
                {activeTableInfo.columns.map((c) => (
                  <Badge
                    key={c.name}
                    variant={c.primary_key ? "default" : "secondary"}
                    className="font-mono text-[10px]"
                  >
                    {c.name}
                    {c.primary_key && " 🔑"}
                    {(c.foreign_key ?? []).length > 0 && " 🔗"}
                  </Badge>
                ))}
              </div>
            )}

            {/* Join */}
            {joinable.length > 0 && (
              <div className="flex flex-wrap gap-1 items-center">
                <GitMerge className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-xs text-muted-foreground">Join:</span>
                {joinable.map((j) => (
                  <button
                    key={j.table}
                    onClick={() =>
                      setSelectedJoins((prev) =>
                        prev.includes(j.table)
                          ? prev.filter((t) => t !== j.table)
                          : [...prev, j.table]
                      )
                    }
                    className={`text-[10px] px-2 py-0.5 rounded border font-mono ${
                      selectedJoins.includes(j.table)
                        ? "bg-indigo-100 border-indigo-400 text-indigo-800"
                        : "border-stone-200 text-stone-600 hover:bg-stone-50"
                    }`}
                  >
                    {j.table}
                  </button>
                ))}
              </div>
            )}

            {/* Filtri */}
            <div className="space-y-1.5">
              {filters.map((f, i) => (
                <div key={i} className="flex gap-2 items-center">
                  <Select
                    value={f.column}
                    onValueChange={(v) => updateFilter(i, { column: v ?? "" })}
                  >
                    <SelectTrigger className="h-7 text-xs w-40 font-mono">
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
                    onValueChange={(v) => updateFilter(i, { op: (v ?? "eq") as FilterOp })}
                  >
                    <SelectTrigger className="h-7 text-xs w-28">
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
                      className="h-7 text-xs flex-1"
                      placeholder={LIST_OPS.includes(f.op) ? "val1, val2, …" : "valore"}
                      value={f.value}
                      onChange={(e) => updateFilter(i, { value: e.target.value })}
                    />
                  )}

                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 w-7 p-0"
                    onClick={() => removeFilter(i)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ))}

              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                onClick={addFilter}
                disabled={allColumnOptions.length === 0}
              >
                <Plus className="h-3.5 w-3.5 mr-1" /> Aggiungi filtro
              </Button>
            </div>

            {/* Order by + Limit */}
            <div className="flex gap-2 items-center flex-wrap">
              <span className="text-xs text-muted-foreground">Ordina per:</span>
              <Select value={orderBy || "__none__"} onValueChange={(v) => setOrderBy((v ?? "") === "__none__" ? "" : (v ?? ""))}>
                <SelectTrigger className="h-7 text-xs w-40 font-mono">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">—</SelectItem>
                  {allColumnOptions.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select value={orderDir} onValueChange={(v) => setOrderDir((v ?? "asc") as "asc" | "desc")}>
                <SelectTrigger className="h-7 text-xs w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="asc">ASC</SelectItem>
                  <SelectItem value="desc">DESC</SelectItem>
                </SelectContent>
              </Select>

              <span className="text-xs text-muted-foreground ml-2">Limite:</span>
              <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v ?? "50"))}>
                <SelectTrigger className="h-7 text-xs w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[25, 50, 100, 250, 500].map((n) => (
                    <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Button
                size="sm"
                className="h-7 text-xs ml-auto"
                onClick={() => runQuery(0)}
                disabled={loading || !activeTable}
              >
                <Play className="h-3.5 w-3.5 mr-1" />
                {loading ? "Esecuzione…" : "Esegui"}
              </Button>

              {result && result.rows?.length > 0 && (
                <Button size="sm" variant="outline" className="h-7 text-xs" onClick={exportCsv}>
                  <Download className="h-3.5 w-3.5 mr-1" /> CSV
                </Button>
              )}
            </div>

            {error && (
              <p className="text-xs text-destructive bg-destructive/10 rounded p-2">{error}</p>
            )}
          </CardContent>
        </Card>

        {/* Risultati */}
        <Card className="flex-1 overflow-hidden flex flex-col min-h-0">
          <CardHeader className="pb-2 flex-shrink-0">
            <CardTitle className="text-sm flex items-center justify-between">
              <span>
                Risultati{" "}
                {result ? `— ${result.rows?.length ?? 0} di ${result.total}` : "—"}
              </span>
              {result && (
                <div className="flex items-center gap-2">
                  <Button
                    size="sm" variant="ghost"
                    disabled={offset === 0}
                    onClick={() => runQuery(Math.max(0, offset - limit))}
                    className="h-7"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </Button>
                  <span className="text-xs text-stone-600">
                    {offset + 1}–{Math.min(offset + (result.rows?.length ?? 0), result.total)}
                  </span>
                  <Button
                    size="sm" variant="ghost"
                    disabled={offset + limit >= result.total}
                    onClick={() => runQuery(offset + limit)}
                    className="h-7"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="overflow-auto flex-1 p-0">
            {result && (result.rows?.length ?? 0) > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    {(result.columns ?? []).map((c) => (
                      <TableHead key={c} className="text-xs font-mono whitespace-nowrap sticky top-0 bg-background">
                        {c.replace(`${activeTable}__`, "")}
                        {!c.startsWith(`${activeTable}__`) && c.includes("__") && (
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
                      {(result.columns ?? []).map((c) => (
                        <TableCell key={c} className="text-xs font-mono whitespace-nowrap max-w-xs truncate">
                          {row[c] === null || row[c] === undefined ? (
                            <span className="text-stone-400 italic">null</span>
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
                {loading
                  ? "Esecuzione query…"
                  : result
                  ? "Nessun risultato per i filtri selezionati"
                  : "Seleziona una tabella e premi Esegui"}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}