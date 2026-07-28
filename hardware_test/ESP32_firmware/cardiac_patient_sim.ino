/**
 * Cardiac Patient Simulator Firmware — ESP32 (any variant, WiFi only)
 *
 * Phase 1 of the hardware integration plan (see
 * HANDOFF_esp32_healthcare_simulation.md at repo root). No physical
 * sensors are read here — the device plays back the same scripted
 * `tachycardia_episode` scenario the Python simulator would run for
 * the `cardiac_patient` persona, publishing to the exact same MQTT
 * topic/payload contract (simulator/transport/mqtt_publisher.py), so
 * downstream consumers (Telegraf, rule engine, Grafana, and later the
 * operator UI) cannot tell this device apart from a Python-simulated
 * patient.
 *
 * Reuses the WiFi/MQTT/JSON/millis()-scheduling boilerplate shape from
 * belt_unit.ino — same pattern, different payload content, no IMU/HR
 * sensor hardware required.
 *
 * v2 changes (post Phase-2 integration RCA):
 *  - Publishes heart_rate_variability too, matching
 *    scenarios/health/tachycardia_episode.yaml's phase ranges exactly —
 *    the cloud PersonalisedAnalyzer requires all three sensors (HR, SpO2,
 *    HRV) present before it will ever leave "learning", so a device
 *    missing HRV could never get a personalised baseline or AI Briefing.
 *  - patient_label is no longer a hardcoded constant. It's read from a
 *    retained MQTT config topic the demo API publishes to on hardware
 *    patient registration, so the operator's chosen name actually shows
 *    up in Grafana instead of a fixed placeholder string.
 *  - Per-tick Serial output is throttled and WiFi.RSSI() is logged every
 *    tick, to help correlate reported MQTT disconnects with either a
 *    blocked main loop (native USB CDC Serial writes can stall on this
 *    board if nothing is draining the port) or genuine RF instability.
 *
 * Libraries (Arduino Library Manager): PubSubClient, ArduinoJson
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <time.h>

// ─── Network ──────────────────────────────────────────────────────────────────
// Topic pattern: {MQTT_PREFIX}/{POC_TYPE}/{PERSONA_ID}/{sensor_name}
// Matches simulator/transport/mqtt_publisher.py exactly — no device_id in the
// topic, multiple devices sharing a persona publish to the same topic and are
// told apart via the device_id field inside the JSON payload.
#define DEVICE_ID       "hw-cardiac-01"
#define PERSONA_ID      "cardiac_patient"
#define POC_TYPE        "healthcare"
#define MQTT_PREFIX     "iots"

// Same test bench network/broker as belt_unit.ino — update if this device
// is on a different network or the broker host's LAN IP has changed.
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_BROKER   = "192.168.0.156";
const int   MQTT_PORT     = 1883;
const char* MQTT_USER     = "";
const char* MQTT_PASS     = "";

// ─── Scenario: tachycardia_episode (30 simulated minutes, 10x compression) ────
#define COMPRESSION          10.0f
#define TOTAL_SIM_MINUTES    30.0f
#define PUBLISH_INTERVAL_MS  1000   // single shared interval for v1

struct Phase {
  const char* name;
  float startMin, endMin;
  float hrMin, hrMax;
  const char* hrBehavior;
  float spo2Min, spo2Max;
  const char* spo2Behavior;
  float hrvMin, hrvMax;
  const char* hrvBehavior;
};

// HR/SpO2/HRV ranges and behaviors ported verbatim from
// scenarios/health/tachycardia_episode.yaml — do not hand-tune these
// independently of that file, or hardware and simulated patients will
// tell visibly different stories for the "same" scenario.
Phase PHASES[] = {
  { "resting_normal", 0,  12, 60,  72,  "oscillating_stable", 97, 99, "oscillating_stable", 40, 62, "oscillating_stable" },
  { "onset",          12, 16, 75,  105, "monotonic_rising",   96, 98, "oscillating_stable", 25, 45, "monotonic_falling"  },
  { "tachycardia",    16, 24, 118, 145, "mean_reverting",     93, 96, "oscillating_stable", 15, 28, "oscillating_stable" },
  { "recovery",       24, 30, 70,  85,  "monotonic_falling",  96, 99, "monotonic_rising",   30, 55, "monotonic_rising"   },
};
const int PHASE_COUNT = 4;

// Persona thresholds (cardiac_patient) — v1 simplification: normal/warning
// only, no critical tier and no warning/critical fraction scaling.
#define THRESH_HR_HIGH   100.0f
#define THRESH_HR_LOW    50.0f
#define THRESH_SPO2_LOW  93.0f
#define THRESH_HRV_LOW   20.0f

// Serial output throttling — the verbose per-tick reading line is heavy
// (this board's native USB CDC Serial can stall the whole loop() if
// nothing drains the port); RSSI is logged every tick regardless since
// it's cheap and lets a disconnect be correlated against signal strength.
#define VERBOSE_PRINT_EVERY_N_TICKS 5

// ─── Battery — cosmetic only (see simulator/sensors/battery_sensor.py for the ──
// Python-side sensor this mirrors). Drains in real wall-clock time, independent
// of the scenario clock/compression. No battery_dead/restart_device mechanic on
// hardware — it floors out instead of ever reaching 0%, so a bench unit left
// running for hours never shows a permanently "dead" card with no way to clear it.
#define BATTERY_INITIAL_LEVEL       85.0f
#define BATTERY_DRAIN_RATE_PER_SEC  0.003f   // ~10.8%/hr, matches BatterySensor
#define BATTERY_FLOOR               15.0f
#define BATTERY_PUBLISH_INTERVAL_MS 10000    // battery changes slowly — publish less often

// ─── Objects ──────────────────────────────────────────────────────────────────
WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

unsigned long simStartMs    = 0;
unsigned long lastPublishMs = 0;
int  currentPhaseIdx        = -1;
long hrSeq                  = 0;
long spo2Seq                 = 0;
long hrvSeq                  = 0;
long batterySeq              = 0;
uint32_t tickCounter         = 0;

float         batteryLevel        = BATTERY_INITIAL_LEVEL;
unsigned long lastBatteryPublishMs = 0;
unsigned long lastBatteryTickMs    = 0;  // real millis() — drain is wall-clock, not scenario time

// Mutable — updated live from the retained config/{DEVICE_ID}/patient_label
// MQTT topic (see mqttCallback()). Starts as a neutral placeholder so a
// freshly-flashed, not-yet-registered device never displays a stale name.
char patientLabel[64] = "Unregistered Device";
char configTopic[80];

// Mean-reverting generator state — reset whenever the phase changes.
float hrMeanRevertCurrent   = NAN;
float spo2MeanRevertCurrent = NAN;
float hrvMeanRevertCurrent  = NAN;

// ─── WiFi / MQTT (same pattern as belt_unit.ino) ──────────────────────────────
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi connecting");
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries++ < 20) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("WiFi OK: %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("WiFi failed — will retry in loop");
}

void reconnectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  static unsigned long lastWifiAttemptMs = 0;
  if (millis() - lastWifiAttemptMs < 10000) return;
  lastWifiAttemptMs = millis();
  Serial.printf("[WiFi] Not connected (status=%d), retrying SSID: %s\n",
                WiFi.status(), WIFI_SSID);
  WiFi.disconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

// Called by PubSubClient whenever a message arrives on a topic we've
// subscribed to. Only used for the retained patient_label config topic.
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  size_t len = length < sizeof(patientLabel) - 1 ? length : sizeof(patientLabel) - 1;
  memcpy(patientLabel, payload, len);
  patientLabel[len] = '\0';
  Serial.printf("[CONFIG] patient_label -> \"%s\"\n", patientLabel);
}

void reconnectMQTT() {
  static unsigned long lastAttemptMs = 0;
  if (millis() - lastAttemptMs < 5000) return;
  lastAttemptMs = millis();

  Serial.printf("[NET] WiFi=%s(%d) RSSI=%ddBm IP=%s MQTT=%s\n",
    WiFi.status() == WL_CONNECTED ? "OK" : "NO",
    WiFi.status(),
    WiFi.RSSI(),
    WiFi.localIP().toString().c_str(),
    mqtt.connected() ? "OK" : "NO");

  if (mqtt.connected() || WiFi.status() != WL_CONNECTED) return;

  Serial.printf("[MQTT] Connecting to %s:%d ... ", MQTT_BROKER, MQTT_PORT);
  bool ok = strlen(MQTT_USER) > 0
    ? mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)
    : mqtt.connect(DEVICE_ID);

  if (ok) {
    Serial.println("OK");
    mqtt.subscribe(configTopic);   // resubscribe — PubSubClient sessions aren't persistent
  } else {
    Serial.printf("FAILED state=%d\n", mqtt.state());
  }
}

// ─── Time (NTP, for timestamp_utc) ─────────────────────────────────────────────
void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("Waiting for NTP time sync");
  time_t now = time(nullptr);
  int tries = 0;
  while (now < 8 * 3600 * 2 && tries++ < 20) {
    delay(500);
    Serial.print(".");
    now = time(nullptr);
  }
  Serial.println();
  // Demo-safe: if NTP never syncs, keep running with epoch-based timestamps
  // rather than blocking or crashing — Mosquitto/dashboard don't care.
  Serial.println(now > 8 * 3600 * 2 ? "NTP OK" : "NTP failed — continuing anyway");
}

void isoTimestamp(char* buf, size_t len) {
  time_t now = time(nullptr);
  struct tm tmStruct;
  gmtime_r(&now, &tmStruct);
  char base[32];
  strftime(base, sizeof(base), "%Y-%m-%dT%H:%M:%S", &tmStruct);
  snprintf(buf, len, "%s.000+00:00", base);
}

// ─── Signal generators (ported from simulator/engine/signal.py) ──────────────
float frand(float lo, float hi) {
  return lo + (hi - lo) * ((float)random(0, 10001) / 10000.0f);
}

float gaussRand(float mean, float stdev) {
  float u1 = (float)random(1, 10000) / 10000.0f;  // avoid log(0)
  float u2 = (float)random(0, 10000) / 10000.0f;
  float z0 = sqrt(-2.0f * log(u1)) * cos(2.0f * PI * u2);
  return mean + z0 * stdev;
}

float genOscillatingStable(float mn, float mx) {
  float range = mx - mn;
  float mid   = (mn + mx) / 2.0f;
  float amp   = 0.05f * range;  // oscillating_amplitude_fraction default
  return mid + frand(-amp, amp);
}

float genMonotonicRising(float mn, float mx, float progress) {
  float range  = mx - mn;
  float trend  = mn + range * progress;
  float jitter = frand(-0.01f, 0.01f) * range;
  return trend + jitter;
}

float genMonotonicFalling(float mn, float mx, float progress) {
  float range  = mx - mn;
  float trend  = mx - range * progress;
  float jitter = frand(-0.01f, 0.01f) * range;
  return trend + jitter;
}

float genMeanReverting(float mn, float mx, float& state) {
  float range = mx - mn;
  float mid   = (mn + mx) / 2.0f;
  if (isnan(state)) state = mid;

  float reversion = 0.15f * (mid - state);          // reversion_speed default
  float noise     = gaussRand(0.0f, 0.03f * range);  // volatility default
  state = state + reversion + noise;

  // Soft clamp — discourage exceeding bounds but don't hard-clip
  if (state < mn) state = mn + fabs(state - mn) * 0.1f;
  if (state > mx) state = mx - fabs(state - mx) * 0.1f;
  return state;
}

float genValue(const char* behavior, float mn, float mx, float progress, float& meanRevertState) {
  if (strcmp(behavior, "oscillating_stable") == 0) return genOscillatingStable(mn, mx);
  if (strcmp(behavior, "monotonic_rising") == 0)   return genMonotonicRising(mn, mx, progress);
  if (strcmp(behavior, "monotonic_falling") == 0)  return genMonotonicFalling(mn, mx, progress);
  if (strcmp(behavior, "mean_reverting") == 0)     return genMeanReverting(mn, mx, meanRevertState);
  return (mn + mx) / 2.0f;  // unreachable for this scenario — demo-safe fallback
}

// ─── Condition classification (ported from condition_mapper.classify(), v1: ──
// no critical tier, no warning/critical fraction scaling — see handoff doc) ───
const char* classifyHeartRate(float v) {
  if (v >= THRESH_HR_HIGH || v <= THRESH_HR_LOW) return "warning";
  return "normal";
}

const char* classifySpo2(float v) {
  if (v <= THRESH_SPO2_LOW) return "warning";
  return "normal";
}

const char* classifyHrv(float v) {
  if (v <= THRESH_HRV_LOW) return "warning";
  return "normal";
}

// Matches BatterySensor.tick()'s classification exactly.
const char* classifyBattery(float v) {
  if (v > 20.0f) return "normal";
  if (v > 10.0f) return "warning";
  return "critical";
}

// ─── MQTT publish ──────────────────────────────────────────────────────────────
void publishSensor(const char* sensorName, float value, const char* unit,
                    const char* phase, const char* condition, long seq, const char* ts) {
  if (!mqtt.connected()) return;

  StaticJsonDocument<512> doc;
  doc["timestamp_utc"]   = ts;
  doc["device_id"]       = DEVICE_ID;
  doc["patient_label"]   = patientLabel;
  doc["persona_id"]      = PERSONA_ID;
  doc["poc_type"]        = POC_TYPE;
  doc["sensor_name"]     = sensorName;
  doc["value"]           = value;
  doc["unit"]            = unit;
  doc["phase"]           = phase;
  doc["condition"]       = condition;
  doc["quality"]         = "good";
  doc["fault_active"]    = false;
  doc["sequence_number"] = seq;
  doc["compression"]     = (int)COMPRESSION;

  char payload[640];  // patient_label is now operator-set and variable-length — leave headroom
  serializeJson(doc, payload);

  char topic[80];
  snprintf(topic, sizeof(topic), "%s/%s/%s/%s", MQTT_PREFIX, POC_TYPE, PERSONA_ID, sensorName);
  mqtt.publish(topic, payload);
}

// ─── Scenario clock + tick ─────────────────────────────────────────────────────
void publishScenarioTick() {
  unsigned long nowMs = millis();
  float realElapsedSec = (nowMs - simStartMs) / 1000.0f;
  float simMinutes = realElapsedSec * COMPRESSION / 60.0f;

  if (simMinutes >= TOTAL_SIM_MINUTES) {
    // Loop back to resting_normal so a device left running just repeats
    // the story — no reset needed for a bench test or demo.
    simStartMs = nowMs;
    simMinutes = 0.0f;
    currentPhaseIdx = -1;  // force phase-change handling below
    Serial.println("[LOOP] scenario restarting from resting_normal");
  }

  int phaseIdx = PHASE_COUNT - 1;
  for (int i = 0; i < PHASE_COUNT; i++) {
    if (simMinutes >= PHASES[i].startMin && simMinutes < PHASES[i].endMin) {
      phaseIdx = i;
      break;
    }
  }

  if (phaseIdx != currentPhaseIdx) {
    currentPhaseIdx = phaseIdx;
    hrMeanRevertCurrent   = NAN;
    spo2MeanRevertCurrent = NAN;
    hrvMeanRevertCurrent  = NAN;
    Serial.printf("[PHASE] -> %s\n", PHASES[phaseIdx].name);
  }

  Phase& p = PHASES[phaseIdx];
  float progress = (p.endMin - p.startMin) > 0
    ? (simMinutes - p.startMin) / (p.endMin - p.startMin)
    : 0.0f;
  if (progress < 0.0f) progress = 0.0f;
  if (progress > 1.0f) progress = 1.0f;

  float hr       = genValue(p.hrBehavior, p.hrMin, p.hrMax, progress, hrMeanRevertCurrent);
  float spo2Raw  = genValue(p.spo2Behavior, p.spo2Min, p.spo2Max, progress, spo2MeanRevertCurrent);
  float spo2     = roundf(spo2Raw);  // quantization noise model: round to whole %
  float hrv      = genValue(p.hrvBehavior, p.hrvMin, p.hrvMax, progress, hrvMeanRevertCurrent);

  const char* hrCondition   = classifyHeartRate(hr);
  const char* spo2Condition = classifySpo2(spo2);
  const char* hrvCondition  = classifyHrv(hrv);

  char ts[40];
  isoTimestamp(ts, sizeof(ts));

  publishSensor("heart_rate",              hr,   "bpm",     p.name, hrCondition,   ++hrSeq,   ts);
  publishSensor("spo2",                    spo2, "percent", p.name, spo2Condition, ++spo2Seq, ts);
  publishSensor("heart_rate_variability",  hrv,  "ms",      p.name, hrvCondition,  ++hrvSeq,  ts);

  if (nowMs - lastBatteryPublishMs >= BATTERY_PUBLISH_INTERVAL_MS) {
    lastBatteryPublishMs = nowMs;
    float elapsedS = (nowMs - lastBatteryTickMs) / 1000.0f;
    lastBatteryTickMs = nowMs;
    batteryLevel = max(BATTERY_FLOOR, batteryLevel - BATTERY_DRAIN_RATE_PER_SEC * elapsedS);
    publishSensor("battery", batteryLevel, "%", p.name, classifyBattery(batteryLevel), ++batterySeq, ts);
  }

  // RSSI logged every tick (cheap) so a reported disconnect can be checked
  // against signal strength; the heavier reading line is throttled since
  // it's the main suspect for stalling this board's native-USB-CDC Serial.
  tickCounter++;
  Serial.printf("RSSI=%ddBm\n", WiFi.RSSI());
  if (tickCounter % VERBOSE_PRINT_EVERY_N_TICKS == 0) {
    Serial.printf("[%s] HR=%.1f (%s)  SpO2=%.0f (%s)  HRV=%.1f (%s)\n",
      p.name, hr, hrCondition, spo2, spo2Condition, hrv, hrvCondition);
  }
}

// ─── Setup / loop ──────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("\n=== Cardiac Patient Simulator Booting ===");

  randomSeed(esp_random());

  snprintf(configTopic, sizeof(configTopic), "config/%s/patient_label", DEVICE_ID);

  connectWiFi();
  syncTime();

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(640);  // must be >= the payload[] buffer in publishSensor()
  mqtt.setKeepAlive(30);

  simStartMs = millis();
  Serial.println("=== Cardiac Patient Simulator Ready ===\n");
}

void loop() {
  reconnectWiFi();
  reconnectMQTT();
  mqtt.loop();

  unsigned long now = millis();
  if (now - lastPublishMs >= PUBLISH_INTERVAL_MS) {
    lastPublishMs = now;
    publishScenarioTick();
  }
}
