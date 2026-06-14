import { useQuery } from '@tanstack/react-query';
import apiClient from '../client';
import type { Operator, OperatorCalendarEntry } from '../types';

export function useOperators() {
  return useQuery<Operator[]>({
    queryKey: ['operators'],
    queryFn: async () => {
      const { data } = await apiClient.get<Operator[]>('/api/operators?page=1&size=100');
      return data;
    },
  });
}

export function useOperatorCalendar(operatorId: string | undefined, dateFrom?: string, dateTo?: string) {
  return useQuery<OperatorCalendarEntry[]>({
    queryKey: ['operator-calendar', operatorId, dateFrom, dateTo],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo);
      const { data } = await apiClient.get<OperatorCalendarEntry[]>(
        `/api/operators/${operatorId}/calendar?${params}`
      );
      return data;
    },
    enabled: !!operatorId,
  });
}
