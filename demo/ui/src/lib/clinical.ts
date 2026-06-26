import type { ClinicalAlert, RiskLevel } from '../types';

/** Convert a raw alert into plain clinical language for the client view. */
export function clinicalMessage(alert: ClinicalAlert): string {
  const v   = alert.sensor_value !== null ? Math.round((alert.sensor_value ?? 0) * 10) / 10 : null;
  const thr = alert.threshold;
  switch (alert.sensor_name) {
    case 'heart_rate':
      if (v !== null && thr !== null) {
        return v > thr
          ? `Elevated heart rate — ${Math.round(v)} bpm`
          : `Low heart rate — ${Math.round(v)} bpm`;
      }
      return 'Abnormal heart rate detected';
    case 'spo2':
      return v !== null ? `Low blood oxygen — ${v}%` : 'Low blood oxygen detected';
    case 'heart_rate_variability':
      return v !== null ? `Low heart rate variability — ${Math.round(v)} ms` : 'Low HRV detected';
    case 'accel_magnitude':
      return 'Fall detected — high impact reading';
    default:
      // Cloud/risk escalation alerts have rule_name as clinical description
      if (alert.rule_id.startsWith('H-')) {
        const level = alert.risk_level;
        const score = Math.round(alert.risk_score);
        return `${level.charAt(0).toUpperCase() + level.slice(1)} clinical risk — composite score ${score}/100`;
      }
      return alert.rule_name || 'Clinical alert';
  }
}

/** Map risk level to a simple three-tier clinical status. */
export function clinicalStatus(level: RiskLevel): 'normal' | 'warning' | 'critical' {
  if (level === 'critical' || level === 'high') return 'critical';
  if (level === 'medium') return 'warning';
  return 'normal';
}

export const STATUS_STYLES = {
  normal:   { dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-200', label: 'Normal' },
  warning:  { dot: 'bg-amber-500',   text: 'text-amber-700',   bg: 'bg-amber-50',   border: 'border-amber-200',   label: 'Warning' },
  critical: { dot: 'bg-red-500',     text: 'text-red-700',     bg: 'bg-red-50',     border: 'border-red-300',     label: 'Critical' },
} as const;
