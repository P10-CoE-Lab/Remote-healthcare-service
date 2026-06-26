import { useState, useEffect } from 'react';
import { X, Brain, RefreshCw, Loader2 } from 'lucide-react';
import { getPatientSummary } from '../api/patients';
import type { PatientState } from '../types';
import { cn } from '../lib/utils';

interface SummaryModalProps {
  patient: PatientState;
  onClose: () => void;
}

/** Parse sections from the LLM output using **Bold** headers. */
function parseSections(text: string): { heading: string; body: string }[] {
  const sections: { heading: string; body: string }[] = [];
  const parts = text.split(/\*\*(.+?)\*\*/g);
  // parts is [pre, heading1, body1, heading2, body2, ...]
  for (let i = 1; i < parts.length - 1; i += 2) {
    sections.push({
      heading: parts[i].replace(/:$/, '').trim(),
      body:    parts[i + 1].trim(),
    });
  }
  if (sections.length === 0) {
    sections.push({ heading: '', body: text });
  }
  return sections;
}

export function SummaryModal({ patient, onClose }: SummaryModalProps) {
  const [summary, setSummary]     = useState<string | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string>('');
  const [baselinePersonalised, setBaselinePersonalised] = useState(false);

  const generate = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getPatientSummary(patient.patient_id);
      setSummary(res.summary);
      setGeneratedAt(res.generated_at ? new Date(res.generated_at).toLocaleTimeString() : '');
      setBaselinePersonalised(res.baseline_personalised);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to generate summary');
    } finally {
      setLoading(false);
    }
  };

  // Auto-generate on mount
  useEffect(() => { generate(); }, []);

  const sections = summary ? parseSections(summary) : [];
  const bl = patient.baseline_info;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-xl mx-4 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-start justify-between px-6 pt-5 pb-4 border-b border-slate-100">
          <div>
            <div className="flex items-center gap-2">
              <Brain size={18} className="text-blue-600" />
              <h2 className="font-semibold text-slate-900 text-base">
                Patient Briefing: {patient.label}
              </h2>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              AI-generated clinical summary
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 p-1 rounded-lg hover:bg-slate-100 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {loading && (
            <div className="flex flex-col items-center justify-center py-12 text-slate-500">
              <Loader2 size={32} className="animate-spin mb-3 text-blue-500" />
              <p className="text-sm">Analysing patient trend…</p>
            </div>
          )}

          {error && !loading && (
            <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && sections.length > 0 && (
            <div className="space-y-5">
              {sections.map(({ heading, body }) => (
                <div key={heading}>
                  {heading && (
                    <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">
                      {heading}
                    </h3>
                  )}
                  <p
                    className={cn(
                      'text-sm text-slate-700 leading-relaxed',
                      heading === 'Recommendation' && 'font-medium text-slate-900',
                    )}
                  >
                    {body}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-between gap-3">
          <div className="text-[10px] text-slate-400 leading-relaxed">
            {baselinePersonalised && bl ? (
              <>
                Personalised baselines:{' '}
                <span className="font-medium text-slate-500">
                  HR {bl.hr_mean.toFixed(0)}±{bl.hr_std.toFixed(1)} bpm
                  {' · '}
                  SpO₂ {bl.spo2_mean.toFixed(1)}%
                </span>
                {generatedAt && <> &nbsp;·&nbsp; Generated at {generatedAt}</>}
              </>
            ) : (
              generatedAt && <>Generated at {generatedAt}</>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={generate}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
              Regenerate
            </button>
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-medium bg-slate-800 text-white rounded-lg hover:bg-slate-700 transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
