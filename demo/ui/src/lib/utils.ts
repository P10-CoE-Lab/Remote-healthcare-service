import { clsx, type ClassValue } from 'clsx';
import type { RiskLevel, AlertSeverity } from '../types';

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function formatTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function formatMinutes(minutes: number): string {
  const m = Math.floor(minutes);
  const s = Math.floor((minutes - m) * 60);
  return `${m}m ${s.toString().padStart(2, '0')}s`;
}

export function riskLevelToSeverity(level: RiskLevel): AlertSeverity {
  switch (level) {
    case 'critical': return 'critical';
    case 'high':     return 'critical';
    case 'medium':   return 'warning';
    default:         return 'info';
  }
}

export function hrColor(hr: number | null): string {
  if (hr === null) return 'text-slate-400';
  if (hr > 100 || hr < 50) return 'text-red-600';
  if (hr > 90  || hr < 58) return 'text-amber-600';
  return 'text-slate-800';
}

export function spo2Color(spo2: number | null): string {
  if (spo2 === null) return 'text-slate-400';
  if (spo2 < 93) return 'text-red-600';
  if (spo2 < 96) return 'text-amber-600';
  return 'text-slate-800';
}

export function hrvColor(hrv: number | null): string {
  if (hrv === null) return 'text-slate-400';
  if (hrv < 15) return 'text-red-600';
  if (hrv < 25) return 'text-amber-600';
  return 'text-slate-800';
}

export function sparklineColor(riskLevel: RiskLevel): string {
  switch (riskLevel) {
    case 'critical': return '#dc2626';
    case 'high':     return '#ea580c';
    case 'medium':   return '#d97706';
    default:         return '#2563eb';
  }
}

export function statusDotClass(status: string, riskLevel: RiskLevel): string {
  if (status !== 'running') return 'bg-slate-400';
  if (riskLevel === 'critical' || riskLevel === 'high') return 'bg-red-500';
  if (riskLevel === 'medium') return 'bg-amber-500';
  return 'bg-emerald-500';
}

export function scenarioLabel(scenarioId: string): string {
  return scenarioId
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
