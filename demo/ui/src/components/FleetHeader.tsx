import { Plus, Activity, RefreshCw } from 'lucide-react';
import type { PatientState } from '../types';
import { cn } from '../lib/utils';

interface FleetHeaderProps {
  patients: PatientState[];
  isLoading: boolean;
  onAddPatient: () => void;
  alertCount?: number;   // total clinical alert events from the alert panel
}

export function FleetHeader({ patients, isLoading, onAddPatient, alertCount = 0 }: FleetHeaderProps) {
  const running       = patients.filter((p) => p.status === 'running').length;
  const highRiskCount = patients.filter((p) => p.risk_level === 'critical' || p.risk_level === 'high').length;
  const warningCount  = patients.filter((p) => p.risk_level === 'medium').length;
  const stable        = running - highRiskCount - warningCount;

  return (
    <header className="bg-white border-b border-slate-200 sticky top-0 z-30" style={{ boxShadow: '0 1px 4px 0 rgb(0 0 0 / 0.06)' }}>
      <div className="max-w-screen-xl mx-auto px-6 h-16 flex items-center justify-between gap-4">

        {/* Brand */}
        <div className="flex items-center gap-3 shrink-0">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <Activity size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-slate-900 leading-tight">
              Remote Healthcare Monitor
            </h1>
            <p className="text-[10px] text-slate-400 leading-tight">Live Patient Fleet</p>
          </div>
        </div>

        {/* Fleet stats */}
        {patients.length > 0 && (
          <div className="hidden sm:flex items-center gap-4">
            <Stat value={patients.length} label="Monitored" color="text-slate-700" />
            <div className="w-px h-6 bg-slate-200" />
            {stable > 0 && (
              <Stat value={stable} label="Stable" color="text-emerald-600" dot="bg-emerald-500" />
            )}
            {warningCount > 0 && (
              <Stat value={warningCount} label="Warning" color="text-amber-600" dot="bg-amber-500" />
            )}
            {highRiskCount > 0 && (
              <Stat value={highRiskCount} label="High Risk" color="text-red-600" dot="bg-red-500" pulse />
            )}
            {alertCount > 0 && (
              <>
                <div className="w-px h-6 bg-slate-200" />
                <Stat value={alertCount} label="Alerts Fired" color="text-red-700" dot="bg-red-600" pulse />
              </>
            )}
          </div>
        )}

        {/* Right side */}
        <div className="flex items-center gap-3 shrink-0">
          {isLoading && (
            <RefreshCw size={14} className="text-slate-400 animate-spin" />
          )}

          <button
            onClick={onAddPatient}
            disabled={patients.length >= 10}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all',
              'bg-blue-600 text-white hover:bg-blue-700 active:scale-95 shadow-sm',
              'disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100',
            )}
          >
            <Plus size={16} strokeWidth={2.5} />
            Add Patient
          </button>
        </div>
      </div>
    </header>
  );
}

function Stat({
  value, label, color, dot, pulse,
}: {
  value: number;
  label: string;
  color: string;
  dot?: string;
  pulse?: boolean;
}) {
  return (
    <div className="flex items-center gap-1.5">
      {dot && (
        <span
          className={cn('w-2 h-2 rounded-full', dot, pulse && 'animate-pulse-slow')}
        />
      )}
      <span className={cn('text-sm font-semibold tabular-nums', color)}>{value}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </div>
  );
}
