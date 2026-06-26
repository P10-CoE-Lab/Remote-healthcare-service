import { useState } from 'react';
import { Zap, Trash2, ChevronDown, Heart, Wind, Activity, BatteryLow, BatteryMedium, BatteryFull, WifiOff, ExternalLink, Brain } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { removePatient, triggerEvent } from '../api/patients';
import { usePatientVitals } from '../hooks/usePatientVitals';
import { RiskBadge } from './RiskBadge';
import { PhaseBadge } from './PhaseBadge';
import { VitalsSparkline } from './VitalsSparkline';
import type { PatientState } from '../types';
import {
  cn,
  hrColor,
  spo2Color,
  hrvColor,
  sparklineColor,
  statusDotClass,
  scenarioLabel,
  formatMinutes,
} from '../lib/utils';

interface PatientCardProps {
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

export function PatientCard({ patient }: PatientCardProps) {
  const qc = useQueryClient();
  const [eventsOpen, setEventsOpen]       = useState(false);
  const [triggeredEvent, setTriggeredEvent] = useState<string | null>(null);

  const remove = useMutation({
    mutationFn: () => removePatient(patient.patient_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fleet'] }),
  });

  const trigger = useMutation({
    mutationFn: (eventType: string) => triggerEvent(patient.patient_id, eventType),
    onSuccess: (_, eventType) => {
      setTriggeredEvent(eventType);
      setEventsOpen(false);
      setTimeout(() => setTriggeredEvent(null), 3000);
      qc.invalidateQueries({ queryKey: ['fleet'] });
    },
  });

  const vitals         = usePatientVitals(patient.device_id);
  const available_events = patient.available_events ?? [];
  const { risk_level, status, current_phase } = patient;
  const dotClass       = statusDotClass(status, risk_level);
  const lineColor      = sparklineColor(risk_level);
  const isStopped      = status !== 'running';
  const isComplete     = patient.progress_pct >= 100;
  const isDisconnected = patient.device_paused === true;

  const baselineActive  = patient.baseline_state === 'active';
  const bl              = patient.baseline_info ?? null;

  // Personalised sparkline threshold: mean + 2*std, else fall back to population 100
  const hrThreshold = baselineActive && bl
    ? Math.round(bl.hr_mean + 2 * bl.hr_std)
    : 100;

  // Normal range display helpers: mean ± 1.5*std
  const hrRange  = bl ? `${Math.round(bl.hr_mean - 1.5 * bl.hr_std)}–${Math.round(bl.hr_mean + 1.5 * bl.hr_std)}` : null;
  const spo2Range = bl ? `${(bl.spo2_mean - 1.5 * bl.spo2_std).toFixed(1)}–${(bl.spo2_mean + 1.5 * bl.spo2_std).toFixed(1)}` : null;
  const hrvRange  = bl ? `${Math.round(bl.hrv_mean - 1.5 * bl.hrv_std)}–${Math.round(bl.hrv_mean + 1.5 * bl.hrv_std)}` : null;

  const borderColor =
    risk_level === 'critical' ? 'border-red-200' :
    risk_level === 'high'     ? 'border-orange-200' :
    risk_level === 'medium'   ? 'border-amber-200' :
    'border-slate-200';

  return (
    <div
      className={cn(
        'bg-white rounded-xl border card-shadow transition-shadow duration-200 hover:card-shadow-hover',
        'flex flex-col overflow-hidden',
        isDisconnected ? 'border-slate-200' : borderColor,
        isStopped && !isDisconnected && 'opacity-60',
      )}
    >
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="px-5 pt-5 pb-3">
        {/* Row 1 — name/scenario + action icons */}
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={cn(
                'w-2.5 h-2.5 rounded-full shrink-0 mt-0.5',
                isDisconnected ? 'bg-slate-400' : dotClass,
                !isDisconnected && status === 'running' && 'animate-pulse-slow',
              )}
            />
            <div className="min-w-0">
              <h3 className="font-semibold text-slate-900 text-sm leading-tight truncate">
                {patient.label}
              </h3>
              <p className="text-xs text-slate-500 mt-0.5 truncate">
                {scenarioLabel(patient.scenario_id)}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0 ml-2">
            <a
              href={`http://localhost:3000/d/healthcare-detail/healthcare-patient-detail?var-device_id=${encodeURIComponent(patient.label)}&from=now-10m&to=now`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-400 hover:text-blue-500 transition-colors p-1 rounded-md hover:bg-blue-50"
              title="Open in Grafana"
            >
              <ExternalLink size={14} />
            </a>
            <button
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
              className="text-slate-400 hover:text-red-500 transition-colors p-1 rounded-md hover:bg-red-50"
              title="Remove patient"
            >
              <Trash2 size={14} />
            </button>
          </div>
        </div>

        {/* Row 2 — battery + risk badge + AI state pill */}
        <div className="flex items-center gap-2">
          <BatteryChip level={vitals.battery_level} />
          <RiskBadge level={risk_level} score={patient.risk_score} />
          {patient.baseline_state === 'learning' && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-slate-100 text-slate-500 animate-pulse-slow">
              Learning…
            </span>
          )}
          {baselineActive && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
              <Brain size={9} />
              AI Active
            </span>
          )}
        </div>
      </div>

      {/* ── Offline notice — inline, never blocks controls ──────── */}
      {isDisconnected && (
        <div className="mx-5 mb-1 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg flex items-center gap-2 text-xs text-amber-800">
          <WifiOff size={12} className="shrink-0 text-amber-500" />
          <span className="flex-1">Device offline — battery depleted</span>
          <span className="text-amber-500 text-[10px]">Trigger "restart device" below</span>
        </div>
      )}

      {/* ── Phase + Progress ────────────────────────────────────── */}
      <div className="px-5 pb-3">
        <div className="flex items-center justify-between mb-1.5">
          <PhaseBadge phase={current_phase} />
          <span className="text-xs text-slate-500 tabular-nums">
            {formatMinutes(patient.elapsed_sim_minutes)} /{' '}
            {Math.round(patient.total_sim_minutes)}m sim
          </span>
        </div>
        <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-1000',
              isComplete ? 'bg-slate-400' :
              risk_level === 'critical' ? 'bg-red-500' :
              risk_level === 'high'     ? 'bg-orange-500' :
              risk_level === 'medium'   ? 'bg-amber-500' :
              'bg-blue-500',
            )}
            style={{ width: `${Math.min(patient.progress_pct, 100)}%` }}
          />
        </div>
      </div>

      {/* ── Vitals — always visible; dimmed when disconnected ───── */}
      <div className={cn('px-5 pb-3 border-t border-slate-100 pt-3', isDisconnected && 'opacity-60')}>
        <div className="grid grid-cols-3 gap-2 mb-2">
          {/* Heart Rate */}
          <div className="text-center">
            <div className="flex items-center justify-center gap-1 mb-0.5">
              <Heart size={11} className="text-slate-400" />
              <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wide">HR</span>
            </div>
            <div className={cn('vital-number text-2xl', hrColor(vitals.heart_rate))}>
              {vitals.heart_rate !== null ? Math.round(vitals.heart_rate) : '—'}
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">bpm</div>
            {baselineActive && hrRange && (
              <div className="text-[9px] text-slate-400 mt-0.5 leading-tight">
                <span className="text-slate-300">↑ normal: </span>{hrRange}
              </div>
            )}
          </div>

          {/* SpO2 */}
          <div className="text-center">
            <div className="flex items-center justify-center gap-1 mb-0.5">
              <Wind size={11} className="text-slate-400" />
              <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wide">SpO₂</span>
            </div>
            <div className={cn('vital-number text-2xl', spo2Color(vitals.spo2))}>
              {vitals.spo2 !== null ? Math.round(vitals.spo2 * 10) / 10 : '—'}
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">%</div>
            {baselineActive && spo2Range && (
              <div className="text-[9px] text-slate-400 mt-0.5 leading-tight">
                <span className="text-slate-300">normal: </span>{spo2Range}%
              </div>
            )}
          </div>

          {/* HRV */}
          <div className="text-center">
            <div className="flex items-center justify-center gap-1 mb-0.5">
              <Activity size={11} className="text-slate-400" />
              <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wide">HRV</span>
            </div>
            <div className={cn('vital-number text-2xl', hrvColor(vitals.hrv))}>
              {vitals.hrv !== null ? Math.round(vitals.hrv) : '—'}
            </div>
            <div className="text-[10px] text-slate-400 mt-0.5">ms</div>
            {baselineActive && hrvRange && (
              <div className="text-[9px] text-slate-400 mt-0.5 leading-tight">
                <span className="text-slate-300">normal: </span>{hrvRange}ms
              </div>
            )}
          </div>
        </div>

        {/* Sparkline — Heart Rate trend */}
        <div className="mt-1">
          <p className="text-[10px] text-slate-400 mb-1">Heart Rate Trend</p>
          <VitalsSparkline
            data={vitals.heart_rate_history}
            color={lineColor}
            threshold={hrThreshold}
            unit="bpm"
          />
        </div>
      </div>

      {/* ── Actions ─────────────────────────────────────────────── */}
      <div className="px-5 pb-4 pt-2 border-t border-slate-100 mt-auto">
        {triggeredEvent && (
          <div className="mb-2 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-md px-2 py-1">
            ✓ Event "{triggeredEvent.replace(/_/g, ' ')}" triggered
          </div>
        )}
        <div className="flex items-center gap-2">
          {available_events.length > 0 && (
            <div className="relative flex-1">
              <button
                onClick={() => setEventsOpen((v) => !v)}
                className={cn(
                  'w-full flex items-center justify-between gap-1 px-3 py-1.5 rounded-lg text-xs font-medium',
                  'bg-blue-600 text-white hover:bg-blue-700 active:bg-blue-800 transition-colors',
                )}
              >
                <span className="flex items-center gap-1.5">
                  <Zap size={12} />
                  Trigger Event
                </span>
                <ChevronDown
                  size={12}
                  className={cn('transition-transform', eventsOpen && 'rotate-180')}
                />
              </button>

              {eventsOpen && (
                <div className="absolute bottom-full left-0 right-0 mb-1 bg-white border border-slate-200 rounded-xl shadow-lg z-20 overflow-hidden">
                  {available_events.map((ev) => (
                    <button
                      key={ev.type}
                      onClick={() => trigger.mutate(ev.type)}
                      disabled={trigger.isPending}
                      className="w-full text-left px-3 py-2.5 text-xs hover:bg-blue-50 transition-colors border-b border-slate-100 last:border-0"
                    >
                      <p className="font-medium text-slate-800">{ev.type.replace(/_/g, ' ')}</p>
                      {ev.description && (
                        <p className="text-slate-500 mt-0.5 text-[10px] leading-snug">{ev.description}</p>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {available_events.length === 0 && (
            <div className="flex-1 text-xs text-slate-400 text-center py-1.5">
              No events available
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
