import { useState, useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../../api/client';
import { useAiSuggestions } from '../../api/hooks/useAi';
import { useScheduleStore } from '../../store/scheduleStore';
import { useAiStore } from '../../store/aiStore';
import type { AiSuggestion, AiSuggestionType } from '../../api/types';
import { CheckCircle, XCircle, Loader2, Star } from 'lucide-react';

// ── Badge colours ─────────────────────────────────────────────────────────────

const TYPE_COLORS: Record<string, string> = {
  ON_DEMAND:        'bg-blue-100 text-blue-700',
  PROACTIVE:        'bg-purple-100 text-purple-700',
  DELAY_ANALYSIS:   'bg-orange-100 text-orange-700',
  HISTORICAL_PATTERN: 'bg-teal-100 text-teal-700',
  WHAT_IF:          'bg-yellow-100 text-yellow-700',
  EXPLAIN_ENTRY:    'bg-gray-100 text-gray-600',
};

// ── Props ─────────────────────────────────────────────────────────────────────

interface SuggestionsListProps {
  compact?: boolean;          // compact=true → used inside sidebar
  scenarioId?: string;
}

export default function SuggestionsList({ compact, scenarioId }: SuggestionsListProps) {
  const qc = useQueryClient();
  const { activeScenarioId } = useScheduleStore();
  const { resetUnread } = useAiStore();

  const sid = scenarioId ?? activeScenarioId ?? undefined;
  const { data: suggestions = [], isLoading } = useAiSuggestions(sid);

  const [filter, setFilter] = useState<'all' | 'high' | 'unread'>('all');
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const acceptMutation = useMutation({
    mutationFn: (id: string) => apiClient.patch(`/api/ai/suggestions/${id}/accept`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-suggestions', sid] }),
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => apiClient.patch(`/api/ai/suggestions/${id}/reject`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ai-suggestions', sid] }),
  });

  const filtered = useMemo(() => {
    return suggestions.filter((s) => {
      if (filter === 'high')   return (s.confidence_score ?? 0) >= 0.8;
      if (filter === 'unread') return s.accepted === null;
      return true;
    });
  }, [suggestions, filter]);

  function markAllRead() {
    resetUnread();
    // Optimistically accept all unread (server-side would need a bulk endpoint;
    // here we just reset the client counter)
  }

  if (isLoading) {
    return <div className="flex justify-center py-6"><Loader2 className="animate-spin" size={20} /></div>;
  }

  return (
    <div className="flex flex-col h-full">
      {/* Filters */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0 text-xs">
        <div className="flex gap-1">
          {(['all', 'high', 'unread'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2 py-0.5 rounded ${filter === f ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'}`}
            >
              {f === 'all' ? 'Tutti' : f === 'high' ? 'Alta priorità' : 'Non letti'}
            </button>
          ))}
        </div>
        <button onClick={markAllRead} className="text-muted-foreground hover:text-foreground text-[10px]">
          Segna letti
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto divide-y divide-border">
        {filtered.length === 0 && (
          <p className="text-muted-foreground text-xs text-center py-8">Nessun suggerimento.</p>
        )}
        {filtered.map((s) => (
          <SuggestionRow
            key={s.id}
            suggestion={s}
            compact={compact}
            onAccept={() => setConfirmId(s.id)}
            onReject={() => rejectMutation.mutate(s.id)}
            isPendingAccept={acceptMutation.isPending && confirmId === s.id}
            isPendingReject={rejectMutation.isPending}
          />
        ))}
      </div>

      {/* Confirm dialog */}
      {confirmId && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40">
          <div className="bg-card border border-border rounded-xl p-5 text-sm w-72 shadow-2xl">
            <p className="font-semibold mb-2">Applicare il suggerimento?</p>
            <p className="text-xs text-muted-foreground mb-4">
              Questa azione potrebbe modificare lo schedule attivo.
            </p>
            <div className="flex gap-2">
              <button onClick={() => setConfirmId(null)} className="flex-1 py-1.5 border border-border rounded hover:bg-accent">
                Annulla
              </button>
              <button
                onClick={() => { acceptMutation.mutate(confirmId); setConfirmId(null); }}
                className="flex-1 py-1.5 bg-primary text-primary-foreground rounded hover:opacity-90"
              >
                Applica
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sub-component ─────────────────────────────────────────────────────────────

function SuggestionRow({
  suggestion: s,
  compact,
  onAccept,
  onReject,
  isPendingAccept,
  isPendingReject,
}: {
  suggestion: AiSuggestion;
  compact?: boolean;
  onAccept: () => void;
  onReject: () => void;
  isPendingAccept: boolean;
  isPendingReject: boolean;
}) {
  const confidence = s.confidence_score ?? 0;

  return (
    <div className={`p-3 ${s.accepted === false ? 'opacity-40' : ''}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex gap-1 flex-wrap mb-1">
            <span className={`text-[10px] rounded px-1.5 py-0.5 ${TYPE_COLORS[s.suggestion_type] ?? 'bg-muted text-muted-foreground'}`}>
              {s.suggestion_type}
            </span>
            {confidence >= 0.8 && (
              <span className="text-[10px] text-yellow-600 flex items-center gap-0.5">
                <Star size={10} /> Alta priorità
              </span>
            )}
          </div>

          <p className={`text-xs ${compact ? 'line-clamp-2' : ''}`}>{s.suggestion_text}</p>

          {/* Confidence bar */}
          <div className="mt-1.5 h-1 bg-muted rounded overflow-hidden">
            <div className="h-full bg-primary transition-all" style={{ width: `${confidence * 100}%` }} />
          </div>

          <p className="text-[10px] text-muted-foreground mt-1">
            {new Date(s.created_at).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
          </p>
        </div>

        {/* Actions */}
        {s.accepted === null && (
          <div className="flex flex-col gap-1 shrink-0">
            <button
              onClick={onAccept}
              disabled={isPendingAccept}
              title="Applica"
              className="text-green-600 hover:text-green-800 disabled:opacity-50"
            >
              {isPendingAccept ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />}
            </button>
            <button
              onClick={onReject}
              disabled={isPendingReject}
              title="Ignora"
              className="text-muted-foreground hover:text-destructive disabled:opacity-50"
            >
              <XCircle size={14} />
            </button>
          </div>
        )}

        {s.accepted === true  && <CheckCircle size={14} className="text-green-500 shrink-0" />}
        {s.accepted === false && <XCircle    size={14} className="text-muted-foreground shrink-0" />}
      </div>
    </div>
  );
}
