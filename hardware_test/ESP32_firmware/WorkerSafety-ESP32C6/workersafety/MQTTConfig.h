#ifndef MQTT_CONFIG_H
#define MQTT_CONFIG_H
#define ENABLE_WIFI

// WiFi
#define WIFI_SSID       "YOUR_WIFI_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// MQTT Broker
#define MQTT_SERVER     "192.168.0.156"
#define MQTT_PORT       1883

// Credentials
#define MQTT_USERNAME   ""
#define MQTT_PASSWORD   ""

// Client
#define MQTT_CLIENT_ID  "WS001"

// Topics
#define MQTT_STATUS_TOPIC    "worker_status"

// ─── Canonical iots/... contract (Remote Healthcare POC integration) ───────
// See HANDOFF_wrist_unit_healthcare_integration.md. This is a *different*
// device identity from MQTT_CLIENT_ID above (which stays as the raw
// worker_status publisher/connection id) — IOTS_DEVICE_ID is what appears
// in the canonical per-sensor payload's device_id field and the retained
// config/{device}/patient_label topic. Both worker_status and iots/...
// publish from the same tick; neither replaces the other.
#define IOTS_DEVICE_ID       "hw-wrist-01"
#define IOTS_PERSONA_ID      "generic_wearable_patient"
#define IOTS_POC_TYPE        "healthcare"
#define IOTS_MQTT_PREFIX     "iots"

// Thresholds mirrored from personas/generic_wearable_patient.yaml — keep
// in sync manually. Firmware doesn't parse YAML at runtime; same accepted
// tradeoff as cardiac_patient_sim.ino.
#define IOTS_THRESH_HR_HIGH   100.0f
#define IOTS_THRESH_HR_LOW    50.0f
#define IOTS_THRESH_SPO2_LOW  93.0f
#define IOTS_THRESH_HRV_LOW   20.0f

#endif