import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import apiClient from '../api/client';
import { useAiSuggestions } from '../api/hooks/useAi';
import { useScheduleStore } from '../store/scheduleStore';
import { useMachineStore } from '../store/machineStore';
import SuggestionsList from '../components/ai/SuggestionsList';
import type { AiSuggestionType } from '../api/types';
import { Bot, Loader2, History, ChevronDown, ChevronUp } from 'lucide-react';

// ── Filter types ──────────────────────────────────────────────────────────────

const ACCEPTED_FILTERS = ['all', 'accepted', 'rejected', 'pending'] as const;
type AcceptedFilter = typeof ACCEPTED_FILTERS[number];

const TYPE_LABELS: Partial<Record<AiSuggestionType, string>> = {
  ON_DEMAND:          'On demand',
  PROACTIVE:          'Proattivo',
  DELAY_ANALYSIS:     'Analisi ritardo',
  HISTORICAL_PATTERN: 'Pattern storico',
  WHAT_IF:            'What-if',
  EXPLAIN_ENTRY:      'Spiega entry',
};

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AIAssistant() {
  const { activeScenarioId } = useScheduleStore();
  const { selectedMachineOrderId } = useMachineStore();

  const { data: suggestions = [] } = useAiSuggestions(activeScenarioId ?? undefined);

  // Advanced filters for suggestions history
  const [typeFilter, setTypeFilter]         = useState<AiSuggestionType | ''>('');
  const [acceptedFilter, setAcceptedFilter] = useState<AcceptedFilter>('all');
  const [dateFrom, setDateFrom]             = useState('');
  const [dateTo, setDateTo]                 = useState('');
  const [showHistorySection, setShowHistorySection] = useState(false);

  // Historical analysis
  const [historyResult, setHistoryResult] = useState<string | null>(null);
  const historyMutation = useMutation({
    mutationFn: () =>
      apiClient.post<{ analysis: unknown }>('/api/ai/analyze-history', {
        machine_order_id: selectedMachineOrderId,
      }),
    onSuccess: (res) => {
      const data = res.data.analysis;
      setHistoryResult(typeof data === 'string' ? data : JSON.stringify(data, null, 2));
    },
  });

  // Filtered suggestions
  const filteredSuggestions = suggestions.filter((s) => {
    if (typeFilter && s.suggestion_type !== typeFilter) return false;
    if (acceptedFilter === 'accepted' && s.accepted !== true)  return false;
    if (acceptedFilter === 'rejected' && s.accepted !== false) return false;
    if (acceptedFilter === 'pending'  && s.accepted !== null)  return false;
    if (dateFrom && new Date(s.created_at) < new Date(dateFrom)) return false;
    if (dateTo   && new Date(s.created_at) > new Date(dateTo))   return false;
    return true;
  });

  return (
    <div className="h-full overflow-auto p-6 space-y-6">
      <h1 className="text-lg font-bold flex items-center gap-2">
        <Bot size={20} className="text-primary" />
        AI Assistant
      </h1>

      {/* ── Suggestions with tabs (re-use SuggestionsList) ─── */}
      <section className="border border-border rounded-xl overflow-hidden">
        <div className="px-4 py-3 bg-muted border-b border-border">
          <h2 className="text-sm font-semibold">Suggerimenti attivi</h2>
        </div>
        <div style={{ maxHeight: 360, overflowY: 'auto' }}>
          <SuggestionsList scenarioId={activeScenarioId ?? undefined} />
        </div>
      </section>

      {/* ── Suggestions history with advanced filters ──────── */}
      <section className="border border-border rounded-xl overflow-hidden">
        <button
          onClick={() => setShowHistorySection((v) => !v)}
          className="w-full flex items-center justify-between px-4 py-3 bg-muted border-b border-border text-sm font-semibold hover:bg-accent"
        >
          <span className="flex items-center gap-2"><History size={14} /> Storico suggerimenti</span>
          {showHistorySection ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>

        {showHistorySection && (
          <div className="p-4 space-y-3">
            {/* Filters */}
            <div className="flex flex-wrap gap-2 text-sm">
              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value as AiSuggestionType | '')}
                className="border border-border rounded px-2 py-1 bg-background"
              >
                <option value="">Tutti i tipi</option>
                {Object.entries(TYPE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>

              <select
                value={acceptedFilter}
                onChange={(e) => setAcceptedFilter(e.target.value as AcceptedFilter)}
                className="border border-border rounded px-2 py-1 bg-background"
              >
                <option value="all">Tutti</option>
                <option value="accepted">Accettati</option>
                <option value="rejected">Rifiutati</option>
                <option value="pending">In attesa</option>
              </select>

              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
                className="border border-border rounded px-2 py-1 bg-background text-xs" />
              <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
                className="border border-border rounded px-2 py-1 bg-background text-xs" />

              {(typeFilter || acceptedFilter !== 'all' || dateFrom || dateTo) && (
                <button
                  onClick={() => { setTypeFilter(''); setAcceptedFilter('all'); setDateFrom(''); setDateTo(''); }}
                  className="text-xs text-muted-foreground hover:text-foreground underline"
                >
                  Azzera filtri
                </button>
              )}
            </div>

            {/* History list */}
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {filteredSuggestions.length === 0 ? (
                <p className="text-xs text-muted-foreground">Nessun suggerimento trovato.</p>
              ) : (
                filteredSuggestions.map((s) => (
                  <div key={s.id} className="text-xs border border-border rounded p-2 space-y-0.5">
                    <div className="flex justify-between">
                      <span className="font-medium">{TYPE_LABELS[s.suggestion_type] ?? s.suggestion_type}</span>
                      <span className={`${s.accepted === true ? 'text-green-600' : s.accepted === false ? 'text-red-500' : 'text-muted-foreground'}`}>
                        {s.accepted === true ? '✓ Accettato' : s.accepted === false ? '✗ Rifiutato' : '…'}
                      </span>
                    </div>
                    <p className="text-muted-foreground line-clamp-2">{s.suggestion_text}</p>
                    <p className="text-[10px] text-muted-foreground">{new Date(s.created_at).toLocaleString('it-IT')}</p>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </section>

      {/* ── Historical pattern analysis ─────────────────────── */}
      <section className="border border-border rounded-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">Analisi pattern storici</h2>
          <button
            onClick={() => historyMutation.mutate()}
            disabled={historyMutation.isPending || !selectedMachineOrderId}
            className="flex items-center gap-1.5 text-xs bg-primary text-primary-foreground rounded px-3 py-1.5 disabled:opacity-50"
          >
            {historyMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Bot size={12} />}
            Analizza pattern storici
          </button>
        </div>

        {historyResult && (
          <pre className="bg-muted rounded p-3 text-xs overflow-auto max-h-48 whitespace-pre-wrap">
            {historyResult}
          </pre>
        )}

        {!selectedMachineOrderId && (
          <p className="text-xs text-muted-foreground">
            Seleziona un ordine macchina per abilitare l&apos;analisi storica.
          </p>
        )}
      </section>
    </div>
  );
}

