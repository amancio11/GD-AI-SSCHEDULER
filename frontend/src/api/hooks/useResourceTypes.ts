import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import apiClient from '../client';

export type SkillType = 'ELECTRICAL' | 'MECHANICAL' | 'MULTI';

export interface WeekdayAvailability {
  count: number;
  hours: number;
}
// chiavi "0".."6" = lun..dom
export type WeekdaySchedule = Record<string, WeekdayAvailability>;

export interface ResourceType {
  id: string;
  name: string | null;
  workcenter_id: string;
  skill: SkillType;
  daily_capacity_hours: number;
  count: number;
  weekday_schedule: WeekdaySchedule | null;
  is_active: boolean;
}

export type ResourceTypeInput = Omit<ResourceType, 'id'>;

export interface Workcenter {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
}

export function useResourceTypes() {
  return useQuery<ResourceType[]>({
    queryKey: ['resource-types'],
    queryFn: async () => {
      const { data } = await apiClient.get<ResourceType[]>('/api/resource-types');
      return data;
    },
  });
}

export function useWorkcenters() {
  return useQuery<Workcenter[]>({
    queryKey: ['workcenters'],
    queryFn: async () => {
      const { data } = await apiClient.get<Workcenter[]>('/api/workcenters');
      return data;
    },
  });
}

export function useCreateResourceType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ResourceTypeInput) => {
      const { data } = await apiClient.post<ResourceType>('/api/resource-types', payload);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-types'] }),
  });
}

export function useUpdateResourceType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: Partial<ResourceTypeInput> & { id: string }) => {
      const { data } = await apiClient.patch<ResourceType>(`/api/resource-types/${id}`, payload);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-types'] }),
  });
}

export function useDeleteResourceType() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/resource-types/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['resource-types'] }),
  });
}
