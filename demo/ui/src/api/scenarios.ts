import { apiFetch } from './client';
import type { CatalogResponse } from '../types';

export const getScenarioCatalog = () =>
  apiFetch<CatalogResponse>('/scenarios/catalog');
