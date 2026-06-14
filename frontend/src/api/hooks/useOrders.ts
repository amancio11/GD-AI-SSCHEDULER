import { useQuery } from '@tanstack/react-query';
import apiClient from '../client';
import type { BOMTreeNode, ProductionOrder, Operation } from '../types';

export interface MachineOrder {
  id: string;
  sap_order_id: string;
  description: string | null;
  status: string;
}

export function useMachineOrders() {
  return useQuery<MachineOrder[]>({
    queryKey: ['machine-orders'],
    queryFn: async () => {
      const { data } = await apiClient.get<MachineOrder[]>('/api/orders/machines');
      return data;
    },
    staleTime: 60_000,
  });
}

export function useBOMTree(machineOrderId: string | undefined) {
  return useQuery<BOMTreeNode>({
    queryKey: ['bom-tree', machineOrderId],
    queryFn: async () => {
      const { data } = await apiClient.get<BOMTreeNode>(
        `/api/orders/machine/${machineOrderId}/bom-tree`
      );
      return data;
    },
    enabled: !!machineOrderId,
  });
}

export function useProductionOrder(id: string | undefined) {
  return useQuery<ProductionOrder>({
    queryKey: ['production-order', id],
    queryFn: async () => {
      const { data } = await apiClient.get<ProductionOrder>(`/api/orders/${id}`);
      return data;
    },
    enabled: !!id,
  });
}

export function useOrderOperations(orderId: string | undefined) {
  return useQuery<Operation[]>({
    queryKey: ['order-operations', orderId],
    queryFn: async () => {
      const { data } = await apiClient.get<Operation[]>(`/api/orders/${orderId}/operations`);
      return data;
    },
    enabled: !!orderId,
  });
}
