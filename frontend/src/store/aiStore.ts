import { create } from 'zustand';
import type { AiSuggestion } from '../api/types';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  action_type?: string;
  data?: Record<string, unknown>;
  apply_actions?: unknown[];
}

interface AiStore {
  suggestions: AiSuggestion[];
  chatHistory: ChatMessage[];
  unreadCount: number;
  setSuggestions: (s: AiSuggestion[]) => void;
  addSuggestion: (s: AiSuggestion) => void;
  addChatMessage: (msg: ChatMessage) => void;
  clearChatHistory: () => void;
  incrementUnread: (by?: number) => void;
  resetUnread: () => void;
}

export const useAiStore = create<AiStore>((set) => ({
  suggestions: [],
  chatHistory: [],
  unreadCount: 0,
  setSuggestions: (suggestions) => set({ suggestions }),
  addSuggestion: (s) => set((state) => ({ suggestions: [s, ...state.suggestions] })),
  addChatMessage: (msg) => set((state) => ({ chatHistory: [...state.chatHistory, msg] })),
  clearChatHistory: () => set({ chatHistory: [] }),
  incrementUnread: (by = 1) => set((state) => ({ unreadCount: state.unreadCount + by })),
  resetUnread: () => set({ unreadCount: 0 }),
}));
