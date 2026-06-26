import { useState } from 'react';
import { WifiOff, Settings } from 'lucide-react';
import { useFleet } from '../hooks/useFleet';
import { useAlerts } from '../hooks/useAlerts';
import { FleetHeader } from '../components/FleetHeader';
import { FleetView } from '../components/FleetView';
import { AddPatientModal } from '../components/AddPatientModal';
import { AlertFeed } from '../components/AlertFeed';

export function OperatorView() {
  const [modalOpen, setModalOpen] = useState(false);
  const { data: fleetData, isFetching, isError } = useFleet();
  const { alerts, clearAlerts } = useAlerts();

  const patients = fleetData?.patients ?? [];

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <FleetHeader
        patients={patients}
        isLoading={isFetching}
        onAddPatient={() => setModalOpen(true)}
        alertCount={alerts.length}
      />

      {/* Operator mode banner */}
      <div className="bg-slate-800 text-slate-300 text-xs px-6 py-1.5 flex items-center gap-2">
        <Settings size={11} />
        <span className="font-medium">Operator Mode</span>
        <span className="text-slate-500">·</span>
        <span>Client monitoring view at <a href="/" className="text-blue-400 hover:text-blue-300 underline">/</a></span>
      </div>

      {isError && (
        <div className="bg-amber-50 border-b border-amber-200 px-6 py-2.5 flex items-center gap-2 text-sm text-amber-800">
          <WifiOff size={15} className="shrink-0" />
          <span>Cannot reach the simulator API — retrying…</span>
        </div>
      )}

      <main className="flex-1">
        <FleetView patients={patients} onAddPatient={() => setModalOpen(true)} />
      </main>

      <AlertFeed alerts={alerts} onClear={clearAlerts} />

      {modalOpen && (
        <AddPatientModal
          currentCount={patients.length}
          onClose={() => setModalOpen(false)}
        />
      )}
    </div>
  );
}
