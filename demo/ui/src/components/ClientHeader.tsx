import { Activity } from 'lucide-react';
import type { PatientState } from '../types';
import { clinicalStatus } from '../lib/clinical';
import { cn } from '../lib/utils';

interface ClientHeaderProps {
  patients: PatientState[];
  alertCount: number;
}

export function ClientHeader({ patients, alertCount }: ClientHeaderProps) {
  const running  = patients.filter((p) => p.status === 'running').length;
  const critical = patients.filter((p) => clinicalStatus(p.risk_level) === 'critical').length;
  const warning  = patients.filter((p) => clinicalStatus(p.risk_level) === 'warning').length;
  const normal   = running - critical - warning;

  return (
    <header
      className="bg-white border-b border-slate-200 sticky top-0 z-30"
      style={{ boxShadow: '0 1px 4px 0 rgb(0 0 0 / 0.06)' }}
    >
      <div className="max-w-screen-xl mx-auto px-6 h-16 flex items-center justify-between gap-4">

        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <Activity size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-slate-900 leading-tight">Patient Monitoring</h1>
            <p className="text-[10px] text-slate-400 leading-tight">Remote Healthcare System</p>
          </div>
        </div>

        {/* Fleet summary */}
        {patients.length > 0 && (
          <div className="hidden sm:flex items-center gap-5">
            <SummaryItem value={patients.length} label="Monitored" color="text-slate-600" />
            <div className="w-px h-5 bg-slate-200" />
            {normal > 0 && <SummaryItem value={normal} label="Normal" dot="bg-emerald-500" color="text-emerald-700" />}
            {warning > 0 && <SummaryItem value={warning} label="Warning" dot="bg-amber-500" color="text-amber-700" />}
            {critical > 0 && <SummaryItem value={critical} label="Critical" dot="bg-red-500" color="text-red-700" pulse />}
            {alertCount > 0 && (
              <>
                <div className="w-px h-5 bg-slate-200" />
                <span className="text-xs font-semibold bg-red-50 text-red-700 border border-red-200 px-2.5 py-1 rounded-full">
                  {alertCount} alert{alertCount !== 1 ? 's' : ''}
                </span>
              </>
            )}
          </div>
        )}

        {/* Live indicator */}
        <div className="flex items-center gap-1.5 text-xs text-emerald-600 font-medium">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          Live
        </div>
      </div>
    </header>
  );
}

function SummaryItem({
  value, label, color, dot, pulse,
}: {
  value: number; label: string; color: string; dot?: string; pulse?: boolean;
}) {
  return (
    <div className="flex items-center gap-1.5">
      {dot && <span className={cn('w-2 h-2 rounded-full', dot, pulse && 'animate-pulse')} />}
      <span className={cn('text-sm font-bold tabular-nums', color)}>{value}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </div>
  );
}
