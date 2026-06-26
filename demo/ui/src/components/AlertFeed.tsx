import { useState } from 'react';
import { ShieldAlert, Smartphone, Cloud, Trash2, ChevronDown, ChevronUp } from 'lucide-react';
import type { ClinicalAlert, AlertSource } from '../types';
import { cn, formatTime } from '../lib/utils';

interface AlertFeedProps {
  alerts: ClinicalAlert[];
  onClear: () => void;
}

// ── Source config ────────────────────────────────────────────────────────────

const SOURCE_CONFIG: Record<AlertSource, {
  label: string;
  detail: string;
  icon: React.ElementType;
  badge: string;
  border: string;
  bg: string;
}> = {
  edge: {
    label:  'On-Device',
    detail: 'Detected immediately by the wearable — no sustained window required. Fires in under 1 second.',
    icon:   Smartphone,
    badge:  'bg-violet-100 text-violet-800 border-violet-200',
    border: 'border-l-violet-500',
    bg:     'bg-violet-50/40',
  },
  cloud: {
    label:  'Remote Engine',
    detail: 'Sustained pattern confirmed by the cloud rule engine over a time window — not possible on a hardware-constrained wearable.',
    icon:   Cloud,
    badge:  'bg-blue-100 text-blue-800 border-blue-200',
    border: 'border-l-blue-500',
    bg:     'bg-blue-50/40',
  },
};

// ── Severity ─────────────────────────────────────────────────────────────────

const SEVERITY_DOT: Record<string, string> = {
  emergency: 'bg-violet-600',
  critical:  'bg-red-500',
  warning:   'bg-amber-500',
  info:      'bg-slate-400',
};

// ── Filter tabs ───────────────────────────────────────────────────────────────

type Filter = 'all' | AlertSource;

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all',   label: 'All Alerts' },
  { value: 'edge',  label: 'On-Device' },
  { value: 'cloud', label: 'Remote Engine' },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatReading(sensor: string, value: number | null, threshold: number | null): string {
  const label = sensor.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const parts = [label];
  if (value     !== null) parts.push(String(Math.round(value * 10) / 10));
  if (threshold !== null) parts.push(`threshold ${threshold}`);
  return parts.join(' · ');
}

// ── Component ─────────────────────────────────────────────────────────────────

export function AlertFeed({ alerts, onClear }: AlertFeedProps) {
  const [filter, setFilter]     = useState<Filter>('all');
  const [expandedId, setExpanded] = useState<string | null>(null);

  const visible      = filter === 'all' ? alerts : alerts.filter((a) => a.source === filter);
  const edgeCount    = alerts.filter((a) => a.source === 'edge').length;
  const cloudCount   = alerts.filter((a) => a.source === 'cloud').length;

  return (
    <section className="bg-white border-t border-slate-200">
      <div className="max-w-screen-xl mx-auto px-6 py-5">

        {/* ── Header ─────────────────────────────────────────── */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <ShieldAlert size={16} className="text-slate-500" />
              <h2 className="text-sm font-bold text-slate-800">Clinical Alert Panel</h2>
            </div>

            {/* Source pills */}
            {edgeCount > 0 && (
              <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-semibold bg-violet-100 text-violet-800 border-violet-200">
                <Smartphone size={9} /> On-Device · {edgeCount}
              </span>
            )}
            {cloudCount > 0 && (
              <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-semibold bg-blue-100 text-blue-800 border-blue-200">
                <Cloud size={9} /> Remote · {cloudCount}
              </span>
            )}
          </div>

          {alerts.length > 0 && (
            <button
              onClick={onClear}
              className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-red-500 transition-colors"
            >
              <Trash2 size={12} /> Clear
            </button>
          )}
        </div>

        {/* ── Filter tabs ────────────────────────────────────── */}
        <div className="flex items-center gap-1 mb-3">
          {FILTERS.map((f) => {
            const count = f.value === 'all' ? alerts.length
              : f.value === 'edge' ? edgeCount : cloudCount;
            return (
              <button
                key={f.value}
                onClick={() => setFilter(f.value)}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-medium transition-all',
                  filter === f.value
                    ? 'bg-slate-800 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200',
                )}
              >
                {f.label}
                {count > 0 && (
                  <span className={cn(
                    'text-[10px] px-1.5 py-0.5 rounded-full font-semibold',
                    filter === f.value ? 'bg-white/20 text-white' : 'bg-slate-200 text-slate-600',
                  )}>
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* ── Alert list ─────────────────────────────────────── */}
        {visible.length === 0 ? (
          <EmptyState filter={filter} />
        ) : (
          <div className="space-y-2 max-h-72 overflow-y-auto thin-scroll pr-1">
            {visible.map((alert) => {
              const src      = SOURCE_CONFIG[alert.source] ?? SOURCE_CONFIG.cloud;
              const Icon     = src.icon;
              const expanded = expandedId === alert.id;

              return (
                <div
                  key={alert.id}
                  className={cn(
                    'rounded-xl border border-slate-100 border-l-4 transition-shadow',
                    src.border,
                    src.bg,
                  )}
                >
                  <button
                    className="w-full text-left px-4 py-3 flex items-start gap-3"
                    onClick={() => setExpanded(expanded ? null : alert.id)}
                  >
                    {/* Severity dot */}
                    <span
                      className={cn(
                        'w-2 h-2 rounded-full mt-1.5 shrink-0',
                        SEVERITY_DOT[alert.severity] ?? SEVERITY_DOT.info,
                      )}
                    />

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold text-slate-900">
                          {alert.patient_label}
                        </span>
                        <code className="text-xs bg-white border border-slate-200 text-slate-600 px-1.5 py-0.5 rounded font-mono">
                          {alert.rule_id}
                        </code>
                        <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-semibold', src.badge)}>
                          <Icon size={9} />
                          {src.label}
                        </span>
                      </div>

                      <p className="text-xs text-slate-600 mt-0.5 leading-snug">
                        {alert.rule_name}
                      </p>

                      {alert.sensor_name && (
                        <p className="text-xs text-slate-500 mt-0.5 font-mono">
                          {formatReading(alert.sensor_name, alert.sensor_value, alert.threshold)}
                        </p>
                      )}
                    </div>

                    {/* Right column */}
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <span className="text-[10px] text-slate-400 tabular-nums">
                        {formatTime(alert.timestamp * 1000)}
                      </span>
                      {alert.risk_score > 0 && (
                        <span className="text-[10px] font-bold text-red-600">
                          Risk {Math.round(alert.risk_score)}
                        </span>
                      )}
                      {expanded
                        ? <ChevronUp size={12} className="text-slate-400" />
                        : <ChevronDown size={12} className="text-slate-400" />
                      }
                    </div>
                  </button>

                  {/* Expanded detail */}
                  {expanded && <AlertDetail alert={alert} src={src} />}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-3 text-xs">
      <span className="text-slate-400 shrink-0 w-20">{label}</span>
      <span className={cn('text-slate-700 leading-snug', mono && 'font-mono')}>{value}</span>
    </div>
  );
}

function AlertDetail({
  alert,
  src,
}: {
  alert: ClinicalAlert;
  src: (typeof SOURCE_CONFIG)[AlertSource];
}) {
  return (
    <div className="px-4 pb-4 pt-0 border-t border-white/60">
      <div className="bg-white/80 rounded-lg p-3 mt-1">
        <DetailRow label="Detection" value={src.detail} />
        {alert.sensor_name && (
          <div className="mt-2">
            <DetailRow
              label="Sensor"
              value={`${alert.sensor_name.replace(/_/g, ' ')} · ${alert.sensor_value !== null ? Math.round((alert.sensor_value ?? 0) * 10) / 10 : '—'}${alert.threshold !== null ? ` (threshold ${alert.threshold})` : ''}`}
              mono
            />
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState({ filter }: { filter: Filter }) {
  return (
    <div className="text-center py-10">
      <ShieldAlert size={28} className="mx-auto mb-3 text-slate-200" />
      <p className="text-sm font-medium text-slate-500">
        {filter === 'all'
          ? 'No clinical alerts yet'
          : filter === 'edge'
          ? 'No on-device alerts yet'
          : 'No remote engine alerts yet'}
      </p>
      <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto leading-relaxed">
        {filter === 'all' || filter === 'edge'
          ? 'On-device alerts fire the moment a vital sign crosses a threshold on the wearable.'
          : 'Remote alerts fire after a sustained abnormal pattern is confirmed by the cloud engine.'}
      </p>
      <div className="mt-5 flex justify-center gap-6 text-xs text-slate-400">
        <span className="flex items-center gap-1.5">
          <Smartphone size={12} className="text-violet-400" />
          On-Device · immediate thresholds
        </span>
        <span className="flex items-center gap-1.5">
          <Cloud size={12} className="text-blue-400" />
          Remote · sustained patterns
        </span>
      </div>
    </div>
  );
}
