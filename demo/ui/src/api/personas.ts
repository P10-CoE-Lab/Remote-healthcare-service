import { apiFetch } from './client';
import type { PersonasResponse } from '../types';

export const getPersonas = () =>
  apiFetch<PersonasResponse>('/personas');
