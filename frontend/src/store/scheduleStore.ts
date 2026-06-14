import { create } from 'zustand';

type GanttViewMode = 'BY_OPERATOR' | 'BY_ORDER';

interface ScheduleStore {
  activeScenarioId: string | null;
  ganttViewMode: GanttViewMode;
  setActiveScenarioId: (id: string | null) => void;
  setGanttViewMode: (mode: GanttViewMode) => void;
}

export const useScheduleStore = create<ScheduleStore>((set) => ({
  activeScenarioId: null,
  ganttViewMode: 'BY_OPERATOR',
  setActiveScenarioId: (id) => set({ activeScenarioId: id }),
  setGanttViewMode: (mode) => set({ ganttViewMode: mode }),
}));
