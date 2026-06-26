import { apiFetch } from './client';
import type {
  FleetResponse,
  AddPatientPayload,
  AddPatientResponse,
  PatientVitals,
} from '../types';

export const getFleet = () =>
  apiFetch<FleetResponse>('/patients');

export const addPatient = (payload: AddPatientPayload) =>
  apiFetch<AddPatientResponse>('/patients/add', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

export const removePatient = (patientId: string) =>
  apiFetch<{ success: boolean; message: string }>(`/patients/${patientId}`, {
    method: 'DELETE',
  });

export const triggerEvent = (patientId: string, eventType: string) =>
  apiFetch<{ success: boolean; message: string }>(
    `/patients/${patientId}/event/trigger`,
    {
      method: 'POST',
      body: JSON.stringify({ event_type: eventType }),
    },
  );

export const getPatientVitals = (deviceId: string) =>
  apiFetch<PatientVitals>(`/vitals/${deviceId}`);

export const getPatientSummary = (patientId: string) =>
  apiFetch<{ summary: string; patient_id: string; label: string; generated_at: string; baseline_personalised: boolean }>(
    `/patients/${patientId}/summary`,
  );

export const getAlertExplanation = (alertId: string) =>
  apiFetch<{ explanation: string }>(`/alerts/${alertId}/explanation`);
