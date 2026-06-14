import { useState } from 'react';
import { useScenarios, useScheduleScenario } from '../api/hooks/useSchedule';
import apiClient from '../api/client';
import { Download, FileText, FileJson, FileType, Database } from 'lucide-react';

type ExportFormat = 'csv' | 'json-sap' | 'pdf';

interface ExportLog {
  id: number;
  ts: string;
  format: ExportFormat;
  scenarioName: string;
}

let logId = 0;

// ── Download helpers ──────────────────────────────────────────────────────────

async function downloadBlob(url: string, filename: string) {
  const res = await fetch(apiClient.defaults.baseURL + url, {
    headers: { 'X-Request-ID': crypto.randomUUID() },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Icons ─────────────────────────────────────────────────────────────────────

const FMT_ICONS: Record<ExportFormat, React.ReactNode> = {
  'csv':      <FileText size={14} />,
  'json-sap': <FileJson size={14} />,
  'pdf':      <FileType  size={14} />,
};

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ExportPage() {
  const { data: scenarios = [] } = useScenarios();
  const [selectedId, setSelectedId] = useState<string>('');
  const [loading, setLoading]       = useState<ExportFormat | null>(null);
  const [error, setError]           = useState<string | null>(null);
  const [logs, setLogs]             = useState<ExportLog[]>([]);

  const { data: entries = [] } = useScheduleScenario(selectedId || undefined);

  const selectedScenario = scenarios.find((s) => s.id === selectedId);

  async function handleExport(format: ExportFormat) {
    if (!selectedId) return;
    setLoading(format);
    setError(null);
    try {
      const ext = format === 'json-sap' ? 'json' : format;
      await downloadBlob(
        `/api/export/scenario/${selectedId}/${format}`,
        `schedule_${selectedId}.${ext}`,
      );
      setLogs((prev) => [
        {
          id: ++logId,
          ts: new Date().toLocaleTimeString('it-IT'),
          format,
          scenarioName: selectedScenario?.name ?? selectedId,
        },
        ...prev.slice(0, 9),
      ]);
    } catch (e) {
      setError(`Errore durante l'esportazione ${format}: ${String(e)}`);
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="h-full overflow-auto p-6 space-y-6">
      <h1 className="text-lg font-bold">Export</h1>

      {/* Scenario selector */}
      <section className="border border-border rounded-xl p-4 space-y-3">
        <h2 className="text-sm font-semibold">Seleziona scenario</h2>
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          className="w-full border border-border rounded px-2 py-1.5 bg-background text-sm"
        >
          <option value="">— Seleziona uno scenario —</option>
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} {s.is_active ? '★ ACTIVE' : ''}
            </option>
          ))}
        </select>
      </section>

      {/* Download buttons */}
      <section className="border border-border rounded-xl p-4 space-y-3">
        <h2 className="text-sm font-semibold">Download</h2>

        {error && (
          <div className="text-xs text-destructive bg-red-50 border border-red-200 rounded p-2">
            {error}
          </div>
        )}

        <div className="flex flex-wrap gap-3">
          {/* CSV */}
          <button
            onClick={() => handleExport('csv')}
            disabled={!selectedId || !!loading}
            className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent disabled:opacity-50"
          >
            {loading === 'csv' ? '…' : FMT_ICONS['csv']}
            Scarica CSV
          </button>

          {/* JSON SAP */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleExport('json-sap')}
              disabled={!selectedId || !!loading}
              className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent disabled:opacity-50"
            >
              {loading === 'json-sap' ? '…' : FMT_ICONS['json-sap']}
              Scarica JSON SAP
            </button>
            <span className="text-xs bg-green-100 text-green-700 rounded px-1.5 py-0.5">
              Pronto per importazione SAP
            </span>
          </div>

          {/* PDF */}
          <button
            onClick={() => handleExport('pdf')}
            disabled={!selectedId || !!loading}
            className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent disabled:opacity-50"
          >
            {loading === 'pdf' ? '…' : FMT_ICONS['pdf']}
            Scarica PDF
          </button>
        </div>
      </section>

      {/* Data preview — first 10 entries */}
      {selectedId && entries.length > 0 && (
        <section className="border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-2 bg-muted border-b border-border flex items-center gap-2 text-sm font-semibold">
            <Database size={14} />
            Anteprima dati (prime 10 righe)
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-muted">
                <tr>
                  {['Operazione', 'Operatore', 'Inizio', 'Fine', 'Stato'].map((h) => (
                    <th key={h} className="px-3 py-1.5 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {entries.slice(0, 10).map((e) => (
                  <tr key={e.id} className="hover:bg-accent">
                    <td className="px-3 py-1.5 truncate max-w-[180px]">{e.operation_desc ?? e.operation_id}</td>
                    <td className="px-3 py-1.5">{e.operator_name}</td>
                    <td className="px-3 py-1.5">{new Date(e.start).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</td>
                    <td className="px-3 py-1.5">{new Date(e.end).toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</td>
                    <td className="px-3 py-1.5">{e.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {entries.length > 10 && (
            <p className="px-4 py-2 text-xs text-muted-foreground">
              … e altre {entries.length - 10} righe.
            </p>
          )}
        </section>
      )}

      {/* Export log */}
      {logs.length > 0 && (
        <section className="border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-2 bg-muted border-b border-border text-sm font-semibold">
            Log export
          </div>
          <ul className="divide-y divide-border text-xs">
            {logs.map((l) => (
              <li key={l.id} className="flex items-center justify-between px-4 py-2">
                <span className="text-muted-foreground">{l.ts}</span>
                <span>{l.scenarioName}</span>
                <span className="uppercase font-mono">{l.format}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

