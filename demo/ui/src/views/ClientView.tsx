import { Users } from 'lucide-react';
import { useFleet } from '../hooks/useFleet';
import { useAlerts } from '../hooks/useAlerts';
import { ClientHeader } from '../components/ClientHeader';
import { ClientPatientCard } from '../components/ClientPatientCard';
import { ClientAlertFeed } from '../components/ClientAlertFeed';

export function ClientView() {
  const { data: fleetData } = useFleet();
  const { alerts, clearAlerts } = useAlerts();

  const patients = fleetData?.patients ?? [];

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <ClientHeader patients={patients} alertCount={alerts.length} />

      <main className="flex-1 max-w-screen-xl mx-auto w-full px-6 py-6">
        {patients.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-32 text-center">
            <div className="w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mb-5">
              <Users size={30} className="text-blue-300" />
            </div>
            <p className="text-slate-500 font-medium">No patients currently monitored</p>
            <p className="text-xs text-slate-400 mt-1">Waiting for patients to be added to the system</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {patients.map((p) => (
              <ClientPatientCard key={p.patient_id} patient={p} />
            ))}
          </div>
        )}
      </main>

      <ClientAlertFeed alerts={alerts} onClear={clearAlerts} />
    </div>
  );
}
