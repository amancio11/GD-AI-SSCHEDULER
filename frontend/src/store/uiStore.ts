import { create } from 'zustand';

type Theme = 'light' | 'dark';

interface UiStore {
  sidebarCollapsed: boolean;
  theme: Theme;
  websocketConnected: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;
  toggleTheme: () => void;
  setTheme: (t: Theme) => void;
  setWebsocketConnected: (v: boolean) => void;
}

export const useUiStore = create<UiStore>((set) => ({
  sidebarCollapsed: false,
  theme: 'light',
  websocketConnected: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleTheme: () =>
    set((state) => ({ theme: state.theme === 'light' ? 'dark' : 'light' })),
  setTheme: (t) => set({ theme: t }),
  setWebsocketConnected: (v) => set({ websocketConnected: v }),
}));
