import { useState } from 'react';
import { ShieldAlert, Trash2, Brain, Loader2 } from 'lucide-react';
import type { ClinicalAlert } from '../types';
import { getAlertExplanation } from '../api/patients';
import { clinicalMessage } from '../lib/clinical';
import { cn, formatTime } from '../lib/utils';

interface ClientAlertFeedProps {
  alerts: ClinicalAlert[];
  onClear: () => void;
}

function parseShap(conditions: string[]): { label: string; pct: number }[] {
  return conditions
    .map((c) => {
      const m = c.match(/^(.+?):\s*(\d+)%$/);
      return m ? { label: m[1].trim(), pct: parseInt(m[2], 10) } : null;
    })
    .filter((x): x is { label: string; pct: number } => x !== null);
}

function ShapBars({ contributions }: { contributions: { label: string; pct: number }[] }) {
  return (
    <div className="space-y-1.5 mb-3">
      <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wide">AI Drivers</span>
      {contributions.map(({ label, pct }) => (
        <div key={label} className="flex items-center gap-2">
          <span className="w-36 shrink-0 text-slate-600 truncate text-[10px]">{label}</span>
          <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-400 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-[10px] text-slate-500 tabular-nums w-7 text-right">{pct}%</span>
        </div>
      ))}
    </div>
  );
}

const SEV_CONFIG = {
  emergency: { dot: 'bg-red-600',   bg: 'bg-red-50',   border: 'border-l-red-600',   label: 'Critical' },
  critical:  { dot: 'bg-red-500',   bg: 'bg-red-50',   border: 'border-l-red-500',   label: 'Critical' },
  warning:   { dot: 'bg-amber-500', bg: 'bg-amber-50', border: 'border-l-amber-500', label: 'Warning'  },
  info:      { dot: 'bg-slate-400', bg: 'bg-slate-50', border: 'border-l-slate-400', label: 'Advisory' },
};

// ── Per-alert row with lazy inline explanation ────────────────────────────────

function ClientAlertRow({ alert }: { alert: ClinicalAlert }) {
  const cfg     = SEV_CONFIG[alert.severity] ?? SEV_CONFIG.info;
  const shap    = parseShap(alert.conditions_met ?? []);
  const nonShap = (alert.conditions_met ?? []).filter((c) => !/^\s*.+?:\s*\d+%\s*$/.test(c));
  const [explanation, setExplanation] = useState<string | null>(alert.explanation ?? null);
  const [loading, setLoading]         = useState(false);
  const [expanded, setExpanded]       = useState(false);

  const handleExpand = async () => {
    if (expanded) { setExpanded(false); return; }
    setExpanded(true);
    if (!explanation && !loading) {
      setLoading(true);
      try {
        const res = await getAlertExplanation(alert.id);
        setExplanation(res.explanation);
      } catch {
        // silently ignore
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <div className={cn('rounded-xl border-l-4 border border-slate-100', cfg.border, cfg.bg)}>
      <div className="flex items-start gap-3 px-4 py-3">
        <span className={cn('w-2 h-2 rounded-full mt-1 shrink-0', cfg.dot)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold text-slate-900">{alert.patient_label}</span>
            <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
              {cfg.label}
            </span>
          </div>
          <p className="text-sm text-slate-700 mt-0.5">{clinicalMessage(alert)}</p>
          {!expanded && (
            <button
              onClick={handleExpand}
              className="mt-1 flex items-center gap-1 text-[10px] text-blue-500 hover:text-blue-700 italic transition-colors"
            >
              <Brain size={10} />
              Tap to read clinical briefing
            </button>
          )}
        </div>
        <span className="text-[10px] text-slate-400 tabular-nums shrink-0 mt-0.5">
          {formatTime(alert.timestamp * 1000)}
        </span>
      </div>

      {/* Expanded — AI drivers + LLM explanation */}
      <div
        className={cn(
          'overflow-hidden transition-all duration-300',
          expanded ? 'max-h-[32rem]' : 'max-h-0',
        )}
      >
        <div className="px-4 pb-4 border-t border-white/60 pt-3 space-y-3">
          {/* Risk level badge */}
          {alert.risk_score > 0 && (
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-slate-400 font-medium uppercase tracking-wide">Risk Level</span>
              <span className={cn(
                'text-[10px] font-bold px-2 py-0.5 rounded-full',
                alert.risk_level === 'critical'
                  ? 'bg-red-100 text-red-700'
                  : alert.risk_level === 'high'
                  ? 'bg-orange-100 text-orange-700'
                  : 'bg-amber-100 text-amber-700',
              )}>
                {alert.risk_level
                  ? alert.risk_level.charAt(0).toUpperCase() + alert.risk_level.slice(1)
                  : 'Elevated'} Risk
              </span>
            </div>
          )}

          {/* SHAP bars — only when personalised AI alert */}
          {shap.length > 0 && <ShapBars contributions={shap} />}

          {/* Non-SHAP conditions (personal baseline text) */}
          {nonShap.length > 0 && (
            <p className="text-[10px] text-slate-500 font-mono leading-relaxed">
              {nonShap.join(' · ')}
            </p>
          )}

          {/* LLM clinical narrative */}
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Loader2 size={12} className="animate-spin" />
              Analysing patient data…
            </div>
          ) : explanation ? (
            <div>
              <div className="flex items-center gap-1.5 mb-1.5">
                <Brain size={11} className="text-blue-500" />
                <span className="text-[10px] font-semibold text-blue-600 uppercase tracking-wide">
                  Clinical Briefing
                </span>
              </div>
              <p className="text-xs text-slate-700 leading-relaxed italic">{explanation}</p>
            </div>
          ) : null}

          <button
            onClick={() => setExpanded(false)}
            className="text-[10px] text-slate-400 hover:text-slate-600 transition-colors"
          >
            Collapse
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Feed ──────────────────────────────────────────────────────────────────────

export function ClientAlertFeed({ alerts, onClear }: ClientAlertFeedProps) {
  return (
    <section className="bg-white border-t border-slate-100">
      <div className="max-w-screen-xl mx-auto px-6 py-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <ShieldAlert size={16} className="text-slate-500" />
            <h2 className="text-sm font-bold text-slate-800">Clinical Alerts</h2>
            {alerts.length > 0 && (
              <span className="bg-red-100 text-red-700 text-xs font-semibold px-2 py-0.5 rounded-full">
                {alerts.length}
              </span>
            )}
          </div>
          {alerts.length > 0 && (
            <button
              onClick={onClear}
              className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-red-500 transition-colors"
            >
              <Trash2 size={12} /> Dismiss all
            </button>
          )}
        </div>

        {alerts.length === 0 ? (
          <div className="text-center py-8">
            <ShieldAlert size={28} className="mx-auto mb-3 text-slate-200" />
            <p className="text-sm text-slate-500">No clinical alerts</p>
            <p className="text-xs text-slate-400 mt-1">All monitored patients are within normal parameters</p>
          </div>
        ) : (
          <div className="space-y-2 max-h-56 overflow-y-auto thin-scroll pr-1">
            {alerts.map((alert) => (
              <ClientAlertRow key={alert.id} alert={alert} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

