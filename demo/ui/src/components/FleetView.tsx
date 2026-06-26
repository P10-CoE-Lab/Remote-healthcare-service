import { Users, Plus } from 'lucide-react';
import type { PatientState } from '../types';
import { PatientCard } from './PatientCard';

interface FleetViewProps {
  patients: PatientState[];
  onAddPatient: () => void;
}

export function FleetView({ patients, onAddPatient }: FleetViewProps) {
  if (patients.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
        <div className="w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mb-5">
          <Users size={32} className="text-blue-400" />
        </div>
        <h2 className="text-lg font-semibold text-slate-700 mb-2">
          No patients being monitored
        </h2>
        <p className="text-sm text-slate-500 max-w-xs mb-6">
          Add a patient to begin live simulation. Each patient runs an independent
          healthcare scenario with real-time vital signs.
        </p>
        <button
          onClick={onAddPatient}
          className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 text-white text-sm font-semibold rounded-xl hover:bg-blue-700 active:scale-95 transition-all shadow-sm"
        >
          <Plus size={16} strokeWidth={2.5} />
          Add Your First Patient
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-screen-xl mx-auto px-6 py-6">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {patients.map((patient) => (
          <PatientCard key={patient.patient_id} patient={patient} />
        ))}
      </div>
    </div>
  );
}
