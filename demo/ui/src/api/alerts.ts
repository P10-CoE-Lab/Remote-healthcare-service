import { apiFetch } from './client';
import type { AlertsResponse } from '../types';

export const getAlerts = (limit = 50) =>
  apiFetch<AlertsResponse>(`/alerts?limit=${limit}`);

export const clearAlerts = () =>
  apiFetch<{ success: boolean }>('/alerts', { method: 'DELETE' });
