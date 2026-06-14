import { useQuery } from '@tanstack/react-query';
import apiClient from '../client';
import type { AiSuggestion } from '../types';

export function useAiSuggestions(scenarioId: string | undefined) {
  return useQuery<AiSuggestion[]>({
    queryKey: ['ai-suggestions', scenarioId],
    queryFn: async () => {
      const { data } = await apiClient.get<AiSuggestion[]>(`/api/ai/suggestions/${scenarioId}`);
      return data;
    },
    enabled: !!scenarioId,
  });
}
