import { useState, useRef, useEffect, useCallback } from 'react';
import { useMutation } from '@tanstack/react-query';
import apiClient from '../../api/client';
import { useAiStore } from '../../store/aiStore';
import { useScheduleStore } from '../../store/scheduleStore';
import { useMachineStore } from '../../store/machineStore';
import SuggestionsList from '../ai/SuggestionsList';
import type { ChatResponse } from '../../api/types';
import { Bot, X, Send, Trash2, Download, MessageSquare, Lightbulb, Loader2 } from 'lucide-react';

type Tab = 'chat' | 'suggestions';

// ── Message bubble ────────────────────────────────────────────────────────────

function Bubble({ role, content, response }: { role: 'user' | 'assistant'; content: string; response?: ChatResponse }) {
  const isUser = role === 'user';

  const downloadReport = () => {
    if (!response?.data?.report_text) return;
    const blob = new Blob([String(response.data.report_text)], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'report.txt';
    a.click();
  };

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-3`}>
      <div
        className={`max-w-[85%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap
          ${isUser ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground'}
        `}
      >
        {content}

        {/* Action buttons for AI responses */}
        {!isUser && response && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {response.action_type === 'REPORT' && (
              <button
                onClick={downloadReport}
                className="flex items-center gap-1 text-xs px-2 py-0.5 border border-border rounded hover:bg-background"
              >
                <Download size={10} /> Scarica report
              </button>
            )}
            {response.action_type === 'SUGGESTION' && response.apply_actions?.map((action, i) => (
              <button
                key={i}
                className="text-xs px-2 py-0.5 border border-border rounded hover:bg-background"
              >
                Applica: {String(action)}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── AISidebar ─────────────────────────────────────────────────────────────────

export default function AISidebar() {
  const { unreadCount, resetUnread, chatHistory, addChatMessage, clearChatHistory } = useAiStore();
  const { activeScenarioId } = useScheduleStore();
  const { selectedMachineOrderId } = useMachineStore();

  const [open, setOpen]         = useState(false);
  const [tab, setTab]           = useState<Tab>('chat');
  const [input, setInput]       = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isTyping, setIsTyping] = useState(false);
  const [responses, setResponses] = useState<Record<number, ChatResponse>>({});

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll
  useEffect(() => {
    if (open && tab === 'chat') {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatHistory, open, tab]);

  // Clear unread when tab opens on suggestions
  useEffect(() => {
    if (open && tab === 'suggestions') resetUnread();
  }, [open, tab, resetUnread]);

  const chatMutation = useMutation({
    mutationFn: async (message: string) => {
      if (!selectedMachineOrderId) throw new Error('No machine order selected');
      const { data } = await apiClient.post<ChatResponse>('/api/ai/chat', {
        machine_order_id: selectedMachineOrderId,
        scenario_id: activeScenarioId ?? undefined,
        message,
        session_id: sessionId ?? undefined,
      });
      return data;
    },
    onSuccess: (data) => {
      setSessionId(data.session_id);
      addChatMessage({ role: 'assistant', content: data.message, action_type: data.action_type });
      setResponses((prev) => ({ ...prev, [chatHistory.length]: data }));
      setIsTyping(false);
    },
    onError: () => {
      addChatMessage({ role: 'assistant', content: 'Errore nella comunicazione con l\'AI.' });
      setIsTyping(false);
    },
  });

  const handleSend = useCallback(() => {
    const msg = input.trim();
    if (!msg || chatMutation.isPending) return;
    addChatMessage({ role: 'user', content: msg });
    setInput('');
    setIsTyping(true);
    chatMutation.mutate(msg);
  }, [input, chatMutation, addChatMessage]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  const clearChat = async () => {
    clearChatHistory();
    if (sessionId) {
      await apiClient.delete(`/api/ai/chat/${sessionId}`).catch(() => {});
      setSessionId(null);
    }
  };

  return (
    <>
      {/* Floating button */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="fixed bottom-5 right-5 z-40 w-12 h-12 rounded-full bg-primary text-primary-foreground shadow-lg flex items-center justify-center hover:opacity-90 transition-all"
        aria-label="AI Assistant"
      >
        <Bot size={20} />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 bg-destructive text-white text-[10px] rounded-full w-5 h-5 flex items-center justify-center">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* Slide-over panel */}
      {open && (
        <>
          {/* Backdrop — click closes but does NOT block page interaction */}
          <div
            className="fixed inset-0 z-40 bg-black/10"
            onClick={() => setOpen(false)}
          />

          <div
            className="fixed right-0 top-0 bottom-0 z-50 w-[400px] bg-card border-l border-border flex flex-col shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
              <div className="flex items-center gap-2">
                <Bot size={16} className="text-primary" />
                <span className="font-semibold text-sm">AI Assistant</span>
              </div>
              <button onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground">
                <X size={16} />
              </button>
            </div>

            {/* Tabs */}
            <div className="flex border-b border-border shrink-0">
              <button
                onClick={() => setTab('chat')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-medium transition-colors
                  ${tab === 'chat' ? 'border-b-2 border-primary text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <MessageSquare size={12} /> Chat
              </button>
              <button
                onClick={() => setTab('suggestions')}
                className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-medium transition-colors
                  ${tab === 'suggestions' ? 'border-b-2 border-primary text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              >
                <Lightbulb size={12} /> Suggerimenti
                {unreadCount > 0 && (
                  <span className="bg-destructive text-white text-[9px] rounded-full w-4 h-4 flex items-center justify-center">
                    {unreadCount}
                  </span>
                )}
              </button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-hidden">
              {tab === 'chat' ? (
                <div className="flex flex-col h-full">
                  {/* Messages */}
                  <div className="flex-1 overflow-y-auto p-4">
                    {chatHistory.length === 0 && (
                      <p className="text-muted-foreground text-xs text-center mt-8">
                        Inizia la conversazione con l&apos;AI. Puoi chiedere suggerimenti,
                        spiegazioni o simulazioni sullo schedule corrente.
                      </p>
                    )}
                    {chatHistory.map((msg, i) => (
                      <Bubble
                        key={i}
                        role={msg.role as 'user' | 'assistant'}
                        content={msg.content}
                        response={msg.role === 'assistant' ? responses[i - 1] : undefined}
                      />
                    ))}
                    {isTyping && (
                      <div className="flex justify-start mb-3">
                        <div className="bg-muted rounded-xl px-3 py-2 text-xs text-muted-foreground flex items-center gap-1">
                          <Loader2 size={12} className="animate-spin" />
                          AI sta scrivendo…
                        </div>
                      </div>
                    )}
                    <div ref={messagesEndRef} />
                  </div>

                  {/* Input */}
                  <div className="border-t border-border p-3 shrink-0">
                    <div className="flex items-end gap-2">
                      <textarea
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Scrivi… (Ctrl+Enter per inviare)"
                        rows={2}
                        className="flex-1 border border-border rounded-lg px-3 py-2 text-sm resize-none bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                      <div className="flex flex-col gap-1">
                        <button
                          onClick={handleSend}
                          disabled={!input.trim() || chatMutation.isPending}
                          className="p-2 bg-primary text-primary-foreground rounded-lg disabled:opacity-50"
                          title="Invia (Ctrl+Enter)"
                        >
                          {chatMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                        </button>
                        <button
                          onClick={clearChat}
                          className="p-2 text-muted-foreground hover:text-destructive rounded-lg"
                          title="Nuova conversazione"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ) : (
                <SuggestionsList compact scenarioId={activeScenarioId ?? undefined} />
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
