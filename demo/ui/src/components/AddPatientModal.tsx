import { useState } from 'react';
import { X, Play, ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getScenarioCatalog } from '../api/scenarios';
import { addPatient } from '../api/patients';
import { cn } from '../lib/utils';

interface AddPatientModalProps {
  currentCount: number;
  onClose: () => void;
}

const SPEEDS = [
  { label: '1×',   value: 1,   desc: 'Real time' },
  { label: '5×',   value: 5,   desc: '6× faster' },
  { label: '10×',  value: 10,  desc: '10× faster' },
  { label: '60×',  value: 60,  desc: '8 hr in 8 min' },
  { label: '120×', value: 120, desc: '8 hr in 4 min' },
];

export function AddPatientModal({ currentCount, onClose }: AddPatientModalProps) {
  const qc = useQueryClient();
  const nextNum = String(currentCount + 1).padStart(3, '0');

  const [label, setLabel]       = useState(`Patient ${nextNum}`);
  const [scenarioPath, setPath] = useState('');
  const [compression, setSpeed] = useState(10);
  const [error, setError]       = useState('');
  const [expandedIdx, setExpanded] = useState<number | null>(null);

  const { data: catalog, isLoading: catalogLoading } = useQuery({
    queryKey: ['scenarios-catalog'],
    queryFn: getScenarioCatalog,
    staleTime: 60_000,
  });

  const add = useMutation({
    mutationFn: () =>
      addPatient({ label: label.trim() || `Patient ${nextNum}`, scenario_path: scenarioPath, compression }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fleet'] });
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  const availableScenarios = catalog?.catalog.filter((s) => s.available) ?? [];
  const selectedScenario   = availableScenarios.find((s) => s.path === scenarioPath);
  const canSubmit          = scenarioPath !== '' && label.trim() !== '' && !add.isPending;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-slate-900/30 backdrop-blur-sm z-40"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          className="bg-white rounded-2xl shadow-2xl w-full max-w-md flex flex-col overflow-hidden"
          style={{ maxHeight: '90vh' }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-5 border-b border-slate-100">
            <div>
              <h2 className="text-base font-bold text-slate-900">Add Patient</h2>
              <p className="text-xs text-slate-500 mt-0.5">
                Start a new monitoring simulation
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg p-1.5 transition-colors"
            >
              <X size={18} />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto thin-scroll px-6 py-5 space-y-5">

            {/* Label */}
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1.5">
                Patient Label
              </label>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="e.g. Patient 004"
                className="w-full px-3 py-2.5 rounded-lg border border-slate-300 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
              />
            </div>

            {/* Scenario */}
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1.5">
                Scenario
              </label>
              {catalogLoading ? (
                <div className="flex items-center gap-2 text-sm text-slate-400 py-4">
                  <Loader2 size={14} className="animate-spin" />
                  Loading scenarios…
                </div>
              ) : (
                <div className="border border-slate-200 rounded-xl overflow-hidden divide-y divide-slate-100">
                  {availableScenarios.map((s, i) => {
                    const isSelected = s.path === scenarioPath;
                    const isExpanded = expandedIdx === i;
                    return (
                      <button
                        key={s.path}
                        type="button"
                        onClick={() => {
                          setPath(s.path);
                          setExpanded(isExpanded ? null : i);
                          setError('');
                        }}
                        className={cn(
                          'w-full text-left px-4 py-3 transition-colors',
                          isSelected
                            ? 'bg-blue-50 border-l-4 border-l-blue-500 pl-3.5'
                            : 'hover:bg-slate-50 border-l-4 border-l-transparent',
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span
                            className={cn(
                              'text-sm font-medium',
                              isSelected ? 'text-blue-800' : 'text-slate-800',
                            )}
                          >
                            {s.label}
                          </span>
                          {isExpanded
                            ? <ChevronUp size={14} className="text-slate-400 shrink-0" />
                            : <ChevronDown size={14} className="text-slate-400 shrink-0" />
                          }
                        </div>
                        {isExpanded && s.description && (
                          <p className="text-xs text-slate-500 mt-1.5 leading-snug pr-4">
                            {s.description}
                          </p>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
              {selectedScenario && (
                <p className="mt-2 text-xs text-blue-700 bg-blue-50 rounded-lg px-3 py-2 border border-blue-100">
                  {selectedScenario.description}
                </p>
              )}
            </div>

            {/* Speed */}
            <div>
              <label className="block text-xs font-semibold text-slate-700 mb-1.5">
                Simulation Speed
              </label>
              <div className="flex gap-2 flex-wrap">
                {SPEEDS.map((s) => (
                  <button
                    key={s.value}
                    type="button"
                    onClick={() => setSpeed(s.value)}
                    title={s.desc}
                    className={cn(
                      'flex-1 min-w-[52px] py-2 rounded-lg text-sm font-semibold border transition-all',
                      compression === s.value
                        ? 'bg-blue-600 text-white border-blue-600 shadow-sm'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-blue-400 hover:text-blue-600',
                    )}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-slate-400 mt-1.5">
                {SPEEDS.find((s) => s.value === compression)?.desc ?? ''}
                {' '}· Recommended: 10× for live demos
              </p>
            </div>

            {error && (
              <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                {error}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-between gap-3 bg-slate-50">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-200 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => add.mutate()}
              disabled={!canSubmit}
              className={cn(
                'flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold transition-all',
                'bg-blue-600 text-white hover:bg-blue-700 active:scale-95 shadow-sm',
                'disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100',
              )}
            >
              {add.isPending ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <Play size={15} />
              )}
              {add.isPending ? 'Starting…' : 'Start Monitoring'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
