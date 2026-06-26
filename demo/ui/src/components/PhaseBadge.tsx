import { cn } from '../lib/utils';

interface PhaseBadgeProps {
  phase: string;
  className?: string;
}

export function PhaseBadge({ phase, className }: PhaseBadgeProps) {
  if (!phase) return null;
  const label = phase.replace(/_/g, ' ');
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium',
        'bg-blue-50 text-blue-700 border border-blue-200',
        className,
      )}
      title={`Current scenario phase: ${label}`}
    >
      {label}
    </span>
  );
}
