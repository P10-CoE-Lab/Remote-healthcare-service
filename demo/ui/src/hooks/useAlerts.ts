import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getAlerts, clearAlerts } from '../api/alerts';

export function useAlerts() {
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ['alerts'],
    queryFn: () => getAlerts(50),
    refetchInterval: 3000,
    staleTime: 0,
  });

  const clear = useMutation({
    mutationFn: clearAlerts,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });

  return {
    alerts:   query.data?.alerts ?? [],
    total:    query.data?.total  ?? 0,
    isLoading: query.isLoading,
    clearAlerts: clear.mutate,
  };
}
