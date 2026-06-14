import { useQuery } from '@tanstack/react-query';
import apiClient from '../client';
import type { MissingComponent } from '../types';

export function useMissingComponents(machineOrderId: string | undefined) {
  return useQuery<MissingComponent[]>({
    queryKey: ['missing-components', machineOrderId],
    queryFn: async () => {
      const { data } = await apiClient.get<MissingComponent[]>(
        `/api/missing-components/machine/${machineOrderId}`
      );
      return data;
    },
    enabled: !!machineOrderId,
  });
}
