export type RiskLevel = 'none' | 'low' | 'medium' | 'high' | 'critical';
export type PatientStatus = 'running' | 'idle' | 'stopped' | 'error';
export type AlertSeverity = 'info' | 'warning' | 'critical' | 'emergency';
export type AlertSource = 'edge' | 'cloud';
export type PatientSource = 'simulated' | 'hardware';

export interface VitalsReading {
  timestamp: number;
  value: number;
}

export interface PatientVitals {
  heart_rate: number | null;
  spo2: number | null;
  hrv: number | null;
  heart_rate_history: VitalsReading[];
  spo2_history: VitalsReading[];
  battery_level?: number | null;
}

export interface AvailableEvent {
  type: string;
  description: string;
}

export interface BaselineInfo {
  hr_mean: number;
  hr_std: number;
  spo2_mean: number;
  spo2_std: number;
  hrv_mean: number;
  hrv_std: number;
  samples_used: number;
}

export interface PatientState {
  patient_id: string;
  device_id: string;
  label: string;
  status: PatientStatus;
  source: PatientSource;
  scenario_id: string;
  persona_id: string;
  current_phase: string;
  elapsed_sim_minutes: number;
  total_sim_minutes: number;
  progress_pct: number;
  compression: number;
  available_events: AvailableEvent[];
  risk_score: number;
  risk_level: RiskLevel;
  alert_count: number;
  device_paused?: boolean;
  baseline_state?: 'learning' | 'active';
  baseline_info?: BaselineInfo | null;
}

export interface FleetResponse {
  patients: PatientState[];
  total: number;
}

export interface ScenarioCatalogEntry {
  label: string;
  path: string;
  description: string;
  available: boolean;
}

export interface CatalogResponse {
  catalog: ScenarioCatalogEntry[];
}

export interface PersonasResponse {
  personas: string[];
}

export interface AddPatientPayload {
  label: string;
  source?: PatientSource;
  scenario_path?: string;
  compression?: number;
  device_id?: string;
  persona_id?: string;
}

export interface AddPatientResponse {
  success: boolean;
  patient_id: string;
  label: string;
  message: string;
}

/** A clinical alert returned by GET /alerts */
export interface ClinicalAlert {
  id: string;
  timestamp: number;
  timestamp_utc: string;
  patient_id: string;
  patient_label: string;
  persona_id: string;
  rule_id: string;
  rule_name: string;
  severity: AlertSeverity;
  source: AlertSource;
  sensor_name: string;
  sensor_value: number | null;
  threshold: number | null;
  risk_score: number;
  risk_level: RiskLevel;
  conditions_met: string[];
  explanation?: string | null;
  shap_contributions?: string[];
}

export interface AlertsResponse {
  alerts: ClinicalAlert[];
  total: number;
}
