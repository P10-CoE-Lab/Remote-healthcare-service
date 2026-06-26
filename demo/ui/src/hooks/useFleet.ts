import { useQuery } from '@tanstack/react-query';
import { getFleet } from '../api/patients';

export function useFleet() {
  return useQuery({
    queryKey: ['fleet'],
    queryFn: getFleet,
    refetchInterval: 2000,
    staleTime: 0,
    retry: 3,
  });
}
