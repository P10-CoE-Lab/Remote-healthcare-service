import { useQuery } from '@tanstack/react-query';
import { getPatientVitals } from '../api/patients';
import type { PatientVitals } from '../types';

const EMPTY_VITALS: PatientVitals = {
  heart_rate: null,
  spo2: null,
  hrv: null,
  heart_rate_history: [],
  spo2_history: [],
  battery_level: null,
};

export function usePatientVitals(deviceId: string) {
  const query = useQuery({
    queryKey: ['vitals', deviceId],
    queryFn: () => getPatientVitals(deviceId),
    refetchInterval: 2000,
    staleTime: 0,
    enabled: !!deviceId,
  });

  return query.data ?? EMPTY_VITALS;
}
