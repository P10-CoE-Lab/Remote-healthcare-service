import { useState } from 'react';
import { Heart, Wind, Activity, BatteryLow, BatteryMedium, BatteryFull, WifiOff, ExternalLink, Brain } from 'lucide-react';
import type { PatientState } from '../types';
import { usePatientVitals } from '../hooks/usePatientVitals';
import { VitalsSparkline } from './VitalsSparkline';
import { SummaryModal } from './SummaryModal';
import { clinicalStatus, STATUS_STYLES } from '../lib/clinical';
import { cn, hrColor, spo2Color, hrvColor, sparklineColor } from '../lib/utils';

interface ClientPatientCardProps {
  patient: PatientState;
}

function BatteryChip({ level }: { level: number | null | undefined }) {
  if (level === null || level === undefined) return null;
  const Icon =
    level <= 20 ? BatteryLow :
    level <= 60 ? BatteryMedium :
    BatteryFull;
  const color =
    level <= 10 ? 'text-red-500' :
    level <= 20 ? 'text-amber-500' :
    'text-emerald-600';
  return (
    <div className={cn('flex items-center gap-0.5 text-[10px] font-medium tabular-nums', color)}>
      <Icon size={12} />
      <span>{Math.round(level)}%</span>
    </div>
  );
}

export function ClientPatientCard({ patient }: ClientPatientCardProps) {
  const [summaryOpen, setSummaryOpen] = useState(false);

  const vitals         = usePatientVitals(patient.device_id);
  const isDisconnected = patient.device_paused === true;
  const status         = clinicalStatus(patient.risk_level);
  const style          = STATUS_STYLES[status];
  const lineColor      = sparklineColor(patient.risk_level);
  const isStopped      = patient.status !== 'running';

  const baselineActive = patient.baseline_state === 'active';
  const bl             = patient.baseline_info ?? null;

  const hrThreshold = baselineActive && bl ? Math.round(bl.hr_mean + 2 * bl.hr_std) : 100;
  const hrRange     = bl ? `${Math.round(bl.hr_mean - 1.5 * bl.hr_std)}–${Math.round(bl.hr_mean + 1.5 * bl.hr_std)}` : null;
  const spo2Range   = bl ? `${(bl.spo2_mean - 1.5 * bl.spo2_std).toFixed(1)}–${(bl.spo2_mean + 1.5 * bl.spo2_std).toFixed(1)}` : null;
  const hrvRange    = bl ? `${Math.round(bl.hrv_mean - 1.5 * bl.hrv_std)}–${Math.round(bl.hrv_mean + 1.5 * bl.hrv_std)}` : null;

  return (
    <div
      className={cn(
        'bg-white rounded-2xl border shadow-sm flex flex-col overflow-hidden',
        isDisconnected ? 'border-slate-200' : style.border,
        isStopped && !isDisconnected && 'opacity-50',
      )}
    >
      {/* Status bar at top */}
      <div className={cn(
        'h-1 w-full',
        isDisconnected
          ? 'bg-slate-300'
          : style.dot.replace('bg-', 'bg-').replace('500', '400'),
      )} />

      {/* Header */}
      <div className="px-5 pt-5 pb-3">
        {/* Row 1 — name + Grafana link */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className={cn(
              'w-2.5 h-2.5 rounded-full shrink-0',
              isDisconnected ? 'bg-slate-300' : style.dot,
              !isDisconnected && status !== 'normal' && 'animate-pulse',
            )} />
            <h3 className="font-semibold text-slate-900 text-base truncate">{patient.label}</h3>
          </div>
          <a
            href={`http://localhost:3000/d/healthcare-detail/healthcare-patient-detail?var-device_id=${encodeURIComponent(patient.label)}&from=now-10m&to=now`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-slate-400 hover:text-blue-500 transition-colors p-1 rounded-md hover:bg-blue-50 shrink-0 ml-2"
            title="Open in Grafana"
          >
            <ExternalLink size={14} />
          </a>
        </div>

        {/* Row 2 — battery + status badge */}
        <div className="flex items-center gap-2">
          <BatteryChip level={vitals.battery_level} />
          {isDisconnected ? (
            <span className="flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-full border bg-slate-100 text-slate-500 border-slate-200">
              <WifiOff size={11} />
              Signal Lost
            </span>
          ) : baselineActive && status === 'normal' ? (
            <span className="flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-full border bg-teal-50 text-teal-700 border-teal-200">
              <Brain size={11} />
              AI Monitoring
            </span>
          ) : (
            <span className={cn(
              'flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full border',
              style.bg, style.text, style.border,
            )}>
              {baselineActive && <Brain size={10} />}
              {style.label}
            </span>
          )}
        </div>
      </div>

      {/* Disconnected notice — compact, doesn't hide anything */}
      {isDisconnected && (
        <div className="mx-5 mb-3 px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl flex items-center gap-2 text-xs text-slate-500">
          <WifiOff size={12} className="shrink-0 text-slate-400" />
          <span>Device disconnected — showing last recorded values</span>
        </div>
      )}

      {/* Vitals — always visible; dimmed when disconnected */}
      <div className={cn('px-5 pb-3', isDisconnected && 'opacity-60')}>
        <div className="grid grid-cols-3 gap-3 mb-3">
          <VitalTile
            icon={Heart}
            label="HR"
            value={vitals.heart_rate !== null ? Math.round(vitals.heart_rate) : null}
            unit="bpm"
            color={isDisconnected ? 'text-slate-400' : hrColor(vitals.heart_rate)}
            normalRange={baselineActive ? hrRange : null}
          />
          <VitalTile
            icon={Wind}
            label="SpO₂"
            value={vitals.spo2 !== null ? Math.round(vitals.spo2 * 10) / 10 : null}
            unit="%"
            color={isDisconnected ? 'text-slate-400' : spo2Color(vitals.spo2)}
            normalRange={baselineActive ? spo2Range : null}
          />
          <VitalTile
            icon={Activity}
            label="HRV"
            value={vitals.hrv !== null ? Math.round(vitals.hrv) : null}
            unit="ms"
            color={isDisconnected ? 'text-slate-400' : hrvColor(vitals.hrv)}
            normalRange={baselineActive ? hrvRange : null}
          />
        </div>

        <div>
          <p className="text-[10px] text-slate-400 mb-1 uppercase tracking-wide">
            {isDisconnected ? 'Last Recorded Trend' : 'Heart Rate Trend'}
          </p>
          <VitalsSparkline
            data={vitals.heart_rate_history}
            color={isDisconnected ? '#cbd5e1' : lineColor}
            threshold={hrThreshold}
            unit="bpm"
          />
        </div>
      </div>

      {/* AI Briefing button + watermark */}
      {baselineActive && (
        <div className="px-5 pb-4 flex items-center justify-between">
          <button
            onClick={() => setSummaryOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-blue-700 border border-blue-200 bg-blue-50 hover:bg-blue-100 transition-colors"
          >
            <Brain size={12} />
            AI Briefing
          </button>
          <span className="text-[9px] text-slate-300 select-none">AI Personalised</span>
        </div>
      )}

      {summaryOpen && (
        <SummaryModal patient={patient} onClose={() => setSummaryOpen(false)} />
      )}
    </div>
  );
}

function VitalTile({
  icon: Icon, label, value, unit, color, normalRange,
}: {
  icon: React.ElementType;
  label: string;
  value: number | null;
  unit: string;
  color: string;
  normalRange?: string | null;
}) {
  return (
    <div className="text-center bg-slate-50 rounded-xl py-4">
      <div className="flex items-center justify-center gap-1 mb-1.5">
        <Icon size={11} className="text-slate-400" />
        <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wide">{label}</span>
      </div>
      <div className={cn('font-bold text-2xl tabular-nums leading-none', color)}>
        {value !== null ? value : '—'}
      </div>
      <div className="text-[10px] text-slate-400 mt-1">{unit}</div>
      {normalRange && (
        <div className="text-[10px] text-slate-300 mt-1 leading-tight tabular-nums">
          {normalRange} {unit}
        </div>
      )}
    </div>
  );
}
