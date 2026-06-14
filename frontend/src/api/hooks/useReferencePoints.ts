import { useQuery } from '@tanstack/react-query';
import apiClient from '../client';
import type { ReferencePoint, ReferencePointPrecedence } from '../types';

export function useReferencePoints(machineModelId: string | undefined) {
  return useQuery<ReferencePoint[]>({
    queryKey: ['reference-points', machineModelId],
    queryFn: async () => {
      const { data } = await apiClient.get<ReferencePoint[]>(
        `/api/reference-points/model/${machineModelId}`
      );
      return data;
    },
    enabled: !!machineModelId,
  });
}

export function useReferencePointPrecedences(machineModelId: string | undefined) {
  return useQuery<ReferencePointPrecedence[]>({
    queryKey: ['rp-precedences', machineModelId],
    queryFn: async () => {
      const { data } = await apiClient.get<ReferencePointPrecedence[]>(
        `/api/reference-points/model/${machineModelId}/precedences`
      );
      return data;
    },
    enabled: !!machineModelId,
  });
}
