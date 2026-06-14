import { useQuery } from '@tanstack/react-query';
import apiClient from '../client';
import type { ScheduleScenario, GanttEntry, ScheduleEntry } from '../types';

export function useScenarios() {
  return useQuery<ScheduleScenario[]>({
    queryKey: ['scenarios'],
    queryFn: async () => {
      const { data } = await apiClient.get<ScheduleScenario[]>('/api/scenarios?page=1&size=50');
      return data;
    },
  });
}

export function useScenario(id: string | undefined) {
  return useQuery<ScheduleScenario>({
    queryKey: ['scenario', id],
    queryFn: async () => {
      const { data } = await apiClient.get<ScheduleScenario>(`/api/scenarios/${id}`);
      return data;
    },
    enabled: !!id,
  });
}

export function useScheduleScenario(id: string | undefined) {
  return useQuery<ScheduleEntry[]>({
    queryKey: ['schedule', id],
    queryFn: async () => {
      const { data } = await apiClient.get<ScheduleEntry[]>(`/api/schedule/scenario/${id}`);
      return data;
    },
    enabled: !!id,
  });
}

export function useGanttData(scenarioId: string | undefined) {
  return useQuery<GanttEntry[]>({
    queryKey: ['gantt', scenarioId],
    queryFn: async () => {
      const { data } = await apiClient.get<GanttEntry[]>(
        `/api/schedule/scenario/${scenarioId}/gantt-data`
      );
      return data;
    },
    enabled: !!scenarioId,
  });
}
