import type { RiskLevel } from '../types';
import { cn } from '../lib/utils';

interface RiskBadgeProps {
  level: RiskLevel;
  score?: number;
  className?: string;
}

const CONFIG: Record<RiskLevel, { label: string; classes: string }> = {
  none:     { label: 'Normal',    classes: 'bg-slate-100 text-slate-600 border-slate-200' },
  low:      { label: 'Low Risk',  classes: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
  medium:   { label: 'Medium',    classes: 'bg-amber-50 text-amber-700 border-amber-200' },
  high:     { label: 'High Risk', classes: 'bg-orange-50 text-orange-700 border-orange-200' },
  critical: { label: 'Critical',  classes: 'bg-red-50 text-red-700 border-red-300 font-semibold' },
};

export function RiskBadge({ level, score, className }: RiskBadgeProps) {
  const { label, classes } = CONFIG[level] ?? CONFIG.none;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border',
        classes,
        className,
      )}
    >
      {label}
      {score !== undefined && score > 0 && (
        <span className="opacity-70">· {Math.min(Math.round(score), 100)}</span>
      )}
    </span>
  );
}
